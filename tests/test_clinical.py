"""
Tests for the triage/vitals and doctor consultation endpoints added in
migrations/0029_vitals_and_consultations.sql (OPD/HIMS master spec
Phase 5): GET /api/appointments/{id}/encounter, POST .../vitals,
GET .../vitals/latest, GET/PUT .../consultation,
POST .../consultation/complete.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _seed_pending_appointment(client, db_connection, doctor_name: str) -> dict:
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name=doctor_name,
        department_name=f"{doctor_name} Dept",
        appointment_type_name=f"{doctor_name} Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9197{abs(hash(doctor_name)) % 10**8:08d}"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()

    return {
        "admin_headers": admin_headers,
        "seeded": seeded,
        "patient": patient,
        "appointment": created,
    }


def _checked_in_context(client, db_connection, doctor_name: str) -> dict:
    ctx = _seed_pending_appointment(client, db_connection, doctor_name)
    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/confirm-and-checkin",
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    return ctx


# ---------------------------------------------------------------------
# Encounter summary
# ---------------------------------------------------------------------


def test_encounter_summary_includes_patient_and_doctor(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Clinical Summary")

    response = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/encounter", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    body = response.json()
    assert body["patient_id"] == ctx["patient"]["id"]
    assert body["patient_name"] == ctx["patient"]["name"]
    assert body["doctor_id"] == ctx["seeded"]["doctor_id"]
    assert body["encounter_status"] == "OPEN"
    assert body["appointment_status"] == "CHECKED_IN"


def test_encounter_requires_staff_auth(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Clinical Auth")
    response = client.get(f"/api/appointments/{ctx['appointment']['id']}/encounter")
    assert response.status_code == 401


# ---------------------------------------------------------------------
# Vitals
# ---------------------------------------------------------------------


def test_recording_vitals_requires_checked_in(client, db_connection):
    ctx = _seed_pending_appointment(client, db_connection, "Dr. Vitals NotCheckedIn")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/vitals",
        json={"pulse": 80},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 409


def test_recording_vitals_succeeds_and_computes_bmi(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Vitals Bmi")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/vitals",
        json={
            "bp_systolic": 120,
            "bp_diastolic": 80,
            "pulse": 76,
            "temperature_celsius": 37.0,
            "spo2": 98,
            "respiratory_rate": 16,
            "weight_kg": 70,
            "height_cm": 175,
            "pain_score": 2,
            "chief_complaint": "Fever and cough",
            "priority": "URGENT",
            "nursing_notes": "Patient looks uncomfortable",
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pulse"] == 76
    assert body["priority"] == "URGENT"
    # 70 / (1.75^2) = 22.9
    assert abs(body["bmi"] - 22.9) < 0.05


def test_latest_vitals_is_none_before_any_recorded(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Vitals None")

    response = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/vitals/latest", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    assert response.json() is None


def test_latest_vitals_returns_the_most_recent_recording(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Vitals Latest")
    appointment_id = ctx["appointment"]["id"]

    client.post(f"/api/appointments/{appointment_id}/vitals", json={"pulse": 70}, headers=ctx["admin_headers"])
    client.post(f"/api/appointments/{appointment_id}/vitals", json={"pulse": 90}, headers=ctx["admin_headers"])

    response = client.get(f"/api/appointments/{appointment_id}/vitals/latest", headers=ctx["admin_headers"])
    assert response.status_code == 200
    assert response.json()["pulse"] == 90


# ---------------------------------------------------------------------
# Consultation
# ---------------------------------------------------------------------


def test_get_consultation_requires_checked_in_to_create(client, db_connection):
    ctx = _seed_pending_appointment(client, db_connection, "Dr. Consult NotCheckedIn")

    response = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/consultation", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


def test_get_consultation_creates_a_draft_when_checked_in(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Consult Create")

    response = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/consultation", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DRAFT"
    assert body["doctor_id"] == ctx["seeded"]["doctor_id"]
    assert body["chief_complaint"] is None

    # Idempotent: a second GET returns the same row, not a second one.
    second = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/consultation", headers=ctx["admin_headers"]
    )
    assert second.json()["id"] == body["id"]


def test_save_consultation_draft_updates_fields(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Consult Save")
    appointment_id = ctx["appointment"]["id"]

    response = client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={
            "chief_complaint": "Sore throat",
            "history_notes": "3 days of symptoms",
            "examination_notes": "Mild pharyngeal erythema",
            "diagnosis": "Acute pharyngitis",
            "clinical_notes": "Advised rest and fluids",
            "follow_up_date": (date.today() + timedelta(days=7)).isoformat(),
            "follow_up_reason": "Review if not improved",
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["chief_complaint"] == "Sore throat"
    assert body["diagnosis"] == "Acute pharyngitis"
    assert body["status"] == "DRAFT"

    # A second save overwrites the same row (still exactly one
    # consultation for this encounter), not a second draft.
    response = client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={"chief_complaint": "Sore throat, worsening", "diagnosis": "Acute pharyngitis"},
        headers=ctx["admin_headers"],
    )
    body = response.json()
    assert body["chief_complaint"] == "Sore throat, worsening"


def test_save_consultation_records_disposition(client, db_connection):
    """migrations/0047_consultation_disposition.sql -- master spec
    audit gap: "Admit to IPD" disposition scaffold ... a stub, same
    category as orders.order_type = EXTERNAL_REFERRAL."""
    ctx = _checked_in_context(client, db_connection, "Dr. Disposition Save")
    appointment_id = ctx["appointment"]["id"]

    response = client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={
            "chief_complaint": "Chest pain",
            "diagnosis": "Suspected ACS",
            "disposition": "ADMIT_TO_IPD",
            "disposition_notes": "Bed requested in cardiology ward",
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["disposition"] == "ADMIT_TO_IPD"
    assert body["disposition_notes"] == "Bed requested in cardiology ward"


def test_save_consultation_rejects_invalid_disposition(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Disposition Invalid")
    response = client.put(
        f"/api/appointments/{ctx['appointment']['id']}/consultation",
        json={"disposition": "SEND_HOME"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 422


def test_save_consultation_with_no_diagnosis_code(client, db_connection):
    """Case A (OPD/HIMS interoperability master prompt Phase 6): a
    diagnosis with no code at all is the ordinary, unchanged case."""
    ctx = _checked_in_context(client, db_connection, "Dr. Dx Code None")
    response = client.put(
        f"/api/appointments/{ctx['appointment']['id']}/consultation",
        json={"chief_complaint": "Fatigue", "diagnosis": "Anemia"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["diagnosis"] == "Anemia"
    assert body["diagnosis_code_system"] is None
    assert body["diagnosis_code"] is None
    assert body["diagnosis_code_display"] is None


def test_save_consultation_with_diagnosis_code(client, db_connection):
    """Case B: diagnosis text plus a full, structurally valid code
    triple. Phase 6 never invents or validates the code's real-world
    meaning -- this is exactly what the caller supplied."""
    ctx = _checked_in_context(client, db_connection, "Dr. Dx Code Full")
    response = client.put(
        f"/api/appointments/{ctx['appointment']['id']}/consultation",
        json={
            "chief_complaint": "Excessive thirst",
            "diagnosis": "Type 2 diabetes mellitus",
            "diagnosis_code_system": "ICD-10",
            "diagnosis_code": "E11",
            "diagnosis_code_display": "Type 2 diabetes mellitus",
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["diagnosis_code_system"] == "ICD-10"
    assert body["diagnosis_code"] == "E11"
    assert body["diagnosis_code_display"] == "Type 2 diabetes mellitus"


def test_save_consultation_rejects_code_without_system(client, db_connection):
    """docs/OPD_HIMS_STANDARDS_READINESS.md S6's own structural rule:
    a code without a system is meaningless -- rejected at the API layer
    before it would even reach the DB's own CHECK constraint."""
    ctx = _checked_in_context(client, db_connection, "Dr. Dx Code NoSystem")
    response = client.put(
        f"/api/appointments/{ctx['appointment']['id']}/consultation",
        json={"chief_complaint": "Cough", "diagnosis": "Bronchitis", "diagnosis_code": "J20"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 422


def test_save_consultation_allows_system_without_code(client, db_connection):
    """No rule requires the reverse (system without a code yet) -- this
    phase deliberately doesn't invent one that isn't there."""
    ctx = _checked_in_context(client, db_connection, "Dr. Dx System Only")
    response = client.put(
        f"/api/appointments/{ctx['appointment']['id']}/consultation",
        json={
            "chief_complaint": "Joint pain",
            "diagnosis": "Suspected rheumatoid arthritis",
            "diagnosis_code_system": "SNOMED CT",
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    assert response.json()["diagnosis_code_system"] == "SNOMED CT"
    assert response.json()["diagnosis_code"] is None


def test_update_consultation_can_remove_diagnosis_code(client, db_connection):
    """The full-form-save semantics already established for every other
    consultation field (save_consultation_draft_service's own docstring)
    apply here too: omitting the code fields on a later save clears
    them, it doesn't leave the old ones in place."""
    ctx = _checked_in_context(client, db_connection, "Dr. Dx Code Remove")
    appointment_id = ctx["appointment"]["id"]

    client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={
            "chief_complaint": "Fatigue",
            "diagnosis": "Anemia",
            "diagnosis_code_system": "ICD-10",
            "diagnosis_code": "D64.9",
        },
        headers=ctx["admin_headers"],
    )

    response = client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={"chief_complaint": "Fatigue", "diagnosis": "Anemia"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["diagnosis_code_system"] is None
    assert body["diagnosis_code"] is None


def test_complete_consultation_requires_chief_complaint_and_diagnosis(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Consult Incomplete")
    appointment_id = ctx["appointment"]["id"]

    client.get(f"/api/appointments/{appointment_id}/consultation", headers=ctx["admin_headers"])
    response = client.post(
        f"/api/appointments/{appointment_id}/consultation/complete", headers=ctx["admin_headers"]
    )
    assert response.status_code == 422


def test_complete_consultation_succeeds_and_locks_further_edits(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Consult Complete")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={"chief_complaint": "Headache", "diagnosis": "Tension headache"},
        headers=admin_headers,
    )

    response = client.post(f"/api/appointments/{appointment_id}/consultation/complete", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["completed_at"] is not None

    # Further edits are refused now that it's signed off.
    edit_attempt = client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={"chief_complaint": "Headache, changed my mind"},
        headers=admin_headers,
    )
    assert edit_attempt.status_code == 409

    # But it's still there to view.
    read_back = client.get(f"/api/appointments/{appointment_id}/consultation", headers=admin_headers)
    assert read_back.status_code == 200
    assert read_back.json()["status"] == "COMPLETED"


def test_consultation_and_vitals_remain_readable_after_visit_completed(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Consult AfterComplete")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    client.post(f"/api/appointments/{appointment_id}/vitals", json={"pulse": 72}, headers=admin_headers)
    client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={"chief_complaint": "Cough", "diagnosis": "Common cold"},
        headers=admin_headers,
    )
    client.post(f"/api/appointments/{appointment_id}/consultation/complete", headers=admin_headers)

    # Front desk closes out the whole visit -- this is the existing,
    # unrelated /complete endpoint (queue completion), not the
    # consultation's own complete action above.
    complete_response = client.post(f"/api/appointments/{appointment_id}/complete", headers=admin_headers)
    assert complete_response.status_code == 200

    # Both stay readable...
    assert client.get(f"/api/appointments/{appointment_id}/vitals/latest", headers=admin_headers).status_code == 200
    consultation_after = client.get(f"/api/appointments/{appointment_id}/consultation", headers=admin_headers)
    assert consultation_after.status_code == 200
    assert consultation_after.json()["status"] == "COMPLETED"

    # ...but the visit being over now blocks a *new* write against it --
    # the encounter closed the moment the appointment reached COMPLETED
    # (see app/services/clinical_services.py's module docstring for why
    # this is gated on appointment status, not encounters.status).
    late_vitals = client.post(
        f"/api/appointments/{appointment_id}/vitals", json={"pulse": 99}, headers=admin_headers
    )
    assert late_vitals.status_code == 409
