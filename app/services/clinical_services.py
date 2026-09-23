"""
Triage/vitals and doctor consultation (OPD/HIMS master spec Phase 5),
built on the encounter foundation from migrations/0028_encounters.sql
and app/services/appointment_services.py.

Write gate: every write here (recording vitals, creating/saving a
consultation draft, completing one) requires the underlying
appointment's status to be CHECKED_IN -- not encounters.status. This is
a deliberate choice, not an oversight: encounters.status flips to CLOSED
the moment the appointment reaches a terminal status (see
_close_encounter_for_appointment), including COMPLETED, which today is
set by a single front-desk "Mark completed" click on the queue
(app/api/appointments.py's /complete, unchanged by this phase). Gating
clinical writes on encounters.status would make that same click
silently lock a doctor out of finishing their documentation if staff
click it first -- gating on appointments.status = CHECKED_IN instead
mirrors the exact same condition record_payment_service and every other
CHECKED_IN-scoped action in this codebase already uses, and makes the
real invariant explicit: clinical documentation happens while the
patient is actually present (CHECKED_IN), and stops being writable once
the visit is closed out, matching the master spec's own Visit Completion
ordering (consultation completed comes before Complete OPD Visit).
Reads (viewing vitals/a consultation after the visit closed) are never
gated -- history doesn't stop being visible because the episode ended.
"""

from app.services.exceptions import (
    AppointmentNotFound,
    EncounterNotFound,
    EncounterClosed,
    ConsultationAlreadyCompleted,
    ConsultationIncomplete,
    ConsultationNotAmendable,
)


