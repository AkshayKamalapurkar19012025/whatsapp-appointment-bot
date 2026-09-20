"""
UHID lookup. Generation itself needs no code here: uhid
(migrations/0024_patient_uhid.sql, from main) is a Postgres
GENERATED ALWAYS AS (...) STORED column derived from patients.id, so
every row has one the instant it's inserted. This module originally
also carried M6's own generate_uhid() (a per-hospital counter table,
<hospital code>-<sequence> format) -- dropped when this branch merged
with main's already-shipped, simpler GENERATED-column design, which
serves the same need (a permanent, human-facing patient identifier)
without a second table or an extra write per patient. See this
branch's merge commit for the reconciliation.
"""


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
