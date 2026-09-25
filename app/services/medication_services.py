"""
Medication Master (OPD/HIMS interoperability master prompt Phase 5,
migrations/0054_medication_master.sql): the internal canonical
medication identity prescription_items and pharmacy_stock both
optionally reference via their own medication_id column.

Deliberately small: generic_name/brand_name/strength/dosage_form/
default_route/active, no external terminology code column -- see
docs/OPD_HIMS_STANDARDS_READINESS.md S16 for why those aren't added
here. Search reuses the same pg_trgm approach already proven for
patient name search (app/services/patient_duplicate_detection.py),
not a second search strategy.
"""

from app.services.exceptions import DuplicateMedication, MedicationInactive, MedicationNotFound

import psycopg

_MEDICATION_COLUMNS = (
    "id", "generic_name", "brand_name", "strength", "dosage_form",
    "default_route", "active", "created_by", "created_at", "updated_at",
)


def _medication_row_to_dict(row) -> dict:
    d = dict(zip(_MEDICATION_COLUMNS, row))
    d["created_at"] = d["created_at"].isoformat()
    d["updated_at"] = d["updated_at"].isoformat()
    # Presentation only -- computed here, not stored, since it's a
    # formatting choice (docs/OPD_HIMS_STANDARDS_READINESS.md S5 kept
    # display_name out of the schema for the same reason). Every caller
    # (search dropdown, admin list) reads this instead of assembling its
    # own copy of the same three-field join.
    parts = [d["generic_name"]]
    if d["brand_name"]:
        parts.append(f'({d["brand_name"]})')
    if d["strength"]:
        parts.append(d["strength"])
    if d["dosage_form"]:
        parts.append(d["dosage_form"])
    d["display_name"] = " ".join(parts)
    return d


def list_medications_service(cur, *, search: str | None = None, active_only: bool = True) -> list[dict]:
    conditions = []
    params: list = []
    if active_only:
        conditions.append("active = TRUE")
    if search and search.strip():
        conditions.append("(generic_name ILIKE %s OR brand_name ILIKE %s OR strength ILIKE %s)")
        like = f"%{search.strip()}%"
        params.extend([like, like, like])

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    cur.execute(
        f"""
        SELECT {", ".join(_MEDICATION_COLUMNS)} FROM medications
        {where_clause}
        ORDER BY generic_name, strength NULLS FIRST
        LIMIT 50
        """,
        params,
    )
    return [_medication_row_to_dict(row) for row in cur.fetchall()]


def get_medication_service(cur, medication_id: int) -> dict:
    cur.execute(
        f"SELECT {', '.join(_MEDICATION_COLUMNS)} FROM medications WHERE id = %s",
        (medication_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise MedicationNotFound()
    return _medication_row_to_dict(row)


def require_active_medication(cur, medication_id: int) -> dict:
    """Shared by every prescription_items/pharmacy_stock write that's
    given a medication_id -- an inactive medication can't be newly
    referenced (existing references keep working; see migrations/
    0054's own comment on medication_id)."""
    medication = get_medication_service(cur, medication_id)
    if not medication["active"]:
        raise MedicationInactive()
    return medication


def create_medication_service(
    cur,
    *,
    staff_id: int,
    generic_name: str,
    brand_name: str | None = None,
    strength: str | None = None,
    dosage_form: str | None = None,
    default_route: str | None = None,
) -> dict:
    try:
        cur.execute(
            f"""
            INSERT INTO medications (generic_name, brand_name, strength, dosage_form, default_route, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {", ".join(_MEDICATION_COLUMNS)}
            """,
            (generic_name.strip(), brand_name, strength, dosage_form, default_route, staff_id),
        )
    except psycopg.errors.UniqueViolation:
        raise DuplicateMedication()

    return _medication_row_to_dict(cur.fetchone())


def update_medication_service(
    cur,
    medication_id: int,
    *,
    generic_name: str,
    brand_name: str | None = None,
    strength: str | None = None,
    dosage_form: str | None = None,
    default_route: str | None = None,
) -> dict:
    get_medication_service(cur, medication_id)  # 404s if missing
    try:
        cur.execute(
            f"""
            UPDATE medications
            SET generic_name = %s, brand_name = %s, strength = %s, dosage_form = %s,
                default_route = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING {", ".join(_MEDICATION_COLUMNS)}
            """,
            (generic_name.strip(), brand_name, strength, dosage_form, default_route, medication_id),
        )
    except psycopg.errors.UniqueViolation:
        raise DuplicateMedication()

    return _medication_row_to_dict(cur.fetchone())


def set_medication_active_service(cur, medication_id: int, *, active: bool) -> dict:
    get_medication_service(cur, medication_id)  # 404s if missing
    cur.execute(
        f"""
        UPDATE medications SET active = %s, updated_at = NOW()
        WHERE id = %s
        RETURNING {", ".join(_MEDICATION_COLUMNS)}
        """,
        (active, medication_id),
    )
    return _medication_row_to_dict(cur.fetchone())
