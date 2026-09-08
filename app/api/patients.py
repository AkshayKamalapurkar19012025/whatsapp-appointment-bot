from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.utils.phone import normalize_whatsapp_number

router = APIRouter(
    prefix="/patients",
    tags=["Patients"],
)


def insert_patient(cur, name: str, whatsapp_number: str):
    cur.execute(
        """
        INSERT INTO patients (
            name,
            whatsapp_number
        )
        VALUES (%s, %s)
        ON CONFLICT (whatsapp_number) DO NOTHING
        RETURNING id, name, whatsapp_number
        """,
        (
            name,
            whatsapp_number,
        ),
    )

    row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "whatsapp_number": row[2],
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


class PatientCreate(_PatientFieldValidators, BaseModel):
    name: str = Field(min_length=1, max_length=150)
    whatsapp_number: str = Field(min_length=1, max_length=30)


class PatientUpdate(_PatientFieldValidators, BaseModel):
    """For PATCH /patients/{id} -- staff correcting a patient's name or
    WhatsApp number discovered wrong during front-desk verification.
    Same two fields, same validation as PatientCreate; the two models
    stay separate (rather than making PatientCreate's fields optional
    and reusing it directly) since create and update have different
    semantics (whatsapp_number collision means "already exists" on
    create, "belongs to a different patient" on update -- see
    update_patient below)."""

    name: str = Field(min_length=1, max_length=150)
    whatsapp_number: str = Field(min_length=1, max_length=30)


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
                    COUNT(a.id) FILTER (WHERE NOT (a.status::text = ANY(ARRAY['CANCELLED', 'REJECTED'])))
                FROM patients p
                LEFT JOIN appointments a ON a.patient_id = p.id
                GROUP BY p.id, p.name, p.whatsapp_number
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
    # counts, including one already in the past.
    return [
        {
            "id": row[0],
            "name": row[1],
            "whatsapp_number": row[2],
            "appointment_count": row[3],
            "patient_type": "recurring" if row[3] > 1 else "first-time",
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

            cur.execute(
                """
                SELECT id
                FROM patients
                WHERE whatsapp_number = %s
                """,
                (patient.whatsapp_number,),
            )

            if cur.fetchone() is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Patient with this WhatsApp number already exists",
                )

            created_patient = insert_patient(
                cur,
                patient.name,
                patient.whatsapp_number,
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
            # collision.
            cur.execute(
                "SELECT id FROM patients WHERE whatsapp_number = %s AND id <> %s",
                (patient.whatsapp_number, patient_id),
            )

            if cur.fetchone() is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Another patient with this WhatsApp number already exists",
                )

            cur.execute(
                """
                UPDATE patients
                SET name = %s,
                    whatsapp_number = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, name, whatsapp_number
                """,
                (patient.name, patient.whatsapp_number, patient_id),
            )

            row = cur.fetchone()

    return {"id": row[0], "name": row[1], "whatsapp_number": row[2]}
