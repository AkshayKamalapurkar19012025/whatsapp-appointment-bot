"""
M8 (HospitalOS build plan): patient merge and unmerge -- see
migrations/0028_patient_merge_and_duplicate_detection.sql for the schema
and the two gaps between this work order's text and what exists in this
codebase today. In particular: merge_patients() rewrites exactly the
three patient-scoped tables that exist today (appointments, encounters,
patient_identifiers), not the full list this work order's text
eventually wants (orders/observations from M9, invoices from a later,
uncosted phase) -- adding a table there later is a small, additive
change to the same function, not a redesign.

merge_patients() never merges a patient that is already itself retired,
or into one that is -- see PatientAlreadyMerged. That guarantee is what
keeps patients.merged_into_id a direct, one-hop pointer at a genuinely
live patient forever: no merge chains to follow, ever.
"""

import json

from app.services.exceptions import (
    CannotMergePatientIntoItself,
    MergeNotFound,
    PatientAlreadyMerged,
    PatientNotFound,
    UnmergeNotPermitted,
)


def merge_patients(
    cur,
    *,
    hospital_id: int,
    surviving_patient_id: int,
    retired_patient_id: int,
    staff_id: int | None = None,
) -> int:
    """
    Merges retired_patient_id into surviving_patient_id: every
    appointment, encounter, and patient identifier that belonged to the
    retired patient now belongs to the surviving one. The retired
    patient's row is never deleted -- merged_into_id is set on it so a
    lookup by their old UHID/phone still resolves to the survivor (see
    app/services/uhid.py's resolve_patient_by_uhid).

    Returns the new patient_merges.id, needed to unmerge later.

    Locks both patient rows in id order first, not
    surviving-then-retired or vice versa -- so two concurrent merges
    never deadlock, the same discipline
    app/services/appointment_services.py's
    pg_advisory_xact_lock(doctor_id) already uses for scheduling.
    """
    if surviving_patient_id == retired_patient_id:
        raise CannotMergePatientIntoItself()

    cur.execute(
        """
        SELECT id, merged_into_id
        FROM patients
        WHERE id = ANY(%s)
        ORDER BY id
        FOR UPDATE
        """,
        (sorted((surviving_patient_id, retired_patient_id)),),
    )
    rows = {row[0]: row[1] for row in cur.fetchall()}

    if surviving_patient_id not in rows or retired_patient_id not in rows:
        raise PatientNotFound()

    if rows[surviving_patient_id] is not None or rows[retired_patient_id] is not None:
        raise PatientAlreadyMerged()

    affected: dict = {}

    # appointments, encounters: plain patient_id rewrite -- neither
    # table constrains patient_id uniqueness, so no collision is
    # possible.
    cur.execute("SELECT id FROM appointments WHERE patient_id = %s", (retired_patient_id,))
    affected["appointments"] = [row[0] for row in cur.fetchall()]
    if affected["appointments"]:
        cur.execute(
            "UPDATE appointments SET patient_id = %s WHERE patient_id = %s",
            (surviving_patient_id, retired_patient_id),
        )

    cur.execute("SELECT id FROM encounters WHERE patient_id = %s", (retired_patient_id,))
    affected["encounters"] = [row[0] for row in cur.fetchall()]
    if affected["encounters"]:
        cur.execute(
            "UPDATE encounters SET patient_id = %s WHERE patient_id = %s",
            (surviving_patient_id, retired_patient_id),
        )

    # patient_identifiers: rewriting patient_id can collide with
    # patient_identifiers_one_primary_per_kind if the survivor already
    # has a primary identifier of the same kind -- demote the retired
    # patient's own identifiers to non-primary first. Recorded in
    # affected (was_primary per row) so unmerge can restore exactly
    # which ones were primary, not just that they existed.
    cur.execute(
        "SELECT id, is_primary FROM patient_identifiers WHERE patient_id = %s",
        (retired_patient_id,),
    )
    identifier_rows = cur.fetchall()
    affected["patient_identifiers"] = [
        {"id": row[0], "was_primary": row[1]} for row in identifier_rows
    ]

    if identifier_rows:
        cur.execute(
            "UPDATE patient_identifiers SET is_primary = FALSE, updated_at = NOW() WHERE patient_id = %s",
            (retired_patient_id,),
        )
        cur.execute(
            "UPDATE patient_identifiers SET patient_id = %s WHERE patient_id = %s",
            (surviving_patient_id, retired_patient_id),
        )

    cur.execute(
        "UPDATE patients SET merged_into_id = %s, updated_at = NOW() WHERE id = %s",
        (surviving_patient_id, retired_patient_id),
    )

    cur.execute(
        """
        INSERT INTO patient_merges (
            hospital_id, surviving_patient_id, retired_patient_id, merged_by_staff_id, affected
        )
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (hospital_id, surviving_patient_id, retired_patient_id, staff_id, json.dumps(affected)),
    )
    return cur.fetchone()[0]


def unmerge_patients(cur, merge_id: int) -> None:
    """
    Reverses a merge exactly, replaying merge_patients' own affected
    record in reverse. Permitted only while no clinical record
    (appointment or encounter) has been created for the surviving
    patient since the merge -- there would be no way to tell whether
    such a record belongs to the surviving identity or the one being
    restored, so this refuses rather than guessing.
    """
    cur.execute(
        """
        SELECT surviving_patient_id, retired_patient_id, affected, created_at, unmerged_at
        FROM patient_merges
        WHERE id = %s
        FOR UPDATE
        """,
        (merge_id,),
    )
    row = cur.fetchone()

    if row is None:
        raise MergeNotFound()

    surviving_patient_id, retired_patient_id, affected, merged_at, unmerged_at = row

    if unmerged_at is not None:
        raise MergeNotFound()

    cur.execute(
        "SELECT count(*) FROM appointments WHERE patient_id = %s AND created_at > %s",
        (surviving_patient_id, merged_at),
    )
    if cur.fetchone()[0] > 0:
        raise UnmergeNotPermitted()

    cur.execute(
        "SELECT count(*) FROM encounters WHERE patient_id = %s AND created_at > %s",
        (surviving_patient_id, merged_at),
    )
    if cur.fetchone()[0] > 0:
        raise UnmergeNotPermitted()

    appointment_ids = affected.get("appointments", [])
    if appointment_ids:
        cur.execute(
            "UPDATE appointments SET patient_id = %s WHERE id = ANY(%s)",
            (retired_patient_id, appointment_ids),
        )

    encounter_ids = affected.get("encounters", [])
    if encounter_ids:
        cur.execute(
            "UPDATE encounters SET patient_id = %s WHERE id = ANY(%s)",
            (retired_patient_id, encounter_ids),
        )

    # One at a time, not a single bulk UPDATE: at most one row per kind
    # was ever primary (the original constraint already guaranteed
    # that), so restoring them individually never has two rows racing
    # to be "the" primary of the same kind at once.
    for entry in affected.get("patient_identifiers", []):
        cur.execute(
            "UPDATE patient_identifiers SET patient_id = %s, is_primary = %s, updated_at = NOW() WHERE id = %s",
            (retired_patient_id, entry["was_primary"], entry["id"]),
        )

    cur.execute(
        "UPDATE patients SET merged_into_id = NULL, updated_at = NOW() WHERE id = %s",
        (retired_patient_id,),
    )

    cur.execute("UPDATE patient_merges SET unmerged_at = NOW() WHERE id = %s", (merge_id,))
