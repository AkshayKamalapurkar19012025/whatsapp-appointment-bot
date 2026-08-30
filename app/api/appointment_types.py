from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
import psycopg

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