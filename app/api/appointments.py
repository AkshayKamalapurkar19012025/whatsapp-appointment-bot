"""
Appointments (WEB P9): staff/admin appointment management on behalf of a
patient -- list/filter, create, cancel, reschedule. Per the RBAC design
(docs/WEB_EXPANSION_ARCHITECTURE.md section 8, "Create/cancel/reschedule
appointments (on behalf of a patient)"), every endpoint here requires an
authenticated staff session (ADMIN or STAFF -- both, unlike the
doctor/department/schedule CRUD routers WEB P6 gated ADMIN-only).

This was the original, pre-Web-expansion REST path -- unauthenticated
until this phase, deliberately deferred from WEB P6's RBAC pass because
gating it meant updating every one of the handful of existing
concurrency/exclusion-constraint tests that called it directly (see
WEB P6's report). That update is done here, alongside the auth gate
itself, rather than as an unrelated side effect of some other change.

GET's ownership-free reach and POST's caller-supplied patient_id were
flagged as a "gap" in earlier phases only because *anyone* could hit
them. Now that both require a real, audited staff session, the same
behavior is the intended admin capability, not a lingering gap --
nothing about that behavior changed in this phase, only who can reach
it.
"""

import calendar as calendar_module
from datetime import date, datetime
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.staff_auth import get_current_staff, require_role
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.appointment_services import (
    create_appointment_service,
    cancel_appointment_service,
    reschedule_appointment_service,
    confirm_appointment_service,
    reject_appointment_service,
    mark_visited_service,
    mark_arrived_service,
    confirm_and_check_in_service,
    mark_completed_service,
    mark_no_show_service,
    get_consultation_charge_service,
    record_payment_service,
    waive_consultation_fee_service,
    settle_free_visit_service,
    record_refund_service,
    get_invoice_service,
    add_invoice_line_item_service,
)
from app.services.availability_engine import list_available_dates_in_range
from app.services.notifications import KIND_CHECK_IN, KIND_QUEUE_TOKEN, send_mock_notification
from app.utils.timezone import convert_to_timezone, validate_timezone

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/appointments",
    tags=["Appointments"],
)


class AppointmentCreate(BaseModel):
    doctor_id: int
    patient_id: int
    appointment_type_id: int
    start_at: datetime
    # Optional -- appointments created before this field existed, and
    # any caller that omits it, stay NULL rather than a fabricated
    # guess (see migrations/0023). Patient web/WhatsApp booking always
    # passes 'ONLINE'; the admin Book Appointment page passes whichever
    # of the 4 pills staff picked.
    booking_source: str | None = None

    @field_validator("booking_source")
    @classmethod
    def validate_booking_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("ONLINE", "PHONE", "WALK_IN", "STAFF_ASSISTED"):
            raise ValueError("booking_source must be one of ONLINE, PHONE, WALK_IN, STAFF_ASSISTED")
        return value


class AppointmentReschedule(BaseModel):
    new_start_at: datetime


class PaymentRecord(BaseModel):
    method: str
    outcome: str

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        if value not in ("CASH", "UPI", "CARD", "OTHER"):
            raise ValueError("method must be one of CASH, UPI, CARD, OTHER")
        return value

    @field_validator("outcome")
    @classmethod
    def validate_outcome(cls, value: str) -> str:
        if value not in ("PAID", "FAILED"):
            raise ValueError("outcome must be one of PAID, FAILED")
        return value