def get_appointment_status_and_doctor(cur, appointment_id: int):
    """Shared by every clinical service module (this one and
    app/services/order_services.py, Phase 6) that needs the same
    CHECKED_IN write-gate check -- not module-private any more now that
    a second module needs it, but still meant to be called from within
    this package, not from an API router directly."""
    cur.execute(
        "SELECT status, doctor_id FROM appointments WHERE id = %s",
        (appointment_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise AppointmentNotFound()
    return {"status": row[0], "doctor_id": row[1]}


def get_encounter_id_for_appointment(cur, appointment_id: int) -> int:
    """Shared by every clinical service module -- see
    get_appointment_status_and_doctor's docstring above.

    Looks up via appointments.encounter_id (the forward FK
    migrations/0028_encounters.sql actually creates), not a reverse
    encounters.appointment_id column -- that column doesn't exist on
    this table; every OPD encounter is found through the appointment
    that opened it instead."""
    cur.execute(
        "SELECT encounter_id FROM appointments WHERE id = %s",
        (appointment_id,),
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        raise EncounterNotFound()
    return row[0]


def get_encounter_summary_service(cur, appointment_id: int):
    """Everything a consultation/triage screen needs to render its
    patient header on a fresh page load, in one round trip: the
    encounter itself, plus enough of the patient/doctor/appointment to
    show identity and status without a second request."""
    appointment = get_appointment_status_and_doctor(cur, appointment_id)
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        """
        SELECT
            e.id, e.status, e.started_at, e.closed_at,
            p.id, p.name, p.uhid, p.date_of_birth, p.gender,
            d.id, d.name,
            a.token_number, a.start_at
        FROM encounters e
        JOIN patients p ON p.id = e.patient_id
        JOIN appointments a ON a.id = %s
        JOIN doctors d ON d.id = a.doctor_id
        WHERE e.id = %s
        """,
        (appointment_id, encounter_id),
    )
    row = cur.fetchone()

    return {
        "encounter_id": row[0],
        "encounter_status": row[1],
        "opened_at": row[2].isoformat(),
        "closed_at": row[3].isoformat() if row[3] else None,
        "patient_id": row[4],
        "patient_name": row[5],
        "patient_uhid": row[6],
        "patient_date_of_birth": row[7].isoformat() if row[7] else None,
        "patient_gender": row[8],
        "doctor_id": row[9],
        "doctor_name": row[10],
        "token_number": row[11],
        "appointment_id": appointment_id,
        "appointment_status": appointment["status"],
        "start_at": row[12].isoformat(),
    }


# ---------------------------------------------------------------------
# Vitals
# ---------------------------------------------------------------------

_VITALS_COLUMNS = (
    "id", "encounter_id", "recorded_by", "bp_systolic", "bp_diastolic",
    "pulse", "temperature_celsius", "spo2", "respiratory_rate",
    "weight_kg", "height_cm", "bmi", "pain_score", "chief_complaint",
    "priority", "nursing_notes", "recorded_at",
)


def _vitals_row_to_dict(row) -> dict:
    d = dict(zip(_VITALS_COLUMNS, row))
    d["recorded_at"] = d["recorded_at"].isoformat()
    return d


def record_vitals_service(
    cur,
    appointment_id: int,
    *,
    staff_id: int,
    bp_systolic=None,
    bp_diastolic=None,
    pulse=None,
    temperature_celsius=None,
    spo2=None,
    respiratory_rate=None,
    weight_kg=None,
    height_cm=None,
    pain_score=None,
    chief_complaint=None,
    priority="ROUTINE",
    nursing_notes=None,
):
    appointment = get_appointment_status_and_doctor(cur, appointment_id)
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    if appointment["status"] != "CHECKED_IN":
        raise EncounterClosed()

    cur.execute(
        f"""
        INSERT INTO vitals (
            encounter_id, recorded_by, bp_systolic, bp_diastolic, pulse,
            temperature_celsius, spo2, respiratory_rate, weight_kg,
            height_cm, pain_score, chief_complaint, priority, nursing_notes
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {", ".join(_VITALS_COLUMNS)}
        """,
        (
            encounter_id, staff_id, bp_systolic, bp_diastolic, pulse,
            temperature_celsius, spo2, respiratory_rate, weight_kg,
            height_cm, pain_score, chief_complaint, priority, nursing_notes,
        ),
    )

    return _vitals_row_to_dict(cur.fetchone())


def get_latest_vitals_service(cur, appointment_id: int):
    """The most recent vitals row for this appointment's encounter, or
    None if triage hasn't happened yet. Never gated on status -- viewing
    a patient's last-recorded vitals is always allowed."""
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        f"""
        SELECT {", ".join(_VITALS_COLUMNS)}
        FROM vitals
        WHERE encounter_id = %s
        ORDER BY recorded_at DESC
        LIMIT 1
        """,
        (encounter_id,),
    )
    row = cur.fetchone()
    return _vitals_row_to_dict(row) if row else None


# ---------------------------------------------------------------------
# Consultation
# ---------------------------------------------------------------------

_CONSULTATION_COLUMNS = (
    "id", "encounter_id", "doctor_id", "status", "chief_complaint",
    "history_notes", "examination_notes", "diagnosis", "clinical_notes",
    "follow_up_date", "follow_up_reason", "disposition", "disposition_notes",
    "started_at", "completed_at",
)


def _consultation_row_to_dict(row) -> dict:
    d = dict(zip(_CONSULTATION_COLUMNS, row))
    d["started_at"] = d["started_at"].isoformat()
    d["completed_at"] = d["completed_at"].isoformat() if d["completed_at"] else None
    d["follow_up_date"] = d["follow_up_date"].isoformat() if d["follow_up_date"] else None
    return d


def get_or_create_consultation_service(cur, appointment_id: int, *, staff_id: int):
    """Ensures a DRAFT consultation row exists for this appointment's
    encounter and returns it -- called when the consultation workspace
    first loads. Reading an already-existing consultation is never
    gated on status (so a COMPLETED consultation, or one whose visit has
    since closed out, is still viewable); creating a new one requires
    the patient to actually be CHECKED_IN, same reasoning as every write
    in this module (see module docstring)."""
    appointment = get_appointment_status_and_doctor(cur, appointment_id)
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        f"SELECT {', '.join(_CONSULTATION_COLUMNS)} FROM consultations WHERE encounter_id = %s",
        (encounter_id,),
    )
    row = cur.fetchone()
    if row is not None:
        return _consultation_row_to_dict(row)

    if appointment["status"] != "CHECKED_IN":
        raise EncounterClosed()

    cur.execute(
        f"""
        INSERT INTO consultations (encounter_id, doctor_id, created_by)
        VALUES (%s, %s, %s)
        ON CONFLICT (encounter_id) DO NOTHING
        RETURNING {", ".join(_CONSULTATION_COLUMNS)}
        """,
        (encounter_id, appointment["doctor_id"], staff_id),
    )
    row = cur.fetchone()
    if row is None:
        # Lost a race with a concurrent create for the same encounter --
        # the conflicting INSERT already committed a row, fetch it.
        cur.execute(
            f"SELECT {', '.join(_CONSULTATION_COLUMNS)} FROM consultations WHERE encounter_id = %s",
            (encounter_id,),
        )
        row = cur.fetchone()

    return _consultation_row_to_dict(row)


