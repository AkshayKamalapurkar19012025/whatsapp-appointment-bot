from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from datetime import date, time

from app.api.staff_auth import require_permission
from app.db.connection import get_connection

router = APIRouter(
    prefix="/doctors",
    tags=["Doctor Schedule"],
)


class DoctorScheduleCreate(BaseModel):
    day_of_week: int = Field(ge=1, le=7)
    start_time: time
    end_time: time
    # WEB P7: NULL (the default) means open-ended on that side, so
    # omitting both keeps a row's pre-P7 "applies forever" meaning.
    start_date: date | None = None
    end_date: date | None = None
    # NULL (the default) means this row applies regardless of
    # department -- see migrations/0010's header. A non-NULL value
    # must be a department this doctor is actually assigned to
    # (checked in the handlers below, not here, since it needs a
    # doctor_departments lookup a field_validator can't do).
    department_id: int | None = None

    @field_validator("end_time")
    @classmethod
    def validate_time_range(cls, value: time, info):
        start_time = info.data.get("start_time")

        if start_time is not None and value <= start_time:
            raise ValueError("End time must be after start time")

        return value

    @field_validator("end_date")
    @classmethod
    def validate_date_range(cls, value: date | None, info):
        start_date = info.data.get("start_date")

        if value is not None and start_date is not None and value < start_date:
            raise ValueError("End date must be on or after start date")

        return value


def schedule_overlaps(
    cur,
    doctor_id: int,
    day_of_week: int,
    start_time: time,
    end_time: time,
    schedule_start_date: date | None = None,
    schedule_end_date: date | None = None,
    exclude_schedule_id: int | None = None,
):
    """
    True if an existing active schedule row for this doctor/day
    conflicts with the given time+date range.

    Two rows conflict only when BOTH their time ranges and their date
    ranges overlap (WEB P7 -- see migrations/0006's header for the
    resolved date-range semantics). A NULL start_date/end_date is
    unbounded on that side, so the interval-overlap test below (each
    side's "IS NULL OR ..." clause) treats it as -infinity/+infinity --
    exactly matching every pre-P7 row's "applies forever" behavior,
    which is why the plain time-only overlap check (no date columns
    involved yet) still worked correctly before this migration.

    Deliberately department-agnostic (migrations/0010): a doctor can
    only be in one place at a time, so this still flags a conflict
    between two rows tagged to different departments -- department_id
    is a label on which of a doctor's time blocks belongs to which
    specialty, not a second independent timeline.
    """
    cur.execute(
        """
        SELECT id
        FROM doctor_schedule
        WHERE doctor_id = %s
          AND day_of_week = %s
          AND active = TRUE
          AND (%s::bigint IS NULL OR id <> %s)
          AND start_time < %s
          AND end_time > %s
          AND (start_date IS NULL OR %s::date IS NULL OR start_date <= %s)
          AND (end_date IS NULL OR %s::date IS NULL OR end_date >= %s)
        """,
        (
            doctor_id,
            day_of_week,
            exclude_schedule_id,
            exclude_schedule_id,
            end_time,
            start_time,
            schedule_end_date,
            schedule_end_date,
            schedule_start_date,
            schedule_start_date,
        ),
    )

    return cur.fetchone() is not None


def doctor_has_department(cur, doctor_id: int, department_id: int) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM doctor_departments
        WHERE doctor_id = %s
          AND department_id = %s
        """,
        (doctor_id, department_id),
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
                    active,
                    start_date,
                    end_date,
                    department_id
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
            "start_date": row[5].isoformat() if row[5] else None,
            "end_date": row[6].isoformat() if row[6] else None,
            "department_id": row[7],
        }
        for row in rows
    ]


@router.post("/{doctor_id}/schedule")
def create_doctor_schedule(
    doctor_id: int,
    schedule: DoctorScheduleCreate,
    admin: dict = Depends(require_permission("doctor_schedule.manage")),
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

            if schedule.department_id is not None and not doctor_has_department(
                cur, doctor_id, schedule.department_id
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Doctor is not assigned to this department",
                )

            if schedule_overlaps(
                cur,
                doctor_id,
                schedule.day_of_week,
                schedule.start_time,
                schedule.end_time,
                schedule.start_date,
                schedule.end_date,
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
                    end_time,
                    start_date,
                    end_date,
                    department_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING
                    id,
                    day_of_week,
                    start_time,
                    end_time,
                    active,
                    start_date,
                    end_date,
                    department_id
                """,
                (
                    doctor_id,
                    schedule.day_of_week,
                    schedule.start_time,
                    schedule.end_time,
                    schedule.start_date,
                    schedule.end_date,
                    schedule.department_id,
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
        "start_date": row[5].isoformat() if row[5] else None,
        "end_date": row[6].isoformat() if row[6] else None,
        "department_id": row[7],
    }


@router.put("/{doctor_id}/schedule/{schedule_id}")
def update_doctor_schedule(
    doctor_id: int,
    schedule_id: int,
    schedule: DoctorScheduleCreate,
    admin: dict = Depends(require_permission("doctor_schedule.manage")),
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

            if schedule.department_id is not None and not doctor_has_department(
                cur, doctor_id, schedule.department_id
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Doctor is not assigned to this department",
                )

            if schedule_overlaps(
                cur,
                doctor_id,
                schedule.day_of_week,
                schedule.start_time,
                schedule.end_time,
                schedule.start_date,
                schedule.end_date,
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
                    end_time = %s,
                    start_date = %s,
                    end_date = %s,
                    department_id = %s
                WHERE id = %s
                  AND doctor_id = %s
                  AND active = TRUE
                RETURNING
                    id,
                    day_of_week,
                    start_time,
                    end_time,
                    active,
                    start_date,
                    end_date,
                    department_id
                """,
                (
                    schedule.day_of_week,
                    schedule.start_time,
                    schedule.end_time,
                    schedule.start_date,
                    schedule.end_date,
                    schedule.department_id,
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
        "start_date": row[5].isoformat() if row[5] else None,
        "end_date": row[6].isoformat() if row[6] else None,
        "department_id": row[7],
    }


@router.delete("/{doctor_id}/schedule/{schedule_id}")
def delete_doctor_schedule(
    doctor_id: int,
    schedule_id: int,
    admin: dict = Depends(require_permission("doctor_schedule.manage")),
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
