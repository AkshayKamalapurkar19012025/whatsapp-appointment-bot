from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
import psycopg

from app.api.staff_auth import get_current_staff, require_permission
from app.db.connection import get_connection
from app.services.availability_engine import DOCTOR_SUMMARY_SELECT_SQL, DOCTOR_SUMMARY_JOIN_SQL, build_doctor_summary
from app.utils.timezone import convert_to_timezone, validate_timezone

router = APIRouter(prefix="/doctors", tags=["Doctors"])


class DoctorCreate(BaseModel):
    """
    Also reused, unchanged, as the body shape for PUT /{doctor_id}
    (update_doctor) -- both creation and edit require the exact same
    fields, per the product decision that specialization is required
    going forward. Existing doctors created before this change simply
    have specialization=NULL until an admin edits them through this same
    model -- no backfill migration, no separate "legacy doctor" shape.
    """

    name: str = Field(min_length=1, max_length=150)
    specialization: str = Field(min_length=1, max_length=150)
    sub_specialization: str | None = Field(default=None, max_length=150)
    # Headline summary (e.g. "MBBS, MD (Cardiology)") -- the compact
    # scheduling card's "key qualification" line. Distinct from the
    # per-entry `qualification` recorded on each doctor_education row.
    qualifications: str | None = Field(default=None, max_length=255)
    years_of_experience: int | None = Field(default=None, ge=0, le=80)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Doctor name cannot be empty")

        return value

    @field_validator("specialization")
    @classmethod
    def validate_specialization(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Specialization cannot be empty")

        return value

    @field_validator("sub_specialization", "qualifications")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None


class DoctorEducationCreate(BaseModel):
    """
    One Education & Training entry. All fields required -- an entry with
    a missing institution or year is not meaningful, so this is always
    inserted all-or-nothing (see migrations/0014_doctor_profile.sql).
    Never carries is_primary: an entry is created as non-featured and is
    only ever promoted via POST .../education/{id}/feature, so "at most
    one featured entry" only ever needs handling in one place.
    """

    qualification: str = Field(min_length=1, max_length=150)
    institution: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    country: str = Field(min_length=1, max_length=100)
    completion_year: int

    @field_validator("qualification", "institution", "city", "country")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("This field cannot be empty")

        return value

    @field_validator("completion_year")
    @classmethod
    def validate_completion_year(cls, value: int) -> int:
        current_year = datetime.now().year

        if value < 1950 or value > current_year:
            raise ValueError(f"Completion year must be between 1950 and {current_year}")

        return value


def _education_entry_dict(row, doctor_id: int) -> dict:
    return {
        "id": row[0],
        "doctor_id": doctor_id,
        "qualification": row[1],
        "institution": row[2],
        "city": row[3],
        "country": row[4],
        "completion_year": row[5],
        "is_primary": row[6],
    }


@router.get("")
def get_doctors():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT d.id, d.name, d.active, d.created_at, s.username,
                       d.default_duration_minutes, d.buffer_minutes,
                       {DOCTOR_SUMMARY_SELECT_SQL}
                FROM doctors d
                LEFT JOIN staff s ON s.id = d.created_by
                {DOCTOR_SUMMARY_JOIN_SQL}
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
            "default_duration_minutes": row[5],
            "buffer_minutes": row[6],
            **{k: v for k, v in build_doctor_summary(row[0], row[1], row[7:]).items() if k not in ("id", "name")},
        }
        for row in rows
    ]


