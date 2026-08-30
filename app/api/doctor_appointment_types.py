from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import psycopg

from app.db.connection import get_connection

router = APIRouter(
    prefix="/doctors",
    tags=["Doctor Appointment Types"],
)


class DoctorAppointmentTypeCreate(BaseModel):
    duration_minutes: int = Field(gt=0, le=480)


@router.get("/{doctor_id}/appointment-types")
def get_doctor_appointment_types(doctor_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    at.id,
                    at.name,
                    dat.duration_minutes,
                    dat.active
                FROM doctor_appointment_types dat
                JOIN appointment_types at
                    ON at.id = dat.appointment_type_id
                WHERE dat.doctor_id = %s
                  AND dat.active = TRUE
                  AND at.active = TRUE
                ORDER BY at.name
                """,
                (doctor_id,),
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "duration_minutes": row[2],
            "active": row[3],
        }
        for row in rows
    ]


@router.post("/{doctor_id}/appointment-types/{appointment_type_id}")
def assign_appointment_type_to_doctor(
    doctor_id: int,
    appointment_type_id: int,
    appointment_type: DoctorAppointmentTypeCreate,
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                """,
                (doctor_id,),
            )

            doctor = cur.fetchone()

            if doctor is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            cur.execute(
                """
                SELECT id, name
                FROM appointment_types
                WHERE id = %s
                  AND active = TRUE
                """,
                (appointment_type_id,),
            )

            appointment_type_row = cur.fetchone()

            if appointment_type_row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment type not found",
                )

            try:
                cur.execute(
                    """
                    INSERT INTO doctor_appointment_types (
                        doctor_id,
                        appointment_type_id,
                        duration_minutes
                    )
                    VALUES (%s, %s, %s)
                    RETURNING doctor_id,
                              appointment_type_id,
                              duration_minutes,
                              active
                    """,
                    (
                        doctor_id,
                        appointment_type_id,
                        appointment_type.duration_minutes,
                    ),
                )

                row = cur.fetchone()

            except psycopg.errors.UniqueViolation:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment type already assigned to doctor",
                )

    return {
        "doctor_id": row[0],
        "appointment_type_id": row[1],
        "duration_minutes": row[2],
        "active": row[3],
        "appointment_type_name": appointment_type_row[1],
    }


@router.put("/{doctor_id}/appointment-types/{appointment_type_id}")
def update_appointment_type_duration(
    doctor_id: int,
    appointment_type_id: int,
    appointment_type: DoctorAppointmentTypeCreate,
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                """,
                (doctor_id,),
            )

            doctor = cur.fetchone()

            if doctor is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            cur.execute(
                """
                SELECT id, name
                FROM appointment_types
                WHERE id = %s
                  AND active = TRUE
                """,
                (appointment_type_id,),
            )

            appointment_type_row = cur.fetchone()

            if appointment_type_row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment type not found",
                )

            cur.execute(
                """
                UPDATE doctor_appointment_types
                SET duration_minutes = %s,
                    active = TRUE
                WHERE doctor_id = %s
                  AND appointment_type_id = %s
                RETURNING doctor_id,
                          appointment_type_id,
                          duration_minutes,
                          active
                """,
                (
                    appointment_type.duration_minutes,
                    doctor_id,
                    appointment_type_id,
                ),
            )

            row = cur.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment type is not assigned to doctor",
                )

    return {
        "doctor_id": row[0],
        "appointment_type_id": row[1],
        "duration_minutes": row[2],
        "active": row[3],
        "appointment_type_name": appointment_type_row[1],
    }


@router.delete("/{doctor_id}/appointment-types/{appointment_type_id}")
def remove_appointment_type_from_doctor(
    doctor_id: int,
    appointment_type_id: int,
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                """,
                (doctor_id,),
            )

            doctor = cur.fetchone()

            if doctor is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            cur.execute(
                """
                UPDATE doctor_appointment_types
                SET active = FALSE
                WHERE doctor_id = %s
                  AND appointment_type_id = %s
                  AND active = TRUE
                RETURNING doctor_id, appointment_type_id
                """,
                (doctor_id, appointment_type_id),
            )

            row = cur.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment type is not assigned to doctor",
                )

    return {
        "doctor_id": row[0],
        "appointment_type_id": row[1],
        "message": "Appointment type removed from doctor",
    }