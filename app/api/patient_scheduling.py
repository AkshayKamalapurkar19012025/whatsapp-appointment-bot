"""
Patient-facing web scheduling API (WEB P3 + P4 + P8).

Every endpoint here is a thin wrapper reusing WEB P1/P2's already-built,
already-tested pieces rather than a third implementation of scheduling
rules (global rule 4):

- GET /web/calendar: wraps availability_engine.list_available_dates_in_range,
  intersected with is_within_scheduling_window so a date outside the current
  month + 3 window (including a past date) is always reported
  unavailable regardless of what raw slot computation would say. Left
  unauthenticated, matching the existing (also unauthenticated)
  POST /api/availability single-day endpoint's security posture -- this
  is doctor schedule/capacity information, not patient data.

- POST /web/appointments: the scheduling-creation step (WEB P3). Requires a
  valid patient session (Depends(get_current_patient)) and always uses
  the session's own patient id -- the request body has no patient_id
  field at all, so there is nothing to spoof. Calls
  create_appointment_service(..., enforce_scheduling_window=True), the
  exact function app/api/appointments.py's REST endpoint and
  app/api/scheduling.py's WhatsApp flow both call, so this patient-facing
  path gets the identical concurrency guarantees (advisory lock +
  EXCLUDE constraint) for free.

- GET /web/appointments/me, DELETE /web/appointments/{id}, and
  POST /web/appointments/{id}/reschedule (WEB P4): "My Appointments" --
  list, cancel, reschedule. All three require a valid patient session
  and always act on that session's own patient id. Cancel/reschedule
  call cancel_appointment_service/reschedule_appointment_service with
  that id as requesting_patient_id/patient_id -- this is what actually
  closes the pre-existing DELETE /api/appointments/{id} ownership gap
  for the web surface (see app/services/appointment_services.py's
  module docstring and the WEB P1 report for why it couldn't be closed
  before patient identity existed). Both NotAppointmentOwner and
  AppointmentNotFound map to the same 404 in the responses below,
  deliberately -- a non-owner must not be able to tell "not yours" from
  "doesn't exist" by the response they get.

Department/doctor/appointment-type browsing deliberately reuse the
existing GET /api/departments, GET /api/departments/{id}/doctors, and
GET /api/doctors/{id}/appointment-types endpoints directly -- they are
already public, already tested, and not patient-specific, so this file
does not duplicate them under a new prefix.

WEB P8: scheduling creation, cancellation, and reschedule each now also
"send" a mock confirmation notification (app/services/notifications.py)
to the patient's WhatsApp number -- closing the gap the WEB P3
confirmation screen's own placeholder text used to flag ("Mock SMS
delivery is implemented in a later phase"). WhatsApp-originated actions
are deliberately not wired to this -- see notifications.py's module
docstring for why. GET /web/notifications/_dev_lookup is this phase's
sibling to /api/auth/patient/otp/_dev_lookup, for retrieving a mock
notification's content in dev/test.
"""

from datetime import date, datetime
import calendar as calendar_module

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import config
from app.api.patient_auth import get_current_patient
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.appointment_services import (
    create_appointment_service,
    cancel_appointment_service,
    reschedule_appointment_service,
    list_patient_appointments_service,
)
from app.services.availability_engine import (
    scheduling_window,
    is_within_scheduling_window,
    list_available_dates_in_range,
    list_available_dates_for_department,
    list_doctors_with_slots_for_date,
)
from app.services.notifications import (
    KIND_SCHEDULING_CONFIRMATION,
    KIND_CANCELLATION,
    KIND_RESCHEDULE,
    send_mock_notification,
)
from app.utils.timezone import convert_to_timezone, validate_timezone

router = APIRouter(
    prefix="/web",
    tags=["Patient Web Scheduling"],
)


