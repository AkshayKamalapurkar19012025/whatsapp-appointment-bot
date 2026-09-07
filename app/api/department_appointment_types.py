from fastapi import APIRouter, HTTPException

from app.db.connection import get_connection
from app.services.availability_engine import get_appointment_types_for_department

router = APIRouter(
    prefix="/departments",
    tags=["Department Appointment Types"],
)


@router.get("/{department_id}/appointment-types")
def get_department_appointment_types(department_id: int):
    """
    Every appointment type offered by at least one active doctor in this
    department -- for the Date-First web flow, which needs an
    Appointment Type step before any doctor is chosen (unlike the
    existing GET /doctors/{doctor_id}/appointment-types, which requires
    a doctor up front). Thin wrapper around
    app.services.availability_engine.get_appointment_types_for_department
    -- the exact same function app/api/scheduling.py's WhatsApp Date-First
    flow calls, so both channels see identical results.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM departments
                WHERE id = %s
                  AND active = TRUE
                """,
                (department_id,),
            )

            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="Department not found",
                )

            appointment_types = get_appointment_types_for_department(cur, department_id)

    return appointment_types
