from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_connection


router = APIRouter(
    prefix="/appointments",
    tags=["Appointments"],
)


class AppointmentCreate(BaseModel):
    doctor_id: int
    patient_id: int
    appointment_type_id: int
    start_at: datetime


def overlaps(
    start_at: datetime,
    end_at: datetime,
    existing_start: datetime,
    existing_end: datetime,
) -> bool:
    return start_at < existing_end and end_at > existing_start


@router.get("")
def get_appointments():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.id,
                    a.doctor_id,
                    d.name,
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
                ORDER BY a.start_at
                """
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "doctor_id": row[1],
            "doctor_name": row[2],
            "patient_id": row[3],
            "patient_name": row[4],
            "whatsapp_number": row[5],
            "appointment_type_id": row[6],
            "appointment_type_name": row[7],
            "start_at": row[8].isoformat(),
            "end_at": row[9].isoformat(),
            "status": row[10],
        }
        for row in rows
    ]


@router.post("")
def create_appointment(appointment: AppointmentCreate):
    doctor_id = appointment.doctor_id
    patient_id = appointment.patient_id
    appointment_type_id = appointment.appointment_type_id
    start_at = appointment.start_at

    # Normalize seconds/microseconds.
    start_at = start_at.replace(
        second=0,
        microsecond=0,
    )

    with get_connection() as conn:
        with conn.cursor() as cur:

            # ---------------------------------------------------------
            # 1. Check doctor
            # ---------------------------------------------------------
            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                FOR UPDATE
                """,
                (doctor_id,),
            )

            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            # ---------------------------------------------------------
            # 2. Check patient
            # ---------------------------------------------------------
            cur.execute(
                """
                SELECT id
                FROM patients
                WHERE id = %s
                """,
                (patient_id,),
            )

            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="Patient not found",
                )

            # ---------------------------------------------------------
            # 3. Check appointment type assignment
            # ---------------------------------------------------------
            cur.execute(
                """
                SELECT
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

            duration_minutes = appointment_type[0]

            end_at = start_at + timedelta(
                minutes=duration_minutes
            )

            # ---------------------------------------------------------
            # 4. Check doctor's schedule
            # ---------------------------------------------------------
            day_of_week = start_at.weekday() + 1
            start_time = start_at.time()
            end_time = end_at.time()

            cur.execute(
                """
                SELECT id
                FROM doctor_schedule
                WHERE doctor_id = %s
                  AND day_of_week = %s
                  AND active = TRUE
                  AND start_time <= %s
                  AND end_time >= %s
                LIMIT 1
                """,
                (
                    doctor_id,
                    day_of_week,
                    start_time,
                    end_time,
                ),
            )

            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment is outside doctor's working schedule",
                )

            # ---------------------------------------------------------
            # 5. Check doctor blocks
            # ---------------------------------------------------------
            cur.execute(
                """
                SELECT
                    start_at,
                    end_at
                FROM doctor_blocks
                WHERE doctor_id = %s
                  AND active = TRUE
                  AND start_at < %s
                  AND end_at > %s
                FOR UPDATE
                """,
                (
                    doctor_id,
                    end_at,
                    start_at,
                ),
            )

            blocks = cur.fetchall()

            for block_start, block_end in blocks:
                if overlaps(
                    start_at,
                    end_at,
                    block_start,
                    block_end,
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Appointment overlaps with doctor block",
                    )

            # ---------------------------------------------------------
            # 6. Check existing appointments
            #
            # Doctor row is locked above using FOR UPDATE.
            # This serializes appointment creation for the same doctor.
            # ---------------------------------------------------------
            cur.execute(
                """
                SELECT
                    start_at,
                    end_at
                FROM appointments
                WHERE doctor_id = %s
                  AND start_at < %s
                  AND end_at > %s
                  AND status <> 'CANCELLED'
                ORDER BY start_at
                FOR UPDATE
                """,
                (
                    doctor_id,
                    end_at,
                    start_at,
                ),
            )

            existing_appointments = cur.fetchall()

            for existing_start, existing_end in existing_appointments:
                if overlaps(
                    start_at,
                    end_at,
                    existing_start,
                    existing_end,
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Appointment overlaps with existing appointment",
                    )

            # ---------------------------------------------------------
            # 7. Create appointment
            # ---------------------------------------------------------
            cur.execute(
                """
                INSERT INTO appointments (
                    doctor_id,
                    patient_id,
                    appointment_type_id,
                    start_at,
                    end_at,
                    status
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'BOOKED'
                )
                RETURNING
                    id,
                    doctor_id,
                    patient_id,
                    appointment_type_id,
                    start_at,
                    end_at,
                    status
                """,
                (
                    doctor_id,
                    patient_id,
                    appointment_type_id,
                    start_at,
                    end_at,
                ),
            )

            row = cur.fetchone()

    return {
        "id": row[0],
        "doctor_id": row[1],
        "patient_id": row[2],
        "appointment_type_id": row[3],
        "start_at": row[4].isoformat(),
        "end_at": row[5].isoformat(),
        "status": row[6],
        "duration_minutes": duration_minutes,
        "appointment_type_name": appointment_type[1],
    }


@router.delete("/{appointment_id}")
def cancel_appointment(appointment_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id, status
                FROM appointments
                WHERE id = %s
                FOR UPDATE
                """,
                (appointment_id,),
            )

            appointment = cur.fetchone()

            if appointment is None:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment not found",
                )

            if appointment[1] == "CANCELLED":
                raise HTTPException(
                    status_code=409,
                    detail="Appointment is already cancelled",
                )

            cur.execute(
                """
                UPDATE appointments
                SET
                    status = 'CANCELLED',
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, status
                """,
                (appointment_id,),
            )

            row = cur.fetchone()

    return {
        "id": row[0],
        "status": row[1],
        "message": "Appointment cancelled",
    }