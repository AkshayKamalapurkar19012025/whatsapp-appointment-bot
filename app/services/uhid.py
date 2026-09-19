"""
M6 (HospitalOS build plan): UHID generation -- see
migrations/0027_patient_uhid.sql for the schema and why a plain counter
table is used instead of a native Postgres sequence per hospital.
"""


def generate_uhid(cur, hospital_id: int) -> str:
    """
    Atomically hands out this hospital's next UHID and returns it,
    formatted <hospital code>-<6-digit sequence> (e.g. MAIN-000001).

    Safe under concurrent callers: the UPDATE below is a single-row
    write, serialized by Postgres's own row-level locking -- two
    concurrent callers for the same hospital_id simply queue, neither
    ever observing the value the other just consumed.
    """
    cur.execute(
        """
        UPDATE hospital_uhid_counters
        SET next_seq = next_seq + 1
        WHERE hospital_id = %s
        RETURNING next_seq - 1
        """,
        (hospital_id,),
    )
    row = cur.fetchone()

    if row is None:
        raise ValueError(f"No hospital_uhid_counters row for hospital_id={hospital_id}")

    sequence = row[0]

    cur.execute("SELECT code FROM hospitals WHERE id = %s", (hospital_id,))
    code = cur.fetchone()[0]

    return f"{code}-{sequence:06d}"


def resolve_patient_by_uhid(cur, hospital_id: int, uhid: str):
    """
    M8: looks up a patient by UHID. If the matched row was retired by a
    merge (patients.merged_into_id set -- see app/services/patient_merge.py),
    follows that pointer and returns the surviving patient instead, with
    retired=True and the original uhid under retired_uhid, so the caller
    can show a note ("this UHID was merged into <survivor>") instead of
    silently pretending the old identity still stands on its own. An old
    wristband or printed report with a since-merged UHID on it still
    finds the right person this way.

    Returns None if no patient in this hospital has ever held this UHID.
    """
    cur.execute(
        "SELECT id, name, uhid, merged_into_id FROM patients WHERE hospital_id = %s AND uhid = %s",
        (hospital_id, uhid),
    )
    row = cur.fetchone()

    if row is None:
        return None

    patient_id, name, matched_uhid, merged_into_id = row

    if merged_into_id is None:
        return {"id": patient_id, "name": name, "uhid": matched_uhid, "retired": False}

    cur.execute("SELECT id, name, uhid FROM patients WHERE id = %s", (merged_into_id,))
    survivor_id, survivor_name, survivor_uhid = cur.fetchone()

    return {
        "id": survivor_id,
        "name": survivor_name,
        "uhid": survivor_uhid,
        "retired": True,
        "retired_uhid": matched_uhid,
    }