def save_consultation_draft_service(
    cur,
    appointment_id: int,
    *,
    staff_id: int,
    chief_complaint=None,
    history_notes=None,
    examination_notes=None,
    diagnosis=None,
    clinical_notes=None,
    follow_up_date=None,
    follow_up_reason=None,
    disposition=None,
    disposition_notes=None,
):
    """Full-form save (every field is set to exactly what's passed, not
    merged field-by-field) -- the consultation workspace always submits
    its whole current form state, so there's no ambiguity between "field
    left out" and "field cleared" to reconcile here."""
    appointment = get_appointment_status_and_doctor(cur, appointment_id)
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        "SELECT id, status FROM consultations WHERE encounter_id = %s",
        (encounter_id,),
    )
    existing = cur.fetchone()

    if existing is not None and existing[1] == "COMPLETED":
        raise ConsultationAlreadyCompleted()

    if appointment["status"] != "CHECKED_IN":
        raise EncounterClosed()

    if existing is None:
        cur.execute(
            f"""
            INSERT INTO consultations (
                encounter_id, doctor_id, chief_complaint, history_notes,
                examination_notes, diagnosis, clinical_notes,
                follow_up_date, follow_up_reason, disposition, disposition_notes, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {", ".join(_CONSULTATION_COLUMNS)}
            """,
            (
                encounter_id, appointment["doctor_id"], chief_complaint,
                history_notes, examination_notes, diagnosis, clinical_notes,
                follow_up_date, follow_up_reason, disposition, disposition_notes, staff_id,
            ),
        )
    else:
        cur.execute(
            f"""
            UPDATE consultations
            SET chief_complaint = %s,
                history_notes = %s,
                examination_notes = %s,
                diagnosis = %s,
                clinical_notes = %s,
                follow_up_date = %s,
                follow_up_reason = %s,
                disposition = %s,
                disposition_notes = %s,
                updated_by = %s,
                updated_at = NOW()
            WHERE id = %s
            RETURNING {", ".join(_CONSULTATION_COLUMNS)}
            """,
            (
                chief_complaint, history_notes, examination_notes, diagnosis,
                clinical_notes, follow_up_date, follow_up_reason,
                disposition, disposition_notes, staff_id,
                existing[0],
            ),
        )

    return _consultation_row_to_dict(cur.fetchone())


