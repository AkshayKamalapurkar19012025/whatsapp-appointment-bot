from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.services.patient_identifiers import resolve_patient_by_identifier, write_phone_identifier
from app.services.uhid import generate_uhid
from app.utils.phone import normalize_whatsapp_number

router = APIRouter(
    prefix="/patients",
    tags=["Patients"],
)


def insert_patient(
    cur,
    name: str,
    whatsapp_number: str,
    date_of_birth: date | None = None,
    gender: str | None = None,
):
    """date_of_birth/gender are optional everywhere this is called from
    (admin create_patient below, and app/api/scheduling.py's WhatsApp
    registration, which never collects either) -- defaulting to None
    keeps that WhatsApp call site unchanged."""
    cur.execute(
        """
        INSERT INTO patients (
            name,
            whatsapp_number,
            date_of_birth,
            gender
        )
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (whatsapp_number) DO NOTHING
        RETURNING id, name, whatsapp_number, date_of_birth, gender, hospital_id
        """,
        (
            name,
            whatsapp_number,
            date_of_birth,
            gender,
        ),
    )

    row = cur.fetchone()

    if row is None:
        return None

    # M4-M5 dual write -- see app/services/patient_identifiers.py.
    write_phone_identifier(
        cur, hospital_id=row[5], patient_id=row[0], whatsapp_number=row[2]
    )

    # M6: assign this patient's permanent UHID at creation time -- see
    # app/services/uhid.py. Unlike whatsapp_number, never regenerated or
    # touched again once assigned (update_patient does not call this).
    uhid = generate_uhid(cur, row[5])
    cur.execute("UPDATE patients SET uhid = %s WHERE id = %s", (uhid, row[0]))

    return {
        "id": row[0],
        "name": row[1],
        "whatsapp_number": row[2],
        "date_of_birth": row[3].isoformat() if row[3] else None,
        "gender": row[4],
        "uhid": uhid,
    }


