from datetime import datetime, timedelta
import logging

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_connection
from app.utils.timezone import overlaps

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
            # 1. Check doctor.
            #
            # Deliberately a plain SELECT, not FOR UPDATE. It used to be
            # FOR UPDATE (the theory being that locking the doctors row
            # would serialize appointment creation for that doctor) --
            # that was removed while adding the pg_advisory_xact_lock
            # below, for two reasons: (a) it never actually serialized
            # against the WhatsApp path anyway, since booking.py doesn't
            # take that same lock (this is exactly the cross-path gap
            # documented in docs/DATABASE_P1_NOTES.md item 4), and
            # (b) worse, it actively caused a real, reproduced deadlock
            # once both paths used pg_advisory_xact_lock: a transaction
            # holding FOR UPDATE on the doctors row while waiting for the
            # advisory lock, racing against a transaction holding the
            # advisory lock while waiting to INSERT (which needs an
            # implicit FOR KEY SHARE lock on that same doctors row via
            # the appointments.doctor_id foreign key) -- a circular wait.
            # Both paths must acquire locks in the same order; the
            # advisory lock below is that one, shared order.
            # ---------------------------------------------------------
            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
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
            # 6. Serialize booking attempts for this doctor.
            #
            # Standardized on the same primitive app/api/booking.py's
            # WhatsApp flow uses: pg_advisory_xact_lock(doctor_id). This
            # is a session/transaction-scoped Postgres advisory lock,
            # released automatically on commit or rollback -- it is NOT
            # the same lock as the "doctors ... FOR UPDATE" row lock
            # taken in step 1 above (that lock only blocks other
            # transactions that also do a FOR UPDATE read of the same
            # doctors row; it does not block a transaction that only
            # takes this advisory lock, or vice versa). Both booking
            # paths must take the *same* lock, on the *same* key
            # (doctor_id, cast to bigint for pg_advisory_xact_lock's
            # signature), or a WhatsApp booking and a direct REST booking
            # for the same doctor/slot can both pass their overlap check
            # and both insert -- this was confirmed to happen in testing
            # before this change (see docs/DATABASE_P1_NOTES.md item 4).
            #
            # Position matters: acquired after the doctor-block check
            # above (matching app/api/booking.py's CONFIRM_BOOKING flow
            # exactly) and before the final existing-appointments
            # overlap re-check below, held until this transaction
            # commits or rolls back (i.e. through the INSERT).
            # ---------------------------------------------------------
            cur.execute(
                "SELECT pg_advisory_xact_lock(%s::bigint)",
                (doctor_id,),
            )

            # ---------------------------------------------------------
            # 7. Re-check existing appointments now that the lock for
            # this doctor is held -- no other transaction can be
            # concurrently inserting/re-checking for this doctor_id.
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
            # 8. Create appointment.
            #
            # A second line of defense sits below this INSERT: a
            # database-level EXCLUDE constraint on (doctor_id, time
            # range) for non-cancelled appointments (see
            # migrations/0003_prevent_overlapping_bookings.sql). The
            # advisory lock above should make it impossible for two
            # concurrent requests to both reach this INSERT for an
            # overlapping slot; the constraint is what guarantees that
            # even if some future code path ever bypassed the lock.
            # psycopg.errors.ExclusionViolation from this statement is
            # handled below.
            # ---------------------------------------------------------
            try:
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
            except psycopg.errors.ExclusionViolation:
                logger.warning(
                    f"Exclusion constraint rejected overlapping booking for "
                    f"doctor_id={doctor_id} (advisory lock should normally "
                    f"prevent reaching this point -- backstop triggered)"
                )
                raise HTTPException(
                    status_code=409,
                    detail="Appointment overlaps with existing appointment",
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