from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from datetime import time

from app.db.connection import get_connection

router = APIRouter(
    prefix="/doctors",
    tags=["Doctor Schedule"],
)


class DoctorScheduleCreate(BaseModel):
    day_of_week: int = Field(ge=1, le=7)
    start_time: time
    end_time: time

    @field_validator("end_time")
    @classmethod
    def validate_time_range(cls, value: time, info):
        start_time = info.data.get("start_time")

        if start_time is not None and value <= start_time:
            raise ValueError("End time must be after start time")

        return value


def schedule_overlaps(
    cur,
    doctor_id: int,
    day_of_week: int,
    start_time: time,
    end_time: time,
    exclude_schedule_id: int | None = None,
):
    if exclude_schedule_id is None:
        cur.execute(
            """
            SELECT id
            FROM doctor_schedule
            WHERE doctor_id = %s
              AND day_of_week = %s
              AND active = TRUE
              AND start_time < %s
              AND end_time > %s
            """,
            (
                doctor_id,
                day_of_week,
                end_time,
                start_time,
            ),
        )
    else:
        cur.execute(
            """
            SELECT id
            FROM doctor_schedule
            WHERE doctor_id = %s
              AND day_of_week = %s
              AND active = TRUE
              AND id <> %s
              AND start_time < %s
              AND end_time > %s
            """,
            (
                doctor_id,
                day_of_week,
                exclude_schedule_id,
                end_time,
                start_time,
            ),
        )

    return cur.fetchone() is not None


@router.get("/{doctor_id}/schedule")
def get_doctor_schedule(doctor_id: int):
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
                SELECT
                    id,
                    day_of_week,
                    start_time,
                    end_time,
                    active
                FROM doctor_schedule
                WHERE doctor_id = %s
                  AND active = TRUE
                ORDER BY day_of_week, start_time
                """,
                (doctor_id,),
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "day_of_week": row[1],
            "start_time": row[2].strftime("%H:%M"),
            "end_time": row[3].strftime("%H:%M"),
            "active": row[4],
        }
        for row in rows
    ]


@router.post("/{doctor_id}/schedule")
def create_doctor_schedule(
    doctor_id: int,
    schedule: DoctorScheduleCreate,
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

            if schedule_overlaps(
                cur,
                doctor_id,
                schedule.day_of_week,
                schedule.start_time,
                schedule.end_time,
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Schedule overlaps with an existing schedule",
                )

            cur.execute(
                """
                INSERT INTO doctor_schedule (
                    doctor_id,
                    day_of_week,
                    start_time,
                    end_time
                )
                VALUES (%s, %s, %s, %s)
                RETURNING
                    id,
                    day_of_week,
                    start_time,
                    end_time,
                    active
                """,
                (
                    doctor_id,
                    schedule.day_of_week,
                    schedule.start_time,
                    schedule.end_time,
                ),
            )

            row = cur.fetchone()

    return {
        "id": row[0],
        "doctor_id": doctor_id,
        "day_of_week": row[1],
        "start_time": row[2].strftime("%H:%M"),
        "end_time": row[3].strftime("%H:%M"),
        "active": row[4],
    }


@router.put("/{doctor_id}/schedule/{schedule_id}")
def update_doctor_schedule(
    doctor_id: int,
    schedule_id: int,
    schedule: DoctorScheduleCreate,
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
                SELECT id
                FROM doctor_schedule
                WHERE id = %s
                  AND doctor_id = %s
                  AND active = TRUE
                """,
                (schedule_id, doctor_id),
            )

            existing_schedule = cur.fetchone()

            if existing_schedule is None:
                raise HTTPException(
                    status_code=404,
                    detail="Schedule not found",
                )

            if schedule_overlaps(
                cur,
                doctor_id,
                schedule.day_of_week,
                schedule.start_time,
                schedule.end_time,
                exclude_schedule_id=schedule_id,
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Schedule overlaps with an existing schedule",
                )

            cur.execute(
                """
                UPDATE doctor_schedule
                SET day_of_week = %s,
                    start_time = %s,
                    end_time = %s
                WHERE id = %s
                  AND doctor_id = %s
                  AND active = TRUE
                RETURNING
                    id,
                    day_of_week,
                    start_time,
                    end_time,
                    active
                """,
                (
                    schedule.day_of_week,
                    schedule.start_time,
                    schedule.end_time,
                    schedule_id,
                    doctor_id,
                ),
            )

            row = cur.fetchone()

    return {
        "id": row[0],
        "doctor_id": doctor_id,
        "day_of_week": row[1],
        "start_time": row[2].strftime("%H:%M"),
        "end_time": row[3].strftime("%H:%M"),
        "active": row[4],
    }


@router.delete("/{doctor_id}/schedule/{schedule_id}")
def delete_doctor_schedule(
    doctor_id: int,
    schedule_id: int,
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
                UPDATE doctor_schedule
                SET active = FALSE
                WHERE id = %s
                  AND doctor_id = %s
                  AND active = TRUE
                RETURNING id
                """,
                (schedule_id, doctor_id),
            )

            row = cur.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Schedule not found",
                )

    return {
        "id": schedule_id,
        "doctor_id": doctor_id,
        "message": "Schedule removed",
    }