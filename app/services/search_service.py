"""
Global search (OPD/HIMS master spec section 14): "no cross-entity
search bar (UHID/name/mobile/appointment/encounter/order/bill from one
box) exists anywhere in the frontend -- only per-page search fields
scoped to that page's own list" (docs/OPD_HIMS_MASTER_SPEC_AUDIT.md
section 14-15).

Scoped deliberately to patients and appointments, not a sixth copy of
free-text search logic across encounters/orders/bills too: Patient 360
(app/services/patient_timeline_service.py, Phase 10) already shows
every encounter/order/result/prescription/bill for a patient in one
place, so "find the patient, then see everything" -- one search, one
click -- is a real, complete answer to "search for X" for those entity
types, not a gap papered over. Appointments get their own branch
because that's the one thing staff plausibly search for that ISN'T
reachable by first finding a patient (e.g. "which doctor is Priya
booked with today").
"""

_PATIENT_RESULT_COLUMNS = ("id", "name", "whatsapp_number", "uhid", "date_of_birth", "gender")


def _patient_row_to_dict(row) -> dict:
    d = dict(zip(_PATIENT_RESULT_COLUMNS, row))
    d["date_of_birth"] = d["date_of_birth"].isoformat() if d["date_of_birth"] else None
    return d


_RESULT_LIMIT = 8


def global_search_service(cur, query: str, *, hospital_id: int) -> dict:
    needle = f"%{query.strip()}%"

    cur.execute(
        f"""
        SELECT {", ".join(_PATIENT_RESULT_COLUMNS)}
        FROM patients
        WHERE hospital_id = %s
          AND (name ILIKE %s OR whatsapp_number ILIKE %s OR uhid ILIKE %s)
        ORDER BY name
        LIMIT %s
        """,
        (hospital_id, needle, needle, needle, _RESULT_LIMIT),
    )
    patients = [_patient_row_to_dict(row) for row in cur.fetchall()]

    # Matched by patient or doctor name -- covers both "find this
    # patient's appointment" and "find who's on Dr. X's list today"
    # without a separate mode switch. Most-recent-first, no date bound:
    # an unbounded LIMIT 8 keeps this a fast, index-friendly query even
    # over a large appointments table (the ILIKE itself, not a date
    # range, is what actually narrows the result set here).
    cur.execute(
        """
        SELECT a.id, a.start_at, a.status, a.token_number,
               p.id, p.name, d.id, d.name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        JOIN doctors d ON d.id = a.doctor_id
        WHERE a.hospital_id = %s
          AND (p.name ILIKE %s OR d.name ILIKE %s)
        ORDER BY a.start_at DESC
        LIMIT %s
        """,
        (hospital_id, needle, needle, _RESULT_LIMIT),
    )
    appointments = [
        {
            "id": row[0],
            "start_at": row[1].isoformat(),
            "status": row[2],
            "token_number": row[3],
            "patient_id": row[4],
            "patient_name": row[5],
            "doctor_id": row[6],
            "doctor_name": row[7],
        }
        for row in cur.fetchall()
    ]

    return {"patients": patients, "appointments": appointments}
