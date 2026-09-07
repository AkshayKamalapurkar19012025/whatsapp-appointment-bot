from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_connection
from app.services.availability_engine import get_available_slots as compute_available_slots

router = APIRouter(
    prefix="/availability",
    tags=["Availability"],
)


class AvailabilityRequest(BaseModel):
    doctor_id: int
    appointment_type_id: int
    date: date
    # Optional (migrations/0010): when the caller already knows which
    # department this slot lookup is for (the patient web/WhatsApp
    # flows both select department before doctor), passing it narrows
    # results to that department's schedule rows plus any department-
    # agnostic ones. Omitted entirely by admin scheduling/reschedule and
    # the patient reschedule flow, which have no department in scope
    # and so see every active row regardless of department -- see
    # get_available_slots's docstring for the exact semantics.
    department_id: int | None = None


@router.post("")
def get_available_slots(request: AvailabilityRequest):
    """
    REST wrapper around app.services.availability_engine.get_available_slots
    (moved there, along with scheduling.py's identical logic, in the WEB P1
    phase). This endpoint keeps its own doctor/appointment-type 404 checks
    and response shape; slot computation itself is delegated so this stays
    the exact same engine WhatsApp uses.

    Behavior note: the shared engine handles overnight schedules
    (schedule_end <= schedule_start), which this endpoint's previous
    inline copy did not. No existing test exercised that case for this
    endpoint, so this is a deliberate, documented fix from consolidating
    onto one implementation, not an intended behavior change.
    """
    doctor_id = request.doctor_id
    appointment_type_id = request.appointment_type_id
    requested_date = request.date

    with get_connection() as conn:
        with conn.cursor() as cur:

            # Check doctor
            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                """,
                (doctor_id,),
            )

            doctor_row = cur.fetchone()

            if doctor_row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            # Get appointment type assigned to doctor
            cur.execute(
                """
                SELECT
                    dat.appointment_type_id,
                    dat.duration_minutes,
                    at.name
                FROM doctor_appointment_types dat
                JOIN appointment_types at
                    ON at.id = dat.appointment_type_id
                WHERE dat.doctor_id = %s
                  AND dat.appointment_type_id = %s
                  AND dat.active = TRUE
                  AND at.active = TRUE
                """,
                (
                    doctor_id,
                    appointment_type_id,
                ),
            )

            appointment_type = cur.fetchone()

            if appointment_type is None:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment type is not assigned to doctor",
                )

            duration_minutes = appointment_type[1]

            slots = compute_available_slots(
                cur,
                doctor_id,
                appointment_type_id,
                requested_date,
                department_id=request.department_id,
            )

    return {
        "doctor_id": doctor_id,
        "appointment_type_id": appointment_type_id,
        "date": requested_date.isoformat(),
        "duration_minutes": duration_minutes,
        "slots": slots,
    }