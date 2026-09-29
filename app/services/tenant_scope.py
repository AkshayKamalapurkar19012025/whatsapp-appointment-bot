"""
Tenant (hospital) scope lookups. Several existing services take a bare
appointment_id/doctor_id and never check which hospital owns it (see
docs/implementation/AI_AGENT_IMPLEMENTATION_AUDIT.md, finding 4). The AI
agent layer calls these first so an agent acting for hospital A can never
read or change hospital B's records, without touching those services.
"""


def appointment_hospital_id(cur, appointment_id: int) -> int | None:
    cur.execute("SELECT hospital_id FROM appointments WHERE id = %s", (appointment_id,))
    row = cur.fetchone()
    return row[0] if row else None


def appointment_patient_id(cur, appointment_id: int) -> int | None:
    cur.execute("SELECT patient_id FROM appointments WHERE id = %s", (appointment_id,))
    row = cur.fetchone()
    return row[0] if row else None


def doctor_hospital_id(cur, doctor_id: int) -> int | None:
    cur.execute("SELECT hospital_id FROM doctors WHERE id = %s", (doctor_id,))
    row = cur.fetchone()
    return row[0] if row else None
