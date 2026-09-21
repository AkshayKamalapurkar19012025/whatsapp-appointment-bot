from datetime import date

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.patient_duplicate_detection import decide_duplicate_review, find_duplicate_candidates
from app.services.patient_identifiers import resolve_patient_by_identifier, write_phone_identifier
from app.services.patient_merge import merge_patients, unmerge_patients
from app.services.patient_timeline_service import get_patient_timeline_service
from app.services.uhid import resolve_patient_by_uhid
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
    government_id: str | None = None,
):
    """date_of_birth/gender are optional everywhere this is called from
    (admin create_patient below, and app/api/scheduling.py's WhatsApp
    registration, which never collects either) -- defaulting to None
    keeps that WhatsApp call site unchanged.

    M7 (in progress): ON CONFLICT (whatsapp_number) is gone -- it needs
    a matching unique/exclusion constraint or index to target, so it
    becomes invalid SQL the moment patients.whatsapp_number's UNIQUE
    constraint is actually dropped, whenever that eventually ships (out
    of scope for this pass -- the constraint itself is untouched).
    Until that drop ships, the constraint is still live, so the same
    race this used to resolve via DO NOTHING can still raise
    UniqueViolation here -- caught below and treated exactly the same
    way (nothing created, caller decides what that means). Once the
    constraint is gone this except simply never fires again; two
    patients sharing a number then both insert successfully, which is
    the point of dropping it.

    uhid (migrations/0024_patient_uhid.sql) is GENERATED ALWAYS AS
    (...) STORED, derived from id -- already present on the row the
    INSERT below returns, no separate assignment step needed the way
    date_of_birth/gender/government_id (actual input) are.
    """
    try:
        cur.execute(
            """
            INSERT INTO patients (
                name,
                whatsapp_number,
                date_of_birth,
                gender,
                government_id
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, name, whatsapp_number, date_of_birth, gender, hospital_id, government_id, uhid
            """,
            (
                name,
                whatsapp_number,
                date_of_birth,
                gender,
                government_id,
            ),
        )
    except psycopg.errors.UniqueViolation:
        cur.connection.rollback()
        return None

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
        "government_id": row[6],
        "uhid": row[7],
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
    # M8: one of the four duplicate-detection signals (see
    # app/services/patient_duplicate_detection.py). Optional, same as
    # date_of_birth/gender -- never required by registration.
    government_id: str | None = None


class PatientUpdate(_PatientFieldValidators, BaseModel):
    """For PATCH /patients/{id} -- staff correcting a patient's name or
    WhatsApp number discovered wrong during front-desk verification.
    Same two required fields, same validation as PatientCreate, plus
    the same optional demographics; the two models stay separate
    (rather than making PatientCreate's fields optional and reusing it
    directly) since create and update have different semantics
    (whatsapp_number collision means "already exists" on create,
    "belongs to a different patient" on update -- see update_patient
    below)."""

    name: str = Field(min_length=1, max_length=150)
    whatsapp_number: str = Field(min_length=1, max_length=30)
    date_of_birth: date | None = None
    gender: str | None = None
    government_id: str | None = None


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


@router.get("/search")
def search_patients(
    q: str | None = None,
    dob: date | None = None,
    staff: dict = Depends(get_current_staff),
):
    """
    Backend-driven patient lookup for the OPD "find patient before
    registering" step -- unlike GET /patients above (the full master
    registry, meant to be paged through/browsed client-side), this is
    for typing a mobile number, name, or UHID at the front desk and
    getting back only plausible matches, so staff can positively
    identify an existing patient (and avoid creating a duplicate)
    before ever reaching the registration form.

    q matches name/whatsapp_number/uhid by substring (case-insensitive);
    dob narrows to an exact date-of-birth match, for a "name + DOB"
    search when a name alone is too common to disambiguate. At least
    one of q/dob is required -- this is a lookup, not a second way to
    list every patient (that's GET /patients, unchanged). Capped at 20
    results: enough to show every plausible match at a front desk, not
    a paginated browse.
    """
    if not q and not dob:
        raise HTTPException(
            status_code=400,
            detail="Provide a search term (q) and/or a date of birth (dob).",
        )

    conditions = []
    params: list = []

    if q:
        needle = f"%{q.strip()}%"
        conditions.append(
            "(p.name ILIKE %s OR p.whatsapp_number ILIKE %s OR p.uhid ILIKE %s)"
        )
        params.extend([needle, needle, needle])

    if dob:
        conditions.append("p.date_of_birth = %s")
        params.append(dob)

    where_clause = " AND ".join(conditions)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    p.id,
                    p.name,
                    p.whatsapp_number,
                    p.date_of_birth,
                    p.gender,
                    p.uhid
                FROM patients p
                WHERE {where_clause}
                ORDER BY p.name
                LIMIT 20
                """,
                params,
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "whatsapp_number": row[2],
            "date_of_birth": row[3].isoformat() if row[3] else None,
            "gender": row[4],
            "uhid": row[5],
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
                patient.government_id,
            )

            if created_patient is None:
                raise HTTPException(
                    status_code=409,
                    detail="Patient with this WhatsApp number already exists",
                )

            # M8: warns, never blocks -- created_patient above is
            # already committed to being returned either way. See
            # app/services/patient_duplicate_detection.py for why this
            # only runs on this (staff-reviewed) registration path.
            created_patient["possible_duplicates"] = find_duplicate_candidates(
                cur, staff["hospital_id"], created_patient["id"]
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
                    government_id = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, name, whatsapp_number, date_of_birth, gender, hospital_id, government_id, uhid
                """,
                (
                    patient.name,
                    patient.whatsapp_number,
                    patient.date_of_birth,
                    patient.gender,
                    patient.government_id,
                    patient_id,
                ),
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
        "government_id": row[6],
        "uhid": row[7],
    }