@router.post("")
def create_doctor(
    doctor: DoctorCreate,
    admin: dict = Depends(require_permission("doctor.manage")),
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO doctors (
                        name, specialization, sub_specialization,
                        qualifications, years_of_experience, created_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, name, active, created_at, specialization,
                              sub_specialization, qualifications, years_of_experience, photo_url,
                              default_duration_minutes, buffer_minutes
                    """,
                    (
                        doctor.name,
                        doctor.specialization,
                        doctor.sub_specialization,
                        doctor.qualifications,
                        doctor.years_of_experience,
                        admin["id"],
                    ),
                )
                row = cur.fetchone()

        return {
            "id": row[0],
            "name": row[1],
            "active": row[2],
            "created_at": row[3].isoformat(),
            "created_by": admin["username"],
            "specialization": row[4],
            "sub_specialization": row[5],
            "qualifications": row[6],
            "years_of_experience": row[7],
            "photo_url": row[8],
            "default_duration_minutes": row[9],
            "buffer_minutes": row[10],
            # A brand-new doctor cannot have a featured doctor_education
            # entry yet (there's nowhere it could have come from) --
            # explicit rather than omitted, so this response matches the
            # same DoctorProfileSummary shape every listing endpoint
            # returns (see build_doctor_summary()), not one field short.
            "education_location": None,
        }

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="Doctor already exists",
        )


@router.put("/{doctor_id}")
def update_doctor(
    doctor_id: int,
    doctor: DoctorCreate,
    admin: dict = Depends(require_permission("doctor.manage")),
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE doctors
                    SET name = %s,
                        specialization = %s,
                        sub_specialization = %s,
                        qualifications = %s,
                        years_of_experience = %s,
                        updated_at = NOW()
                    WHERE id = %s
                      AND active = TRUE
                    RETURNING id, name, active, created_at, specialization,
                              sub_specialization, qualifications, years_of_experience, photo_url,
                              default_duration_minutes, buffer_minutes
                    """,
                    (
                        doctor.name,
                        doctor.specialization,
                        doctor.sub_specialization,
                        doctor.qualifications,
                        doctor.years_of_experience,
                        doctor_id,
                    ),
                )
                row = cur.fetchone()

                if row is None:
                    raise HTTPException(status_code=404, detail="Doctor not found")

        return {
            "id": row[0],
            "name": row[1],
            "active": row[2],
            "created_at": row[3].isoformat(),
            "specialization": row[4],
            "sub_specialization": row[5],
            "qualifications": row[6],
            "years_of_experience": row[7],
            "photo_url": row[8],
            "default_duration_minutes": row[9],
            "buffer_minutes": row[10],
        }

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="Doctor already exists",
        )


class DoctorSlotSettingsUpdate(BaseModel):
    """
    Body for PATCH /{doctor_id}/slot-settings -- deliberately its own
    small endpoint rather than folded into PUT /{doctor_id}, which
    requires resending every scalar field on the doctor (name,
    specialization, ...) since it has no partial-update support. These
    two fields are edited from a completely different part of the UI
    (Schedule tab's "Slot settings" panel, not the Profile form) and
    have nothing to do with identity/specialization, so a dedicated
    partial-update endpoint avoids that coupling entirely.
    """

    default_duration_minutes: int = Field(ge=5, le=240)
    buffer_minutes: int = Field(ge=0, le=120)


