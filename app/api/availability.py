from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_connection

router = APIRouter(
    prefix="/availability",
    tags=["Availability"],
)


APP_TIMEZONE = ZoneInfo("Asia/Kolkata")


class AvailabilityRequest(BaseModel):
    doctor_id: int
    appointment_type_id: int
    date: date


def overlaps(
    start_at: datetime,
    end_at: datetime,
    existing_start: datetime,
    existing_end: datetime,
) -> bool:
    return start_at < existing_end and end_at > existing_start


@router.post("")
def get_available_slots(request: AvailabilityRequest):
    doctor_id = request.doctor_id
    appointment_type_id = request.appointment_type_id
    requested_date = request.date

    day_of_week = requested_date.weekday() + 1

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

            if cur.fetchone() is None:
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

            # Get doctor's schedule
            cur.execute(
                """
                SELECT
                    start_time,
                    end_time
                FROM doctor_schedule
                WHERE doctor_id = %s
                  AND day_of_week = %s
                  AND active = TRUE
                ORDER BY start_time
                """,
                (
                    doctor_id,
                    day_of_week,
                ),
            )

            schedules = cur.fetchall()

            if not schedules:
                return {
                    "doctor_id": doctor_id,
                    "appointment_type_id": appointment_type_id,
                    "date": requested_date.isoformat(),
                    "duration_minutes": duration_minutes,
                    "slots": [],
                }

            # Create timezone-aware local day boundaries.
            day_start = datetime.combine(
                requested_date,
                time.min,
                tzinfo=APP_TIMEZONE,
            )

            day_end = day_start + timedelta(days=1)

            # Get active doctor blocks.
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
                ORDER BY start_at
                """,
                (
                    doctor_id,
                    day_end,
                    day_start,
                ),
            )

            blocks = cur.fetchall()

            # Get existing appointments.
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
                    day_end,
                    day_start,
                ),
            )

            appointments = cur.fetchall()

    slots = []

    for schedule_start, schedule_end in schedules:

        # Schedule times are local doctor times.
        current_start = datetime.combine(
            requested_date,
            schedule_start,
            tzinfo=APP_TIMEZONE,
        )

        schedule_end_at = datetime.combine(
            requested_date,
            schedule_end,
            tzinfo=APP_TIMEZONE,
        )

        while (
            current_start + timedelta(minutes=duration_minutes)
            <= schedule_end_at
        ):

            current_end = current_start + timedelta(
                minutes=duration_minutes
            )

            slot_available = True

            # Check doctor blocks.
            for block_start, block_end in blocks:

                if overlaps(
                    current_start,
                    current_end,
                    block_start,
                    block_end,
                ):
                    slot_available = False
                    break

            # Check existing appointments.
            if slot_available:

                for appointment_start, appointment_end in appointments:

                    if overlaps(
                        current_start,
                        current_end,
                        appointment_start,
                        appointment_end,
                    ):
                        slot_available = False
                        break

            if slot_available:
                slots.append(
                    {
                        "start_at": current_start.isoformat(),
                        "end_at": current_end.isoformat(),
                    }
                )

            current_start = current_end

    return {
        "doctor_id": doctor_id,
        "appointment_type_id": appointment_type_id,
        "date": requested_date.isoformat(),
        "duration_minutes": duration_minutes,
        "slots": slots,
    }