# ---------------------------------------------------------------------
# M8: merge, unmerge, retired-UHID resolution.
# ---------------------------------------------------------------------

class MergePatientsRequest(BaseModel):
    retired_patient_id: int


@router.post("/{patient_id}/merge")
def merge_patients_endpoint(
    patient_id: int,
    body: MergePatientsRequest,
    staff: dict = Depends(get_current_staff),
):
    """patient_id survives; body.retired_patient_id is folded into it
    and never deleted -- see app/services/patient_merge.py."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                merge_id = merge_patients(
                    cur,
                    hospital_id=staff["hospital_id"],
                    surviving_patient_id=patient_id,
                    retired_patient_id=body.retired_patient_id,
                    staff_id=staff["id"],
                )
            except svc_exc.CannotMergePatientIntoItself:
                raise HTTPException(status_code=400, detail="Cannot merge a patient into itself")
            except svc_exc.PatientNotFound:
                raise HTTPException(status_code=404, detail="Patient not found")
            except svc_exc.PatientAlreadyMerged:
                raise HTTPException(
                    status_code=409,
                    detail="One of these patients has already been merged",
                )

    return {"merge_id": merge_id, "surviving_patient_id": patient_id, "retired_patient_id": body.retired_patient_id}


@router.post("/merges/{merge_id}/unmerge")
def unmerge_patients_endpoint(
    merge_id: int,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                unmerge_patients(cur, merge_id)
            except svc_exc.MergeNotFound:
                raise HTTPException(status_code=404, detail="Merge not found")
            except svc_exc.UnmergeNotPermitted:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot unmerge: a clinical record has been created for the "
                    "surviving patient since the merge",
                )

    return {"merge_id": merge_id, "unmerged": True}


@router.get("/by-uhid/{uhid}")
def get_patient_by_uhid(
    uhid: str,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            resolved = resolve_patient_by_uhid(cur, staff["hospital_id"], uhid)

    if resolved is None:
        raise HTTPException(status_code=404, detail="No patient found for this UHID")

    return resolved


@router.get("/{patient_id}/timeline")
def get_patient_timeline(
    patient_id: int,
    staff: dict = Depends(get_current_staff),
):
    """
    OPD/HIMS master spec Phase 10 (section 44): Patient 360 / unified
    timeline -- one visit (encounter) per entry, most recent first, each
    carrying everything that happened during it (vitals, consultation,
    orders + results, prescription + dispensing, billing). Read-only,
    same bare get_current_staff tier as every other endpoint in this
    router (see migrations/0031_rbac_decomposition.sql's own note on
    which patients.py endpoints it deliberately left ungated).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return get_patient_timeline_service(cur, patient_id, hospital_id=staff["hospital_id"])
            except svc_exc.PatientNotFound:
                raise HTTPException(status_code=404, detail="Patient not found")


class DuplicateReviewDecision(BaseModel):
    decision: str


@router.patch("/duplicate-reviews/{review_id}")
def decide_duplicate_review_endpoint(
    review_id: int,
    body: DuplicateReviewDecision,
    staff: dict = Depends(get_current_staff),
):
    if body.decision not in ("CONFIRMED_DUPLICATE", "NOT_DUPLICATE"):
        raise HTTPException(
            status_code=422,
            detail="decision must be one of CONFIRMED_DUPLICATE, NOT_DUPLICATE",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                decide_duplicate_review(cur, review_id, body.decision, staff["id"])
            except svc_exc.DuplicateReviewNotFound:
                raise HTTPException(
                    status_code=404,
                    detail="Duplicate review not found, or already decided",
                )

    return {"review_id": review_id, "decision": body.decision}