@router.patch("/{doctor_id}/slot-settings")
def update_doctor_slot_settings(
    doctor_id: int,
    settings: DoctorSlotSettingsUpdate,
    admin: dict = Depends(require_permission("doctor.manage")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE doctors
                SET default_duration_minutes = %s,
                    buffer_minutes = %s,
                    updated_at = NOW()
                WHERE id = %s
                  AND active = TRUE
                RETURNING id, default_duration_minutes, buffer_minutes
                """,
                (settings.default_duration_minutes, settings.buffer_minutes, doctor_id),
            )
            row = cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Doctor not found")

    return {
        "id": row[0],
        "default_duration_minutes": row[1],
        "buffer_minutes": row[2],
    }


class DoctorActiveUpdate(BaseModel):
    active: bool


@router.patch("/{doctor_id}/active")
def update_doctor_active(
    doctor_id: int,
    body: DoctorActiveUpdate,
    admin: dict = Depends(require_permission("doctor.manage")),
):
    """
    Deactivate/reactivate a doctor (the workspace header's "..." menu).
    There was previously no way to do this at all through the web
    admin -- every other doctor-scoped endpoint already treats
    `active = FALSE` as "doesn't exist" (see get_doctors,
    get_doctor_profile_and_education, etc. above), so flipping this
    column is what actually removes a doctor from every listing,
    booking flow, and availability calculation without deleting any
    of their history (schedule, appointments, education all stay put).
    Deliberately allowed to reactivate too (active: true), for
    reversing an accidental deactivation -- this is the one doctor-
    scoped update endpoint that has to work even when the doctor is
    currently inactive, so it has no `AND active = TRUE` guard.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE doctors
                SET active = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, active
                """,
                (body.active, doctor_id),
            )
            row = cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Doctor not found")

    return {"id": row[0], "active": row[1]}


def get_doctor_profile_and_education(cur, doctor_id: int) -> dict | None:
    """
    The full profile + complete education history -- unlike every
    doctor-listing query elsewhere in this module (which only ever
    computes the compact summary fields via build_doctor_summary()),
    this is what "View Profile" fetches on demand, on both channels:
    GET /{doctor_id} below (web) and app/api/scheduling.py's WhatsApp
    "PROFILE <number>" side-channel reply both call this same function,
    so the two channels can never drift onto different profile data.

    Returns None if no such active doctor exists -- callers decide how
    to surface that (an HTTP 404 for the web route, a plain WhatsApp
    reply for the side-channel).
    """
    cur.execute(
        """
        SELECT id, name, active, created_at, specialization,
               sub_specialization, qualifications, years_of_experience, photo_url,
               default_duration_minutes, buffer_minutes
        FROM doctors
        WHERE id = %s
          AND active = TRUE
        """,
        (doctor_id,),
    )
    row = cur.fetchone()

    if row is None:
        return None

    cur.execute(
        """
        SELECT id, qualification, institution, city, country, completion_year, is_primary
        FROM doctor_education
        WHERE doctor_id = %s
        ORDER BY completion_year DESC, id
        """,
        (doctor_id,),
    )
    education_rows = cur.fetchall()

    return {
        "id": row[0],
        "name": row[1],
        "active": row[2],
        "created_at": row[3].isoformat(),
        "specialization": row[4],
        "sub_specialization": row[5],
        "qualifications": row[6],
        "years_of_experience": row[7],
        "photo_url": row[8],
        "default_duration_minutes": row[9],
        "buffer_minutes": row[10],
        "education": [_education_entry_dict(e, doctor_id) for e in education_rows],
    }


@router.get("/{doctor_id}")
def get_doctor_profile(doctor_id: int):
    """
    Deliberately unauthenticated, matching GET /{doctor_id}/departments
    below: patients viewing a doctor's profile mid-scheduling are not staff.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            profile = get_doctor_profile_and_education(cur, doctor_id)

    if profile is None:
        raise HTTPException(status_code=404, detail="Doctor not found")

    return profile


@router.post("/{doctor_id}/education")
def add_doctor_education(
    doctor_id: int,
    entry: DoctorEducationCreate,
    admin: dict = Depends(require_permission("doctor.manage")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM doctors WHERE id = %s AND active = TRUE",
                (doctor_id,),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Doctor not found")

            cur.execute(
                """
                INSERT INTO doctor_education (
                    doctor_id, qualification, institution, city, country, completion_year
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, qualification, institution, city, country, completion_year, is_primary
                """,
                (
                    doctor_id,
                    entry.qualification,
                    entry.institution,
                    entry.city,
                    entry.country,
                    entry.completion_year,
                ),
            )
            row = cur.fetchone()

    return _education_entry_dict(row, doctor_id)


@router.delete("/{doctor_id}/education/{education_id}")
def remove_doctor_education(
    doctor_id: int,
    education_id: int,
    admin: dict = Depends(require_permission("doctor.manage")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM doctor_education
                WHERE id = %s
                  AND doctor_id = %s
                RETURNING id
                """,
                (education_id, doctor_id),
            )
            removed = cur.fetchone()

    if removed is None:
        raise HTTPException(status_code=404, detail="Education entry not found")

    return {"id": education_id, "doctor_id": doctor_id, "message": "Education entry removed"}


@router.post("/{doctor_id}/education/{education_id}/feature")
def feature_doctor_education(
    doctor_id: int,
    education_id: int,
    admin: dict = Depends(require_permission("doctor.manage")),
):
    """
    Marks this entry as the one shown on the compact scheduling card's
    education/training line, first clearing whatever entry (if any) was
    previously featured for this doctor. The partial unique index
    (migrations/0014_doctor_profile.sql) guarantees at most one survives
    regardless, but clearing the old one first here means the API path
    never actually hits that constraint.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM doctor_education WHERE id = %s AND doctor_id = %s",
                (education_id, doctor_id),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Education entry not found")

            cur.execute(
                "UPDATE doctor_education SET is_primary = FALSE WHERE doctor_id = %s AND is_primary = TRUE",
                (doctor_id,),
            )
            cur.execute(
                """
                UPDATE doctor_education
                SET is_primary = TRUE
                WHERE id = %s
                RETURNING id, qualification, institution, city, country, completion_year, is_primary
                """,
                (education_id,),
            )
            row = cur.fetchone()

    return _education_entry_dict(row, doctor_id)


@router.delete("/{doctor_id}/education/{education_id}/feature")
def unfeature_doctor_education(
    doctor_id: int,
    education_id: int,
    admin: dict = Depends(require_permission("doctor.manage")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE doctor_education
                SET is_primary = FALSE
                WHERE id = %s
                  AND doctor_id = %s
                RETURNING id, qualification, institution, city, country, completion_year, is_primary
                """,
                (education_id, doctor_id),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Education entry not found")

    return _education_entry_dict(row, doctor_id)


@router.get("/{doctor_id}/departments")
def get_doctor_departments(doctor_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, d.name, d.active, dd.created_at
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
            # When this department was assigned to this doctor -- not
            # shown directly, but lets the frontend treat the
            # earliest-assigned department as "primary" without a new
            # is_primary column (doctor_departments has always had
            # created_at; nothing here was previously exposed).
            "assigned_at": row[3].isoformat(),
        }
        for row in rows
    ]


@router.post("/{doctor_id}/departments/{department_id}")
def assign_department_to_doctor(
    doctor_id: int,
    department_id: int,
    admin: dict = Depends(require_permission("doctor.manage")),
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
    admin: dict = Depends(require_permission("doctor.manage")),
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
    queue_tokens.sql): patients checked in today (status CHECKED_IN or
    COMPLETED) who have actually been issued a token -- as of the
    patient arrival workflow's Phase 4, that's no longer everyone who's
    CHECKED_IN (token_number is assigned by record_payment_service's
    PAID outcome or waive_consultation_fee_service, not check-in
    itself any more; see mark_visited_service's docstring). The
    explicit token_number IS NOT NULL filter below is what keeps a
    checked-in-but-unpaid patient off this list -- the core business
    rule ("don't enter the queue before payment") enforced at the
    query that actually surfaces the queue to a doctor, not just at
    write time.

    Split into "now serving" (the lowest still-waiting token -- this
    app has no separate "in consultation" status, so the
    lowest-numbered CHECKED_IN row still waiting is the working
    definition of who's up), the rest of the CHECKED_IN rows waiting
    behind them, and today's already-Completed patients for reference.

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
                  AND a.status IN ('CHECKED_IN', 'COMPLETED')
                  AND a.token_number IS NOT NULL
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

    waiting = [entry(r) for r in rows if r[1] == "CHECKED_IN"]
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