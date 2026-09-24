"""
Patient allergy list (master spec section 91's clinical-safety UX
requirement). See migrations/0042_patient_allergies.sql for why this
is one row per allergy, not a free-text column, and why retraction
deactivates rather than deletes.
"""

from app.services.exceptions import AllergyNotFound, PatientNotFound

_ALLERGY_COLUMNS = (
    "id", "patient_id", "allergen", "reaction", "severity", "active",
    "recorded_by", "recorded_at", "resolved_by", "resolved_reason", "resolved_at",
)


def _row_to_dict(row) -> dict:
    d = dict(zip(_ALLERGY_COLUMNS, row))
    d["recorded_at"] = d["recorded_at"].isoformat()
    d["resolved_at"] = d["resolved_at"].isoformat() if d["resolved_at"] else None
    return d


def _patient_exists(cur, patient_id: int, *, hospital_id: int) -> None:
    cur.execute("SELECT 1 FROM patients WHERE id = %s AND hospital_id = %s", (patient_id, hospital_id))
    if cur.fetchone() is None:
        raise PatientNotFound()


def list_patient_allergies_service(cur, patient_id: int, *, hospital_id: int, include_resolved: bool = False):
    _patient_exists(cur, patient_id, hospital_id=hospital_id)
    where = "" if include_resolved else "AND active = TRUE"
    cur.execute(
        f"""
        SELECT {', '.join(_ALLERGY_COLUMNS)} FROM patient_allergies
        WHERE patient_id = %s {where}
        ORDER BY recorded_at DESC
        """,
        (patient_id,),
    )
    return [_row_to_dict(row) for row in cur.fetchall()]


def add_patient_allergy_service(
    cur, patient_id: int, *, staff_id: int, hospital_id: int,
    allergen: str, reaction: str | None = None, severity: str | None = None,
):
    _patient_exists(cur, patient_id, hospital_id=hospital_id)
    cur.execute(
        f"""
        INSERT INTO patient_allergies (patient_id, allergen, reaction, severity, recorded_by)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING {', '.join(_ALLERGY_COLUMNS)}
        """,
        (patient_id, allergen, reaction, severity, staff_id),
    )
    return _row_to_dict(cur.fetchone())


def resolve_patient_allergy_service(cur, patient_id: int, allergy_id: int, *, staff_id: int, hospital_id: int, reason: str):
    _patient_exists(cur, patient_id, hospital_id=hospital_id)
    cur.execute(
        "SELECT active FROM patient_allergies WHERE id = %s AND patient_id = %s FOR UPDATE",
        (allergy_id, patient_id),
    )
    row = cur.fetchone()
    if row is None:
        raise AllergyNotFound()

    cur.execute(
        f"""
        UPDATE patient_allergies
        SET active = FALSE, resolved_by = %s, resolved_reason = %s, resolved_at = NOW()
        WHERE id = %s
        RETURNING {', '.join(_ALLERGY_COLUMNS)}
        """,
        (staff_id, reason, allergy_id),
    )
    return _row_to_dict(cur.fetchone())