class PaymentWaive(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A reason is required to waive the consultation fee")
        return value


class PaymentRefund(BaseModel):
    amount: float = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A reason is required to refund a payment")
        return value


class InvoiceLineItemCreate(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A description is required for an invoice line item")
        return value


@router.get("")
def get_appointments(
    doctor_id: int | None = None,
    patient_id: int | None = None,
    status: str | None = None,
    appointment_type_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    staff: dict = Depends(get_current_staff),
):
    """
    The admin appointment dashboard's listing, per
    docs/WEB_EXPANSION_ARCHITECTURE.md section 4 ("existing
    appointments.py GET can likely be reused/extended with query
    filters rather than duplicated") -- all filters optional, so the
    unfiltered call still returns everything, matching this endpoint's
    pre-P9 behavior exactly.

    date_from/date_to filter on each row's own doctor-local calendar
    date -- the same ambiguity this docstring used to flag ("which
    doctor's day?" when doctors span timezones) is resolved by reusing
    the exact per-row doctor-local conversion this endpoint's display
    already computes below, rather than inventing a second, separate
    notion of "date" at the SQL level. Applied in Python after that
    conversion, not as a WHERE clause, for exactly that reason: the SQL
    layer only knows start_at's UTC instant, not which doctor-local day
    it falls on.

    start_at/end_at are now converted to each row's own doctor's local
    timezone before being returned -- a real, pre-existing display bug
    (a value read back from Postgres is UTC-normalized, not the
    doctor's local time; see the WEB P3/P4/P8 reports for the same
    class of bug found and fixed three times before in patient-facing
    code) that simply hadn't mattered yet, since nothing previously
    displayed this endpoint's output to a human. It does now, as the
    admin dashboard's own data source.
    """
    where_clauses = []
    params: list = []

    if doctor_id is not None:
        where_clauses.append("a.doctor_id = %s")
        params.append(doctor_id)
    if patient_id is not None:
        where_clauses.append("a.patient_id = %s")
        params.append(patient_id)
    if status is not None:
        where_clauses.append("a.status = %s")
        params.append(status)
    if appointment_type_id is not None:
        where_clauses.append("a.appointment_type_id = %s")
        params.append(appointment_type_id)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    a.id,
                    a.doctor_id,
                    d.name,
                    d.timezone,
                    a.patient_id,
                    p.name,
                    p.whatsapp_number,
                    a.appointment_type_id,
                    at.name,
                    a.start_at,
                    a.end_at,
                    a.status,
                    a.token_number,
                    a.created_at,
                    a.payment_status,
                    dat.consultation_fee,
                    a.payment_method,
                    a.payment_amount,
                    a.payment_recorded_at,
                    a.waive_reason,
                    a.arrived_at,
                    a.booking_source
                FROM appointments a
                JOIN doctors d
                    ON d.id = a.doctor_id
                JOIN patients p
                    ON p.id = a.patient_id
                JOIN appointment_types at
                    ON at.id = a.appointment_type_id
                LEFT JOIN doctor_appointment_types dat
                    ON dat.doctor_id = a.doctor_id
                   AND dat.appointment_type_id = a.appointment_type_id
                {where_sql}
                ORDER BY a.start_at
                """,
                params,
            )

            rows = cur.fetchall()

    results = []
    for row in rows:
        doctor_tz = row[3]
        if not validate_timezone(doctor_tz):
            doctor_tz = "Asia/Kolkata"

        local_start_at = convert_to_timezone(row[9], doctor_tz)

        if date_from is not None and local_start_at.date() < date_from:
            continue
        if date_to is not None and local_start_at.date() > date_to:
            continue

        results.append(
            {
                "id": row[0],
                "doctor_id": row[1],
                "doctor_name": row[2],
                "patient_id": row[4],
                "patient_name": row[5],
                "whatsapp_number": row[6],
                "appointment_type_id": row[7],
                "appointment_type_name": row[8],
                "start_at": local_start_at.isoformat(),
                "end_at": convert_to_timezone(row[10], doctor_tz).isoformat(),
                "status": row[11],
                "token_number": row[12],
                # Deliberately NOT converted to doctor_tz like start_at/
                # end_at above -- unlike a clinic wall-clock slot time,
                # this is an audit-log-style "when did this happen"
                # moment (same category as a doctor's created_at in
                # DoctorProfile), which format.ts's formatDateTime
                # renders in the *viewer's* own local time, not the
                # doctor's.
                "created_at": row[13].isoformat(),
                "payment_status": row[14],
                "consultation_fee": row[15],
                "payment_method": row[16],
                "payment_amount": row[17],
                "paid_at": row[18].isoformat() if row[18] else None,
                "waive_reason": row[19],
                # Doctor-local, same convention as start_at/end_at above
                # (a clinic wall-clock moment, not an audit-log one) --
                # this is what the "Arrived early/late" label is
                # computed from on the frontend, alongside start_at.
                "arrived_at": convert_to_timezone(row[20], doctor_tz).isoformat() if row[20] else None,
                "booking_source": row[21],
            }
        )

    return results


@router.get("/calendar")
def get_appointments_calendar(
    doctor_id: int,
    appointment_type_id: int,
    year: int,
    month: int,
    staff: dict = Depends(get_current_staff),
):
    """
    A staff-only month-availability view for the admin scheduling/reschedule
    UI's date picker, so it can show which days actually have open slots
    before staff pick one (colour-coded in the frontend) rather than
    picking blind. Deliberately NOT the same endpoint as GET /web/calendar
    (app/api/patient_booking.py): that one both rejects a request outside
    the patient-facing 3-month scheduling window entirely (409) and marks
    every day beyond it unavailable -- exactly the restriction staff/
    admin schedulings are already exempt from everywhere else in this router
    (see create_appointment's own enforce_scheduling_window=False). Reuses
    list_available_dates_in_range directly, the same underlying function
    the patient endpoint calls, so "is this day open" is computed
    identically either way -- only the window restriction differs.
    """
    if not (1 <= month <= 12):
        raise HTTPException(status_code=422, detail="month must be between 1 and 12")

    _, last_day = calendar_module.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, last_day)

    with get_connection() as conn:
        with conn.cursor() as cur:
            dates = list_available_dates_in_range(
                cur,
                doctor_id,
                appointment_type_id,
                month_start,
                month_end,
            )

    return {
        "doctor_id": doctor_id,
        "appointment_type_id": appointment_type_id,
        "year": year,
        "month": month,
        "dates": dates,
    }


@router.post("")
def create_appointment(
    appointment: AppointmentCreate,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = create_appointment_service(
                    cur,
                    doctor_id=appointment.doctor_id,
                    patient_id=appointment.patient_id,
                    appointment_type_id=appointment.appointment_type_id,
                    start_at=appointment.start_at,
                    # enforce_scheduling_window intentionally left at its
                    # default (False) -- staff/admin creating an
                    # appointment on a patient's behalf is not subject
                    # to the patient-facing current+3-month scheduling
                    # window (e.g. recording a past visit, or scheduling
                    # further out than a self-service patient could).
                    booking_source=appointment.booking_source,
                )
            except svc_exc.DoctorNotFound:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )
            except svc_exc.PatientNotFound:
                raise HTTPException(
                    status_code=404,
                    detail="Patient not found",
                )
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

    return result


@router.delete("/{appointment_id}")
def cancel_appointment(
    appointment_id: int,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = cancel_appointment_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment not found",
                )
            except svc_exc.AlreadyCancelled:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment can no longer be cancelled",
                )

    return {
        "id": result["id"],
        "status": result["status"],
        "message": "Appointment cancelled",
    }


@router.post("/{appointment_id}/reschedule")
def reschedule_appointment(
    appointment_id: int,
    body: AppointmentReschedule,
    staff: dict = Depends(get_current_staff),
):
    """
    Staff/admin reschedule on a patient's behalf. reschedule_appointment_
    service requires patient_id (baked into its ownership-safe lookup
    query -- see its own docstring), which a staff caller doesn't
    inherently know the way an authenticated patient session does, so
    it's looked up here first from the appointment being rescheduled.
    Not a new implementation of reschedule rules -- same shared service
    app/api/patient_booking.py's web endpoint and app/api/scheduling.py's
    WhatsApp flow both call.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT patient_id FROM appointments WHERE id = %s",
                (appointment_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Appointment not found")
            patient_id = row[0]

            try:
                result = reschedule_appointment_service(
                    cur,
                    appointment_id,
                    patient_id=patient_id,
                    new_start_at=body.new_start_at,
                    # enforce_scheduling_window intentionally left at its
                    # default (False) -- same reasoning as create above.
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

    return result


# ---------------------------------------------------------------------
# Lifecycle transitions (migrations/0011_appointment_lifecycle_
# statuses.sql): every appointment starts PENDING (WhatsApp, patient web
# scheduling, and the admin create above all go through the same
# create_appointment_service). Staff move it forward from here -- same
# RBAC as the rest of this router (any authenticated STAFF or ADMIN,
# matching cancel/reschedule above, not require_role("ADMIN")).
# ---------------------------------------------------------------------


@router.post("/{appointment_id}/confirm")
def confirm_appointment(
    appointment_id: int,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = confirm_appointment_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.InvalidStatusTransition:
                raise HTTPException(
                    status_code=409,
                    detail="Only a Pending appointment can be confirmed",
                )
            except svc_exc.AppointmentSlotPassed:
                raise HTTPException(
                    status_code=409,
                    detail="This appointment's scheduled time has already passed and can no longer be confirmed",
                )

    return result


@router.post("/{appointment_id}/reject")
def reject_appointment(
    appointment_id: int,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = reject_appointment_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.InvalidStatusTransition:
                raise HTTPException(
                    status_code=409,
                    detail="Only a Pending appointment can be rejected",
                )

    return result


@router.post("/{appointment_id}/visit")
def visit_appointment(
    appointment_id: int,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = mark_visited_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.InvalidStatusTransition:
                raise HTTPException(
                    status_code=409,
                    detail="Only a Confirmed appointment can be marked Checked In",
                )
            except svc_exc.AppointmentNotStarted:
                raise HTTPException(
                    status_code=409,
                    detail="This appointment has not started yet",
                )

            # Staff-initiated check-in notification (migrations/0012).
            # No token number here any more (Phase 4 decoupled token
            # issuance from check-in -- see mark_visited_service's
            # docstring): this just confirms arrival. The token itself
            # is announced separately, via KIND_QUEUE_TOKEN, once
            # payment succeeds or is waived (record_appointment_payment/
            # waive_appointment_payment below). Not a duplicate of
            # anything: unlike a WhatsApp-driven action, the patient
            # isn't mid-chat with the bot when staff check them in at
            # the front desk, so there's no live confirmation this
            # would repeat (see notifications.py's KIND_CHECK_IN note).
            cur.execute(
                """
                SELECT p.whatsapp_number, p.name, d.name
                FROM patients p, doctors d
                WHERE p.id = %s AND d.id = %s
                """,
                (result["patient_id"], result["doctor_id"]),
            )
            patient_number, patient_name, doctor_name = cur.fetchone()
            send_mock_notification(
                cur,
                patient_number,
                KIND_CHECK_IN,
                f"Hi {patient_name}, you're checked in with {doctor_name}. "
                f"Please complete registration and payment at the front desk.",
            )

    return {
        "id": result["id"],
        "status": result["status"],
        "token_number": result["token_number"],
        "visited_at": result["visited_at"],
    }


@router.post("/{appointment_id}/arrive")
def arrive_appointment(
    appointment_id: int,
    staff: dict = Depends(get_current_staff),
):
    """
    Front desk records that a Confirmed patient has physically shown up
    -- usable any time before their scheduled start_at, most commonly
    for an early arrival (a 2pm appointment whose patient arrives at
    1:30). Deliberately separate from /visit: this never sets
    status=CHECKED_IN and never generates a queue token (see
    mark_arrived_service's docstring) -- once start_at actually
    arrives, staff use the ordinary Check-In action, unchanged.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = mark_arrived_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.InvalidStatusTransition:
                raise HTTPException(
                    status_code=409,
                    detail="Only a Confirmed appointment can be marked arrived",
                )

    return result


@router.post("/{appointment_id}/confirm-and-checkin")
def confirm_and_check_in_appointment(
    appointment_id: int,
    staff: dict = Depends(get_current_staff),
):
    """
    The walk-in "Confirm & Check In" button -- composes confirm/visit/
    arrive (see confirm_and_check_in_service's docstring) into one
    round trip. Never bypasses mark_visited_service's start_at <= now
    guard: if the appointment's slot is still in the future, this ends
    up in the "arrived early" state instead (arrival_kind:
    "arrived_early" in the response) rather than raising an error, so a
    walk-in booked a few minutes out doesn't dead-end the front desk.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = confirm_and_check_in_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.InvalidStatusTransition:
                raise HTTPException(
                    status_code=409,
                    detail="Only a Pending or Confirmed appointment can be confirmed and checked in",
                )

            if result["arrival_kind"] == "checked_in":
                cur.execute(
                    """
                    SELECT p.whatsapp_number, p.name, d.name
                    FROM patients p, doctors d
                    WHERE p.id = %s AND d.id = %s
                    """,
                    (result["patient_id"], result["doctor_id"]),
                )
                patient_number, patient_name, doctor_name = cur.fetchone()
                send_mock_notification(
                    cur,
                    patient_number,
                    KIND_CHECK_IN,
                    f"Hi {patient_name}, you're checked in with {doctor_name}. "
                    f"Please complete registration and payment at the front desk.",
                )

    return result


@router.get("/{appointment_id}/charge")
def get_appointment_charge(
    appointment_id: int,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = get_consultation_charge_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.AppointmentTypeNotAssigned:
                raise HTTPException(
                    status_code=409,
                    detail="This doctor/appointment-type combination no longer has a configured fee",
                )

    return result


@router.get("/{appointment_id}/invoice")
def get_appointment_invoice(
    appointment_id: int,
    staff: dict = Depends(get_current_staff),
):
    """The itemized bill for this appointment -- consultation_fee plus
    any ad-hoc line items, and the total record_payment_service will
    actually charge. Same staff-readable posture as GET .../charge."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = get_invoice_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.AppointmentTypeNotAssigned:
                raise HTTPException(
                    status_code=409,
                    detail="This doctor/appointment-type combination no longer has a configured fee",
                )

    return result


@router.post("/{appointment_id}/invoice/line-items")
def add_appointment_invoice_line_item(
    appointment_id: int,
    line_item: InvoiceLineItemCreate,
    admin: dict = Depends(require_role("ADMIN")),
):
    """ADMIN-only: add an ad-hoc charge (e.g. a dressing charge or a
    minor procedure) to this appointment's bill, on top of its
    consultation_fee. Only possible before the bill is settled -- see
    add_invoice_line_item_service's docstring for why PAID/WAIVED/
    REFUNDED reject this."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = add_invoice_line_item_service(
                    cur,
                    appointment_id,
                    description=line_item.description,
                    amount=line_item.amount,
                    staff_id=admin["id"],
                )
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.PaymentStateConflict:
                raise HTTPException(
                    status_code=409,
                    detail="Line items can only be added before the bill is paid, waived, or refunded",
                )

    return result


def _notify_queue_token(cur, appointment_id: int, token_number: int) -> None:
    """Fires once, exactly when a token is newly issued (record_payment_
    service/waive_consultation_fee_service's token_just_issued flag) --
    the Phase 4 replacement for the old check-in-time token
    announcement (see visit_appointment's own note above)."""
    cur.execute(
        """
        SELECT p.whatsapp_number, p.name, d.name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        JOIN doctors d ON d.id = a.doctor_id
        WHERE a.id = %s
        """,
        (appointment_id,),
    )
    patient_number, patient_name, doctor_name = cur.fetchone()
    send_mock_notification(
        cur,
        patient_number,
        KIND_QUEUE_TOKEN,
        f"Hi {patient_name}, you're checked in successfully. "
        f"Queue Token: {token_number}. Status: Waiting for Doctor "
        f"({doctor_name}).",
    )


@router.post("/{appointment_id}/payment")
def record_appointment_payment(
    appointment_id: int,
    payment: PaymentRecord,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = record_payment_service(
                    cur,
                    appointment_id,
                    method=payment.method,
                    outcome=payment.outcome,
                    staff_id=staff["id"],
                )
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.InvalidStatusTransition:
                raise HTTPException(
                    status_code=409,
                    detail="Payment can only be recorded for a Checked-In appointment",
                )
            except svc_exc.PaymentStateConflict:
                raise HTTPException(
                    status_code=409,
                    detail="This appointment's consultation fee is already waived or refunded",
                )
            except svc_exc.AppointmentTypeNotAssigned:
                raise HTTPException(
                    status_code=409,
                    detail="This doctor/appointment-type combination no longer has a configured fee",
                )

            if result["token_just_issued"]:
                _notify_queue_token(cur, appointment_id, result["token_number"])

    return result


@router.post("/{appointment_id}/waive-payment")
def waive_appointment_payment(
    appointment_id: int,
    waiver: PaymentWaive,
    admin: dict = Depends(require_role("ADMIN")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = waive_consultation_fee_service(
                    cur,
                    appointment_id,
                    reason=waiver.reason,
                    staff_id=admin["id"],
                )
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.InvalidStatusTransition:
                raise HTTPException(
                    status_code=409,
                    detail="The consultation fee can only be waived for a Checked-In appointment",
                )
            except svc_exc.PaymentStateConflict:
                raise HTTPException(
                    status_code=409,
                    detail="This appointment's consultation fee is already paid or refunded",
                )
            except svc_exc.WaiverNotEligible:
                raise HTTPException(
                    status_code=409,
                    detail="Waiver requires a completed visit with this doctor in the last 3 days",
                )

            if result["token_just_issued"]:
                _notify_queue_token(cur, appointment_id, result["token_number"])

    return result


@router.post("/{appointment_id}/settle-free-visit")
def settle_free_appointment_visit(
    appointment_id: int,
    staff: dict = Depends(get_current_staff),
):
    """
    OPD front-desk "Payment Required? No" path -- called by the admin
    booking UI right after check-in when the appointment's own
    configured consultation fee is 0, so a genuinely free visit reaches
    the queue without staff having to press a manual waive button (see
    settle_free_visit_service's docstring for why this is a distinct
    action from record_payment_service/waive_consultation_fee_service,
    not a variant of either). No ADMIN gate, unlike waive-payment: there
    is no discretion here, and the service function itself refuses to
    settle anything with a nonzero fee.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = settle_free_visit_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.InvalidStatusTransition:
                raise HTTPException(
                    status_code=409,
                    detail="A visit can only be settled as free for a Checked-In appointment",
                )
            except svc_exc.PaymentStateConflict:
                raise HTTPException(
                    status_code=409,
                    detail="This appointment's consultation fee is already paid or refunded",
                )
            except svc_exc.FreeVisitNotEligible:
                raise HTTPException(
                    status_code=409,
                    detail="This appointment has a configured consultation fee and cannot be settled as free",
                )
            except svc_exc.AppointmentTypeNotAssigned:
                raise HTTPException(
                    status_code=409,
                    detail="This doctor/appointment-type combination no longer has a configured fee",
                )

            if result["token_just_issued"]:
                _notify_queue_token(cur, appointment_id, result["token_number"])

    return result


@router.post("/{appointment_id}/refund-payment")
def refund_appointment_payment(
    appointment_id: int,
    refund: PaymentRefund,
    admin: dict = Depends(require_role("ADMIN")),
):
    """
    ADMIN-only reversal of a PAID appointment -- back-office correction,
    not a queue-entry action (unlike payment/waive-payment/settle-free-
    visit, this never touches token issuance; see record_refund_
    service's docstring for why it isn't gated on appointment status).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = record_refund_service(
                    cur,
                    appointment_id,
                    amount=refund.amount,
                    reason=refund.reason,
                    staff_id=admin["id"],
                )
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.PaymentStateConflict:
                raise HTTPException(
                    status_code=409,
                    detail="Only a Paid appointment's consultation fee can be refunded",
                )
            except svc_exc.RefundExceedsPayment:
                raise HTTPException(
                    status_code=422,
                    detail="Refund amount cannot exceed the amount actually paid",
                )

    return result


@router.post("/{appointment_id}/complete")
def complete_appointment(
    appointment_id: int,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = mark_completed_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.InvalidStatusTransition:
                raise HTTPException(
                    status_code=409,
                    detail="Only a Checked-In appointment can be marked Completed",
                )

    return result


@router.post("/{appointment_id}/no-show")
def no_show_appointment(
    appointment_id: int,
    staff: dict = Depends(get_current_staff),
):
    """Front-desk marks a Confirmed appointment as a no-show -- manual
    only, no automatic/cron trigger (see mark_no_show_service)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = mark_no_show_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.InvalidStatusTransition:
                raise HTTPException(
                    status_code=409,
                    detail="Only a Confirmed appointment can be marked No-Show",
                )
            except svc_exc.AppointmentNotStarted:
                raise HTTPException(
                    status_code=409,
                    detail="This appointment has not started yet",
                )

    return result