def _format_date_time(dt: datetime) -> tuple[str, str]:
    """Same date/time label style as app/api/scheduling.py's WhatsApp
    messages ("Sat, 04 Dec 2027" / "9:00 AM") -- one consistent voice
    across both channels' notifications."""
    date_label = dt.strftime("%a, %d %b %Y")
    time_label = dt.strftime("%I:%M %p").lstrip("0")
    return date_label, time_label


def _get_doctor_name(cur, doctor_id: int) -> str:
    cur.execute("SELECT name FROM doctors WHERE id = %s", (doctor_id,))
    row = cur.fetchone()
    return row[0] if row else "your doctor"


class WebAppointmentCreate(BaseModel):
    doctor_id: int
    appointment_type_id: int
    start_at: datetime


class WebAppointmentReschedule(BaseModel):
    new_start_at: datetime


@router.get("/calendar")
def get_calendar_month(
    doctor_id: int,
    appointment_type_id: int,
    year: int,
    month: int,
    # Optional (migrations/0010) -- see AvailabilityRequest.department_id
    # in app/api/availability.py for the exact semantics. The web
    # scheduling flow (SchedulingFlow.tsx) already selects department before
    # doctor and passes it here; the reschedule flow has no department
    # in scope and omits it, seeing every active schedule row.
    department_id: int | None = None,
):
    if not (1 <= month <= 12):
        raise HTTPException(status_code=422, detail="month must be between 1 and 12")

    _, last_day = calendar_module.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, last_day)

    window_start, window_end = scheduling_window()

    if month_end < window_start or month_start > window_end:
        raise HTTPException(
            status_code=409,
            detail="Requested month is outside the allowed scheduling window",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            raw = list_available_dates_in_range(
                cur,
                doctor_id,
                appointment_type_id,
                month_start,
                month_end,
                department_id=department_id,
            )

    dates = {
        iso_date: (is_available and is_within_scheduling_window(date.fromisoformat(iso_date)))
        for iso_date, is_available in raw.items()
    }

    return {
        "doctor_id": doctor_id,
        "appointment_type_id": appointment_type_id,
        "year": year,
        "month": month,
        "scheduling_window_start": window_start.isoformat(),
        "scheduling_window_end": window_end.isoformat(),
        "dates": dates,
    }


@router.get("/calendar/department")
def get_department_calendar_month(
    department_id: int,
    appointment_type_id: int,
    year: int,
    month: int,
):
    """
    Date-First's aggregate calendar: same window enforcement and same
    per-day-boolean response shape as GET /web/calendar above, but a date
    is available if ANY doctor in the department offering this
    appointment type has a real slot on it (list_available_dates_for_
    department -- department_id is applied per doctor, same as GET
    /web/calendar already does for a single doctor's own department-
    scoped schedule rows, see migrations/0010). No doctor_id is known
    yet at this step, by definition.
    """
    if not (1 <= month <= 12):
        raise HTTPException(status_code=422, detail="month must be between 1 and 12")

    _, last_day = calendar_module.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, last_day)

    window_start, window_end = scheduling_window()

    if month_end < window_start or month_start > window_end:
        raise HTTPException(
            status_code=409,
            detail="Requested month is outside the allowed scheduling window",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            raw = list_available_dates_for_department(
                cur,
                department_id,
                appointment_type_id,
                month_start,
                month_end,
            )

    dates = {
        iso_date: (is_available and is_within_scheduling_window(date.fromisoformat(iso_date)))
        for iso_date, is_available in raw.items()
    }

    return {
        "department_id": department_id,
        "appointment_type_id": appointment_type_id,
        "year": year,
        "month": month,
        "scheduling_window_start": window_start.isoformat(),
        "scheduling_window_end": window_end.isoformat(),
        "dates": dates,
    }


@router.get("/availability/by-date")
def get_department_availability_by_date(
    department_id: int,
    appointment_type_id: int,
    selected_date: date,
):
    """
    Date-First's per-date doctor list: every doctor in the department
    offering this appointment type, each with their actual slots and
    that day's total_slots capacity -- unlike the WhatsApp equivalent,
    a doctor with zero valid slots is still included here (include_
    unavailable=True), so the web UI can show them as a disabled
    "Unavailable" card rather than silently omitting them.
    """
    if not is_within_scheduling_window(selected_date):
        raise HTTPException(
            status_code=409,
            detail="Requested date is outside the allowed scheduling window",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            doctors = list_doctors_with_slots_for_date(
                cur,
                department_id,
                appointment_type_id,
                selected_date,
                include_unavailable=True,
            )

    return {
        "department_id": department_id,
        "appointment_type_id": appointment_type_id,
        "date": selected_date.isoformat(),
        "doctors": doctors,
    }


@router.post("/appointments")
def create_web_appointment(
    body: WebAppointmentCreate,
    patient: dict = Depends(get_current_patient),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = create_appointment_service(
                    cur,
                    doctor_id=body.doctor_id,
                    patient_id=patient["id"],
                    appointment_type_id=body.appointment_type_id,
                    start_at=body.start_at,
                    enforce_scheduling_window=True,
                )
            except svc_exc.DoctorNotFound:
                raise HTTPException(status_code=404, detail="Doctor not found")
            except svc_exc.PatientNotFound:
                raise HTTPException(status_code=404, detail="Patient not found")
            except svc_exc.AppointmentTypeNotAssigned:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment type is not assigned to doctor",
                )
            except svc_exc.OutsideDoctorSchedule:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment is outside doctor's working schedule",
                )
            except svc_exc.DoctorBlockConflict:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment overlaps with doctor block",
                )
            except svc_exc.OutsideSchedulingWindow:
                raise HTTPException(
                    status_code=409,
                    detail="Requested date is outside the allowed scheduling window",
                )
            except svc_exc.SlotOverlap:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment overlaps with existing appointment",
                )

            # WEB P8: mock confirmation notification. Uses body.start_at
            # (the doctor-local instant the client actually requested)
            # rather than result["start_at"] (round-tripped through
            # Postgres and UTC-normalized on read-back -- the same
            # characteristic documented at SchedulingFlow.tsx's confirmation
            # screen since WEB P3) so the message shows the right
            # wall-clock time.
            doctor_name = _get_doctor_name(cur, body.doctor_id)
            date_label, time_label = _format_date_time(body.start_at)
            send_mock_notification(
                cur,
                patient["whatsapp_number"],
                KIND_SCHEDULING_CONFIRMATION,
                (
                    f"Your appointment request with {doctor_name} on {date_label} at "
                    f"{time_label} has been received and is awaiting confirmation."
                ),
            )

    return result


