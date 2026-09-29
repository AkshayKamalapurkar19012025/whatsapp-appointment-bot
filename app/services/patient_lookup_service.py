"""
Read-only patient lookups, extracted verbatim from app/api/patients.py's
GET /patients/search and GET /patients/{id} handlers so the AI agent
tools (app/agent/tools) and those routes share ONE implementation rather
than the agent layer growing a second copy of the queries. The routes
call these with the arguments they always effectively used; behavior is
unchanged.

hospital_id is optional on search_patients_service purely to keep the
existing route's behavior identical (it has never filtered by tenant --
see docs/implementation/AI_AGENT_IMPLEMENTATION_AUDIT.md, finding 4);
the agent tools always pass it.
"""

from datetime import date

PATIENT_OPTIONAL_DETAIL_COLUMNS = (
    "email", "alternate_whatsapp_number", "address_line", "city",
    "state", "pincode", "emergency_contact_name", "emergency_contact_phone",
    "blood_group",
)

SEARCH_RESULT_LIMIT = 20


def search_patients_service(
    cur,
    *,
    q: str | None = None,
    dob: date | None = None,
    hospital_id: int | None = None,
) -> list[dict]:
    """q matches name/whatsapp_number/uhid by case-insensitive substring;
    dob narrows to an exact date of birth. Capped at SEARCH_RESULT_LIMIT.
    Callers must supply at least one of q/dob (the route turns "neither"
    into a 400; this raises ValueError so no caller can list every
    patient by accident)."""
    if not q and not dob:
        raise ValueError("search_patients_service requires q and/or dob")

    conditions = []
    params: list = []

    if hospital_id is not None:
        conditions.append("p.hospital_id = %s")
        params.append(hospital_id)

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
        LIMIT {SEARCH_RESULT_LIMIT}
        """,
        params,
    )

    return [
        {
            "id": row[0],
            "name": row[1],
            "whatsapp_number": row[2],
            "date_of_birth": row[3].isoformat() if row[3] else None,
            "gender": row[4],
            "uhid": row[5],
        }
        for row in cur.fetchall()
    ]


def get_patient_service(cur, patient_id: int, *, hospital_id: int) -> dict | None:
    """A single patient's full record, or None if no such patient exists
    in this hospital."""
    cur.execute(
        f"""
        SELECT id, name, whatsapp_number, date_of_birth, gender, government_id, uhid, created_at,
               {', '.join(PATIENT_OPTIONAL_DETAIL_COLUMNS)}
        FROM patients
        WHERE id = %s AND hospital_id = %s
        """,
        (patient_id, hospital_id),
    )
    row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "whatsapp_number": row[2],
        "date_of_birth": row[3].isoformat() if row[3] else None,
        "gender": row[4],
        "government_id": row[5],
        "uhid": row[6],
        "registered_at": row[7].isoformat(),
        **dict(zip(PATIENT_OPTIONAL_DETAIL_COLUMNS, row[8:])),
    }
