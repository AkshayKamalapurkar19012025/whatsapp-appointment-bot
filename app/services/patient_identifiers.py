"""
M4-M5 (HospitalOS build plan): dual-write patient_identifiers alongside
the authoritative patients.whatsapp_number column, and the single new
read path (resolve_patient_by_identifier) readers migrate onto one at a
time -- see migrations/0029_patient_identifiers.sql for the schema and
why it's structured the way it is.

During M4-M5 the column stays authoritative: write_phone_identifier is
called from every patient create/update alongside the column write
(app/api/patients.py's insert_patient and update_patient,
app/services/patient_auth.py's verify_otp), and resolve_patient_by_identifier
is being adopted by the three existing phone-number readers one at a
time, each its own commit -- see that phase's report for which have and
haven't moved yet.
"""

import logging

logger = logging.getLogger(__name__)

# No authenticated actor (staff, or an already-resolved patient) exists
# yet at the two call sites that need this -- web OTP verify and the
# WhatsApp flow's own get_patient(), both keyed only on a phone number
# before any patient/session exists. There is exactly one hospital in
# this deployment (see migrations/0027), so hardcoding it here is
# equivalent to every other unscoped read/write in the codebase today,
# not a new limitation. Revisit once those two entry points have a real
# way to resolve which hospital an incoming phone number belongs to
# (e.g. per-hospital WhatsApp business numbers, or a hospital code in
# the OTP request).
DEFAULT_HOSPITAL_ID = 1


def write_phone_identifier(cur, *, hospital_id: int, patient_id: int, whatsapp_number: str) -> None:
    """
    Idempotently record whatsapp_number as this patient's primary PHONE
    identifier: inserts one if none exists yet, or updates the value in
    place if the patient's phone number changed (the admin
    PATCH /api/patients/{id} endpoint is the only place that happens
    today). Write-only -- never call this from a lookup path.
    """
    cur.execute(
        """
        INSERT INTO patient_identifiers (hospital_id, patient_id, kind, value, is_primary)
        VALUES (%s, %s, 'PHONE', %s, TRUE)
        ON CONFLICT (patient_id, kind) WHERE is_primary = TRUE
        DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """,
        (hospital_id, patient_id, whatsapp_number),
    )


def resolve_patient_by_identifier(cur, hospital_id: int, kind: str, value: str):
    """
    The single new read path for M4-M5's migrated readers. Returns the
    same {"id", "name", "whatsapp_number"} shape every existing direct
    patients-table lookup already returns, or None if nothing matches.

    More than one patient can share the same identifier value -- a
    family sharing a phone number is real, expected data, not
    corruption (see M8, which ships the disambiguation UI this is a
    placeholder for). Returns the is_primary match; if more than one row
    matches, logs a warning rather than guessing further -- that log is
    the signal for whether shared numbers are common enough in real data
    to justify designing M8 around, before that design gets committed to.
    """
    cur.execute(
        """
        SELECT p.id, p.name, p.whatsapp_number, pi.is_primary, pi.id
        FROM patient_identifiers pi
        JOIN patients p ON p.id = pi.patient_id
        WHERE pi.hospital_id = %s
          AND pi.kind = %s
          AND pi.value = %s
        ORDER BY pi.is_primary DESC, pi.id ASC
        """,
        (hospital_id, kind, value),
    )
    rows = cur.fetchall()

    if not rows:
        return None

    if len(rows) > 1:
        logger.warning(
            "resolve_patient_by_identifier: %d patients matched kind=%r value=%r "
            "in hospital_id=%r -- returning the is_primary match. A shared "
            "identifier is expected data, not corruption; see M4-M5's report.",
            len(rows),
            kind,
            value,
            hospital_id,
        )

    patient_id, name, whatsapp_number, _, _ = rows[0]

    return {
        "id": patient_id,
        "name": name,
        "whatsapp_number": whatsapp_number,
    }