class _PatientFieldValidators:
    """Shared name/whatsapp_number validation -- mixed into both
    PatientCreate and PatientUpdate below (Phase 2 of the patient
    arrival workflow adds the latter) so the two never drift out of
    sync on what counts as a valid patient name or number. Plain
    mixin, not a BaseModel subclass, matching pydantic's own documented
    pattern for sharing field_validators across models: it must come
    before BaseModel in each model's base list."""

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Patient name cannot be empty")

        return value

    @field_validator("whatsapp_number")
    @classmethod
    def validate_whatsapp_number(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("WhatsApp number cannot be empty")

        return normalize_whatsapp_number(value)

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("MALE", "FEMALE", "OTHER"):
            raise ValueError("gender must be one of MALE, FEMALE, OTHER")
        return value


class PatientCreate(_PatientFieldValidators, BaseModel):
    name: str = Field(min_length=1, max_length=150)
    whatsapp_number: str = Field(min_length=1, max_length=30)
    # Optional -- fast registration (especially for a walk-in) must
    # never be blocked on these. Staff can add them now or later via
    # PatientUpdate.
    date_of_birth: date | None = None
    gender: str | None = None


class PatientUpdate(_PatientFieldValidators, BaseModel):
    """For PATCH /patients/{id} -- staff correcting a patient's name or
    WhatsApp number discovered wrong during front-desk verification.
    Same two required fields, same validation as PatientCreate, plus
    the same two optional demographics; the two models stay separate
    (rather than making PatientCreate's fields optional and reusing it
    directly) since create and update have different semantics
    (whatsapp_number collision means "already exists" on create,
    "belongs to a different patient" on update -- see update_patient
    below)."""

    name: str = Field(min_length=1, max_length=150)
    whatsapp_number: str = Field(min_length=1, max_length=30)
    date_of_birth: date | None = None
    gender: str | None = None


@router.get("")
def get_patients(staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.whatsapp_number,
                    -- a.status::text: see availability_engine.py's
                    -- get_available_slots for why (enum-typed
                    -- appointments.status on some databases).
                    COUNT(a.id) FILTER (WHERE NOT (a.status::text = ANY(ARRAY['CANCELLED', 'REJECTED']))),
                    p.date_of_birth,
                    p.gender,
                    -- Most recent real appointment (same exclusion as
                    -- appointment_count above), an audit-log-style "when
                    -- did this happen" fact -- see the OPD Patients-page
                    -- redesign report for why this is deliberately NOT
                    -- converted to any one doctor's local time the way an
                    -- appointment slot time is (a patient's history can
                    -- span doctors in different timezones; format.ts's
                    -- formatDateTime renders this in the viewer's own
                    -- local time instead, the same convention already
                    -- used for created_at elsewhere in this app).
                    MAX(a.start_at) FILTER (WHERE NOT (a.status::text = ANY(ARRAY['CANCELLED', 'REJECTED']))),
                    -- M6: nullable -- a patient created before this
                    -- column existed and not yet backfilled has none.
                    p.uhid
                FROM patients p
                LEFT JOIN appointments a ON a.patient_id = p.id
                GROUP BY p.id, p.name, p.whatsapp_number, p.date_of_birth, p.gender, p.uhid
                ORDER BY p.name
                """
            )

            rows = cur.fetchall()

    # "Recurring" here means the patient has more than one appointment
    # on record that was never cancelled or rejected (2+ real requests
    # that were, or still could be, actual visits); 0 or 1 reads as
    # "first-time" -- every other status (PENDING/CONFIRMED/CHECKED_IN/
    # COMPLETED/NO_SHOW, see migrations/0011_appointment_lifecycle_
    # statuses.sql and migrations/0015)
    # counts, including one already in the past. Kept for any existing
    # caller, but the OPD Patients page no longer treats this as the
    # patient's primary/permanent attribute -- see last_visit_at/
    # appointment_count instead, which the redesigned page actually shows.
    return [
        {
            "id": row[0],
            "name": row[1],
            "whatsapp_number": row[2],
            "appointment_count": row[3],
            "patient_type": "recurring" if row[3] > 1 else "first-time",
            "date_of_birth": row[4].isoformat() if row[4] else None,
            "gender": row[5],
            "last_visit_at": row[6].isoformat() if row[6] else None,
            "uhid": row[7],
        }
        for row in rows
    ]


@router.post("")
def create_patient(
    patient: PatientCreate,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            # M4-M5: migrated onto the identifier resolver (admin patient
            # lookup) -- see app/services/patient_identifiers.py. The
            # column is still what actually enforces uniqueness (the
            # UNIQUE constraint on patients.whatsapp_number, unchanged
            # until M7); this check is just a friendlier 409 ahead of it.
            if resolve_patient_by_identifier(
                cur, staff["hospital_id"], "PHONE", patient.whatsapp_number
            ) is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Patient with this WhatsApp number already exists",
                )

            created_patient = insert_patient(
                cur,
                patient.name,
                patient.whatsapp_number,
                patient.date_of_birth,
                patient.gender,
            )

            if created_patient is None:
                raise HTTPException(
                    status_code=409,
                    detail="Patient with this WhatsApp number already exists",
                )

    return created_patient


@router.patch("/{patient_id}")
def update_patient(
    patient_id: int,
    patient: PatientUpdate,
    staff: dict = Depends(get_current_staff),
):
    """
    Staff correcting a patient's name or WhatsApp number -- the
    "Verify details / Update if required" step of front-desk check-in
    (patient arrival workflow Phase 2). Reuses the same patient record
    create_patient already established (no second patient table/
    identity, no new dedup mechanism): this only ever UPDATEs an
    existing row, never inserts one.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM patients WHERE id = %s", (patient_id,))

            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Patient not found")

            # Unlike create_patient's check (any existing row with this
            # number is a conflict), a patient keeping their own current
            # number must not conflict with themselves -- only a
            # *different* patient already owning this number is a real
            # collision. M4-M5: migrated onto the identifier resolver,
            # same as create_patient above.
            match = resolve_patient_by_identifier(
                cur, staff["hospital_id"], "PHONE", patient.whatsapp_number
            )

            if match is not None and match["id"] != patient_id:
                raise HTTPException(
                    status_code=409,
                    detail="Another patient with this WhatsApp number already exists",
                )

            cur.execute(
                """
                UPDATE patients
                SET name = %s,
                    whatsapp_number = %s,
                    date_of_birth = %s,
                    gender = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, name, whatsapp_number, date_of_birth, gender, hospital_id
                """,
                (patient.name, patient.whatsapp_number, patient.date_of_birth, patient.gender, patient_id),
            )

            row = cur.fetchone()

            # M4-M5 dual write -- see app/services/patient_identifiers.py.
            write_phone_identifier(
                cur, hospital_id=row[5], patient_id=row[0], whatsapp_number=row[2]
            )

    return {
        "id": row[0],
        "name": row[1],
        "whatsapp_number": row[2],
        "date_of_birth": row[3].isoformat() if row[3] else None,
        "gender": row[4],
    }
