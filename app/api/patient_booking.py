"""
Patient-facing web booking API (WEB P3 + P4).

Every endpoint here is a thin wrapper reusing WEB P1/P2's already-built,
already-tested pieces rather than a third implementation of booking
rules (global rule 4):

- GET /web/calendar: wraps availability_engine.list_available_dates_in_range,
  intersected with is_within_booking_window so a date outside the current
  month + 3 window (including a past date) is always reported
  unavailable regardless of what raw slot computation would say. Left
  unauthenticated, matching the existing (also unauthenticated)
  POST /api/availability single-day endpoint's security posture -- this
  is doctor schedule/capacity information, not patient data.

- POST /web/appointments: the booking-creation step (WEB P3). Requires a
  valid patient session (Depends(get_current_patient)) and always uses
  the session's own patient id -- the request body has no patient_id
  field at all, so there is nothing to spoof. Calls
  create_appointment_service(..., enforce_booking_window=True), the
  exact function app/api/appointments.py's REST endpoint and
  app/api/booking.py's WhatsApp flow both call, so this patient-facing
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
"""

from datetime import date, datetime
import calendar as calendar_module

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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
    booking_window,
    is_within_booking_window,
    list_available_dates_in_range,
)

router = APIRouter(
    prefix="/web",
    tags=["Patient Web Booking"],
)


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
):
    if not (1 <= month <= 12):
        raise HTTPException(status_code=422, detail="month must be between 1 and 12")

    _, last_day = calendar_module.monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, last_day)

    window_start, window_end = booking_window()

    if month_end < window_start or month_start > window_end:
        raise HTTPException(
            status_code=409,
            detail="Requested month is outside the allowed booking window",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            raw = list_available_dates_in_range(
                cur,
                doctor_id,
                appointment_type_id,
                month_start,
                month_end,
            )

    dates = {
        iso_date: (is_available and is_within_booking_window(date.fromisoformat(iso_date)))
        for iso_date, is_available in raw.items()
    }

    return {
        "doctor_id": doctor_id,
        "appointment_type_id": appointment_type_id,
        "year": year,
        "month": month,
        "booking_window_start": window_start.isoformat(),
        "booking_window_end": window_end.isoformat(),
        "dates": dates,
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
                    enforce_booking_window=True,
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
            except svc_exc.OutsideBookingWindow:
                raise HTTPException(
                    status_code=409,
                    detail="Requested date is outside the allowed booking window",
                )
            except svc_exc.SlotOverlap:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment overlaps with existing appointment",
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
                raise HTTPException(status_code=409, detail="Appointment is already cancelled")

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
                    enforce_booking_window=True,
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
            except svc_exc.DoctorBlockConflict:
                raise HTTPException(
                    status_code=409,
                    detail="That slot is no longer available because the doctor is unavailable",
                )
            except svc_exc.OutsideBookingWindow:
                raise HTTPException(
                    status_code=409,
                    detail="Requested date is outside the allowed booking window",
                )
            except svc_exc.SlotOverlap:
                raise HTTPException(
                    status_code=409,
                    detail="That slot was just booked by someone else",
                )

    return result
