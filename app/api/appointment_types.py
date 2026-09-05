from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
import psycopg

from app.api.staff_auth import require_role
from app.db.connection import get_connection

router = APIRouter(
    prefix="/appointment-types",
    tags=["Appointment Types"],
)


class AppointmentTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Appointment type name cannot be empty")

        return value


@router.get("")
def get_appointment_types():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, active
                FROM appointment_types
                WHERE active = TRUE
                ORDER BY name
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "active": row[2],
        }
        for row in rows
    ]


@router.post("")
def create_appointment_type(
    appointment_type: AppointmentTypeCreate,
    admin: dict = Depends(require_role("ADMIN")),
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO appointment_types (name)
                    VALUES (%s)
                    RETURNING id, name, active
                    """,
                    (appointment_type.name,),
                )
                row = cur.fetchone()

        return {
            "id": row[0],
            "name": row[1],
            "active": row[2],
        }

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="Appointment type already exists",
        )


@router.put("/{appointment_type_id}")
def update_appointment_type(
    appointment_type_id: int,
    appointment_type: AppointmentTypeCreate,
    admin: dict = Depends(require_role("ADMIN")),
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE appointment_types
                    SET name = %s,
                        updated_at = NOW()
                    WHERE id = %s
                      AND active = TRUE
                    RETURNING id, name, active
                    """,
                    (appointment_type.name, appointment_type_id),
                )
                row = cur.fetchone()

                if row is None:
                    raise HTTPException(status_code=404, detail="Appointment type not found")

        return {
            "id": row[0],
            "name": row[1],
            "active": row[2],
        }

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="Appointment type already exists",
        )


@router.delete("/{appointment_type_id}")
def delete_appointment_type(
    appointment_type_id: int,
    admin: dict = Depends(require_role("ADMIN")),
):
    # Soft delete only, same as departments -- appointment_types is
    # referenced by doctor_appointment_types and appointments.appointment_
    # type_id, so a hard DELETE would either fail on the FK or silently
    # orphan rows. Every listing endpoint already filters on active = TRUE.
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE appointment_types
                SET active = FALSE,
                    updated_at = NOW()
                WHERE id = %s
                  AND active = TRUE
                RETURNING id
                """,
                (appointment_type_id,),
            )

            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Appointment type not found")

    return {
        "id": appointment_type_id,
        "message": "Appointment type removed",
    }