def complete_consultation_service(cur, appointment_id: int, *, staff_id: int):
    """Marks the consultation COMPLETED. Requires chief_complaint and
    diagnosis to already be non-empty -- the one clinical-safety floor
    this phase enforces (a consultation can't be signed off with nothing
    actually documented); every other field stays optional, matching how
    little the master spec itself mandates as strictly required beyond
    that pair."""
    appointment = get_appointment_status_and_doctor(cur, appointment_id)
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        "SELECT id, status, chief_complaint, diagnosis FROM consultations WHERE encounter_id = %s",
        (encounter_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise EncounterNotFound()

    consultation_id, status, chief_complaint, diagnosis = row

    if status == "COMPLETED":
        raise ConsultationAlreadyCompleted()

    if appointment["status"] != "CHECKED_IN":
        raise EncounterClosed()

    if not (chief_complaint and chief_complaint.strip()) or not (diagnosis and diagnosis.strip()):
        raise ConsultationIncomplete()

    cur.execute(
        f"""
        UPDATE consultations
        SET status = 'COMPLETED',
            completed_at = NOW(),
            updated_by = %s,
            updated_at = NOW()
        WHERE id = %s
        RETURNING {", ".join(_CONSULTATION_COLUMNS)}
        """,
        (staff_id, consultation_id),
    )

    return _consultation_row_to_dict(cur.fetchone())


# ---------------------------------------------------------------------
# Amendment (master spec section 70) -- correcting a COMPLETED
# consultation. Deliberately NOT gated on CHECKED_IN like every write
# above: an amendment exists specifically to correct a record *after*
# completion, which in practice usually means after the visit -- and
# often the whole encounter -- has already closed. Gating it the same
# way as first-time documentation would make it unusable for the one
# thing it's for. Gated on RBAC instead (consultation.amend,
# ADMIN-only today) -- a materially more sensitive action than routine
# documentation, so it gets its own permission rather than piggybacking
# on the CHECKED_IN check every other write here relies on.
# ---------------------------------------------------------------------

_AMENDMENT_COLUMNS = (
    "id", "consultation_id", "previous_chief_complaint", "previous_history_notes",
    "previous_examination_notes", "previous_diagnosis", "previous_clinical_notes",
    "previous_follow_up_date", "previous_follow_up_reason",
    "previous_disposition", "previous_disposition_notes",
    "reason", "amended_by", "amended_at",
)


def _amendment_row_to_dict(row) -> dict:
    d = dict(zip(_AMENDMENT_COLUMNS, row))
    d["previous_follow_up_date"] = d["previous_follow_up_date"].isoformat() if d["previous_follow_up_date"] else None
    d["amended_at"] = d["amended_at"].isoformat()
    return d


def amend_consultation_service(
    cur,
    appointment_id: int,
    *,
    staff_id: int,
    reason: str,
    chief_complaint=None,
    history_notes=None,
    examination_notes=None,
    diagnosis=None,
    clinical_notes=None,
    follow_up_date=None,
    follow_up_reason=None,
    disposition=None,
    disposition_notes=None,
):
    """Same "full-form save" semantics as save_consultation_draft_service
    -- every field set to exactly what's passed -- but only for a
    COMPLETED consultation, and only after archiving its pre-amendment
    values. Keeps the same clinical-safety floor complete_consultation_
    service itself enforces: an amendment can't blank out chief
    complaint or diagnosis either."""
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        """
        SELECT id, status, chief_complaint, history_notes, examination_notes,
               diagnosis, clinical_notes, follow_up_date, follow_up_reason,
               disposition, disposition_notes
        FROM consultations WHERE encounter_id = %s FOR UPDATE
        """,
        (encounter_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise EncounterNotFound()

    (consultation_id, status, prev_cc, prev_hn, prev_en, prev_dx, prev_cn, prev_fd, prev_fr,
     prev_disp, prev_disp_notes) = row

    if status != "COMPLETED":
        raise ConsultationNotAmendable()

    if not (chief_complaint and chief_complaint.strip()) or not (diagnosis and diagnosis.strip()):
        raise ConsultationIncomplete()

    cur.execute(
        """
        INSERT INTO consultation_amendments (
            consultation_id, previous_chief_complaint, previous_history_notes,
            previous_examination_notes, previous_diagnosis, previous_clinical_notes,
            previous_follow_up_date, previous_follow_up_reason,
            previous_disposition, previous_disposition_notes, reason, amended_by
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            consultation_id, prev_cc, prev_hn, prev_en, prev_dx, prev_cn, prev_fd, prev_fr,
            prev_disp, prev_disp_notes, reason, staff_id,
        ),
    )

    cur.execute(
        f"""
        UPDATE consultations
        SET chief_complaint = %s,
            history_notes = %s,
            examination_notes = %s,
            diagnosis = %s,
            clinical_notes = %s,
            follow_up_date = %s,
            follow_up_reason = %s,
            disposition = %s,
            disposition_notes = %s,
            updated_by = %s,
            updated_at = NOW()
        WHERE id = %s
        RETURNING {", ".join(_CONSULTATION_COLUMNS)}
        """,
        (
            chief_complaint, history_notes, examination_notes, diagnosis,
            clinical_notes, follow_up_date, follow_up_reason,
            disposition, disposition_notes, staff_id, consultation_id,
        ),
    )

    return _consultation_row_to_dict(cur.fetchone())


def list_consultation_amendments_service(cur, appointment_id: int):
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        "SELECT id FROM consultations WHERE encounter_id = %s",
        (encounter_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise EncounterNotFound()

    cur.execute(
        f"""
        SELECT {", ".join("a." + c for c in _AMENDMENT_COLUMNS)}, s.username
        FROM consultation_amendments a
        JOIN staff s ON s.id = a.amended_by
        WHERE a.consultation_id = %s
        ORDER BY a.amended_at DESC
        """,
        (row[0],),
    )
    results = []
    for r in cur.fetchall():
        d = _amendment_row_to_dict(r[:-1])
        d["amended_by_username"] = r[-1]
        results.append(d)
    return results
