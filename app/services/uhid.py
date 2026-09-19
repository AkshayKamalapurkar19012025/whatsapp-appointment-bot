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