@router.get("/appointments/me")
def get_my_appointments(patient: dict = Depends(get_current_patient)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_patient_appointments_service(cur, patient["id"])


@router.delete("/appointments/{appointment_id}")
def cancel_web_appointment(
    appointment_id: int,
    patient: dict = Depends(get_current_patient),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = cancel_appointment_service(
                    cur,
                    appointment_id,
                    requesting_patient_id=patient["id"],
                )
            except (svc_exc.AppointmentNotFound, svc_exc.NotAppointmentOwner):
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.AlreadyCancelled:
                raise HTTPException(status_code=409, detail="Appointment can no longer be cancelled")

            # WEB P8: mock cancellation notification. cancel_appointment_
            # service's own return doesn't carry display fields (doctor
            # name, appointment time), so those are read back here
            # separately -- and, like get_upcoming_scheduled_appointments
            # and list_patient_appointments_service before it, start_at
            # needs an explicit convert_to_timezone() since a value read
            # back from Postgres is UTC-normalized, not the doctor's
            # local time.
            cur.execute(
                """
                SELECT d.name, d.timezone, a.start_at
                FROM appointments a
                JOIN doctors d ON d.id = a.doctor_id
                WHERE a.id = %s
                """,
                (appointment_id,),
            )
            doctor_name, doctor_tz, start_at = cur.fetchone()
            if not validate_timezone(doctor_tz):
                doctor_tz = "Asia/Kolkata"
            date_label, time_label = _format_date_time(
                convert_to_timezone(start_at, doctor_tz)
            )
            send_mock_notification(
                cur,
                patient["whatsapp_number"],
                KIND_CANCELLATION,
                (
                    f"Your appointment with {doctor_name} on {date_label} at "
                    f"{time_label} has been cancelled."
                ),
            )

    return {
        "id": result["id"],
        "status": result["status"],
        "message": "Appointment cancelled",
    }


@router.post("/appointments/{appointment_id}/reschedule")
def reschedule_web_appointment(
    appointment_id: int,
    body: WebAppointmentReschedule,
    patient: dict = Depends(get_current_patient),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = reschedule_appointment_service(
                    cur,
                    appointment_id,
                    patient_id=patient["id"],
                    new_start_at=body.new_start_at,
                    enforce_scheduling_window=True,
                )
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.AlreadyCancelled:
                raise HTTPException(
                    status_code=409,
                    detail="That appointment is no longer available to reschedule",
                )
            except svc_exc.AppointmentTypeNotAssigned:
                raise HTTPException(
                    status_code=409,
                    detail="The selected appointment type is no longer available",
                )
            except svc_exc.OutsideDoctorSchedule:
                raise HTTPException(
                    status_code=409,
                    detail="Requested time is outside the doctor's working hours",
                )
            except svc_exc.DoctorBlockConflict:
                raise HTTPException(
                    status_code=409,
                    detail="That slot is no longer available because the doctor is unavailable",
                )
            except svc_exc.OutsideSchedulingWindow:
                raise HTTPException(
                    status_code=409,
                    detail="Requested date is outside the allowed scheduling window",
                )
            except svc_exc.SlotOverlap:
                raise HTTPException(
                    status_code=409,
                    detail="That slot was just scheduled by someone else",
                )

            # WEB P8: mock reschedule notification. Uses body.new_start_at
            # (client-supplied, doctor-local) rather than
            # result["start_at"] for the same UTC-round-trip reason as
            # the scheduling-confirmation notification above.
            doctor_name = _get_doctor_name(cur, result["doctor_id"])
            date_label, time_label = _format_date_time(body.new_start_at)
            send_mock_notification(
                cur,
                patient["whatsapp_number"],
                KIND_RESCHEDULE,
                (
                    f"Your appointment with {doctor_name} has been rescheduled to "
                    f"{date_label} at {time_label}."
                ),
            )

    return result


@router.get("/notifications/_dev_lookup")
def notifications_dev_lookup(whatsapp_number: str, kind: str | None = None):
    """
    Dev/test-only: returns the most recent mock notification sent to a
    number, of the given kind if specified (KIND_SCHEDULING_CONFIRMATION /
    KIND_CANCELLATION / KIND_RESCHEDULE, or 'OTP' -- though the sibling
    /auth/patient/otp/_dev_lookup endpoint is the intended way to
    retrieve those). Same mock-provider outbox as that endpoint (see
    app/services/notifications.py); disabled the same way whenever
    ENVIRONMENT=production, for the same reason (its existence shouldn't
    be revealed in a production deployment).
    """
    if config.ENVIRONMENT == "production":
        raise HTTPException(status_code=404, detail="Not found")

    with get_connection() as conn:
        with conn.cursor() as cur:
            if kind is None:
                cur.execute(
                    """
                    SELECT kind, message_body, created_at
                    FROM mock_sms_outbox
                    WHERE whatsapp_number = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (whatsapp_number,),
                )
            else:
                cur.execute(
                    """
                    SELECT kind, message_body, created_at
                    FROM mock_sms_outbox
                    WHERE whatsapp_number = %s
                      AND kind = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (whatsapp_number, kind),
                )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="No mock notification found for this number")

    return {
        "whatsapp_number": whatsapp_number,
        "kind": row[0],
        "message": row[1],
        "sent_at": row[2].isoformat(),
    }
