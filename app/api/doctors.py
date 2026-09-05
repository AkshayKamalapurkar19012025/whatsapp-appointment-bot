from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
import psycopg

from app.api.staff_auth import get_current_staff, require_role
from app.db.connection import get_connection
from app.utils.timezone import convert_to_timezone, validate_timezone

router = APIRouter(prefix="/doctors", tags=["Doctors"])


class DoctorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Doctor name cannot be empty")

        return value


@router.get("")
def get_doctors():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, d.name, d.active, d.created_at, s.username
                FROM doctors d
                LEFT JOIN staff s ON s.id = d.created_by
                WHERE d.active = TRUE
                ORDER BY d.name
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "active": row[2],
            "created_at": row[3].isoformat(),
            "created_by": row[4],
        }
        for row in rows
    ]


@router.post("")
def create_doctor(
    doctor: DoctorCreate,
    admin: dict = Depends(require_role("ADMIN")),
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO doctors (name, created_by)
                    VALUES (%s, %s)
                    RETURNING id, name, active, created_at
                    """,
                    (doctor.name, admin["id"]),
                )
                row = cur.fetchone()

        return {
            "id": row[0],
            "name": row[1],
            "active": row[2],
            "created_at": row[3].isoformat(),
            "created_by": admin["username"],
        }

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="Doctor already exists",
        )


@router.get("/{doctor_id}/departments")
def get_doctor_departments(doctor_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, d.name, d.active
                FROM doctor_departments dd
                JOIN departments d
                    ON d.id = dd.department_id
                WHERE dd.doctor_id = %s
                  AND d.active = TRUE
                ORDER BY d.name
                """,
                (doctor_id,),
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


@router.post("/{doctor_id}/departments/{department_id}")
def assign_department_to_doctor(
    doctor_id: int,
    department_id: int,
    admin: dict = Depends(require_role("ADMIN")),
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
                FROM departments
                WHERE id = %s
                  AND active = TRUE
                """,
                (department_id,),
            )

            department = cur.fetchone()

            if department is None:
                raise HTTPException(
                    status_code=404,
                    detail="Department not found",
                )

            try:
                cur.execute(
                    """
                    INSERT INTO doctor_departments (
                        doctor_id,
                        department_id
                    )
                    VALUES (%s, %s)
                    """,
                    (doctor_id, department_id),
                )

            except psycopg.errors.UniqueViolation:
                raise HTTPException(
                    status_code=409,
                    detail="Department already assigned to doctor",
                )

    return {
        "doctor_id": doctor_id,
        "department_id": department[0],
        "department_name": department[1],
    }


@router.delete("/{doctor_id}/departments/{department_id}")
def remove_department_from_doctor(
    doctor_id: int,
    department_id: int,
    admin: dict = Depends(require_role("ADMIN")),
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
                DELETE FROM doctor_departments
                WHERE doctor_id = %s
                  AND department_id = %s
                RETURNING doctor_id, department_id
                """,
                (doctor_id, department_id),
            )

            removed = cur.fetchone()

            if removed is None:
                raise HTTPException(
                    status_code=404,
                    detail="Department is not assigned to doctor",
                )

    return {
        "doctor_id": removed[0],
        "department_id": removed[1],
        "message": "Department removed from doctor",
    }


@router.get("/{doctor_id}/queue")
def get_doctor_queue(
    doctor_id: int,
    staff: dict = Depends(get_current_staff),
):
    """
    Today's walk-in queue for this doctor (migrations/0012_appointment_
    queue_tokens.sql): patients checked in today (status VISITED or
    COMPLETED, token_number assigned at check-in -- see mark_visited_
    service), split into "now serving" (the lowest still-waiting token
    -- this app has no separate "in consultation" status, so the
    lowest-numbered VISITED row still waiting is the working definition
    of who's up), the rest of the VISITED rows waiting behind them, and
    today's already-Completed patients for reference.

    "Today" is the doctor's own local calendar day, matching every other
    per-doctor-per-day cut in this app (dashboard stats, this queue's own
    token-number assignment).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, timezone FROM doctors WHERE id = %s AND active = TRUE",
                (doctor_id,),
            )
            doctor = cur.fetchone()

            if doctor is None:
                raise HTTPException(status_code=404, detail="Doctor not found")

            doctor_name, doctor_tz = doctor
            if not validate_timezone(doctor_tz):
                doctor_tz = "Asia/Kolkata"

            today = datetime.now(ZoneInfo(doctor_tz)).date()

            cur.execute(
                """
                SELECT a.id, a.status, a.token_number, a.visited_at, p.id, p.name
                FROM appointments a
                JOIN patients p ON p.id = a.patient_id
                WHERE a.doctor_id = %s
                  AND a.status IN ('VISITED', 'COMPLETED')
                  AND (a.visited_at AT TIME ZONE %s)::date = %s
                ORDER BY a.token_number
                """,
                (doctor_id, doctor_tz, today),
            )
            rows = cur.fetchall()

    def entry(row):
        return {
            "appointment_id": row[0],
            "token_number": row[2],
            # Converted to the doctor's own local time before returning
            # -- a raw TIMESTAMPTZ read-back always comes back UTC-
            # labeled from psycopg regardless of what offset it was
            # written with (the same bug class already fixed at every
            # other appointment-time display in this app; see e.g.
            # app/api/appointments.py's admin listing docstring).
            "visited_at": convert_to_timezone(row[3], doctor_tz).isoformat(),
            "patient_id": row[4],
            "patient_name": row[5],
        }

    waiting = [entry(r) for r in rows if r[1] == "VISITED"]
    completed = [entry(r) for r in rows if r[1] == "COMPLETED"]
    now_serving = waiting.pop(0) if waiting else None

    return {
        "doctor_id": doctor_id,
        "doctor_name": doctor_name,
        "date": today.isoformat(),
        "now_serving": now_serving,
        "waiting": waiting,
        "completed": completed,
    }