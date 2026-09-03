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

from datetime import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.appointment_services import (
    create_appointment_service,
    cancel_appointment_service,
    reschedule_appointment_service,
)
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


class AppointmentReschedule(BaseModel):
    new_start_at: datetime


@router.get("")
def get_appointments(
    doctor_id: int | None = None,
    patient_id: int | None = None,
    status: str | None = None,
    staff: dict = Depends(get_current_staff),
):
    """
    The admin appointment dashboard's listing, per
    docs/WEB_EXPANSION_ARCHITECTURE.md section 4 ("existing
    appointments.py GET can likely be reused/extended with query
    filters rather than duplicated") -- all filters optional, so the
    unfiltered call still returns everything, matching this endpoint's
    pre-P9 behavior exactly.

    Deliberately no date-range filter: appointments in this list can
    span doctors in different timezones, so "give me appointments on
    date X" has no single unambiguous meaning at the SQL level (X in
    which doctor's local calendar day?) without doing the same explicit
    per-row timezone conversion this endpoint's own display already
    needs (see below) -- worth a look if a later phase's admin UI
    actually needs date filtering, but not invented speculatively here.

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
                    a.status
                FROM appointments a
                JOIN doctors d
                    ON d.id = a.doctor_id
                JOIN patients p
                    ON p.id = a.patient_id
                JOIN appointment_types at
                    ON at.id = a.appointment_type_id
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
                "start_at": convert_to_timezone(row[9], doctor_tz).isoformat(),
                "end_at": convert_to_timezone(row[10], doctor_tz).isoformat(),
                "status": row[11],
            }
        )

    return results


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
                    # enforce_booking_window intentionally left at its
                    # default (False) -- staff/admin creating an
                    # appointment on a patient's behalf is not subject
                    # to the patient-facing current+3-month booking
                    # window (e.g. recording a past visit, or booking
                    # further out than a self-service patient could).
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
                    detail="Appointment is already cancelled",
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
    app/api/patient_booking.py's web endpoint and app/api/booking.py's
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
                    # enforce_booking_window intentionally left at its
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
