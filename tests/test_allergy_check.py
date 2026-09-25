"""
Tests for the P0 clinical-safety allergy-vs-prescription check (OPD/HIMS
interoperability master prompt Phase 4): app/services/allergy_check_
service.py, wired into POST /api/appointments/{id}/prescription/items.

See docs/OPD_HIMS_STANDARDS_READINESS.md S17 for the approved design and
docs/workflows/PHARMACY.md for the documented matching limitation this
suite exercises directly (Scenario: unrelated-class medicine is NOT
caught, by design -- this is same-text matching, not drug-class
intelligence).
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _checked_in_patient(client, db_connection, doctor_name: str) -> dict:
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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9195{abs(hash(doctor_name)) % 10**8:08d}"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    checkin = client.post(
        f"/api/appointments/{appointment['id']}/confirm-and-checkin", headers=admin_headers
    )
    assert checkin.status_code == 200
    return {"admin_headers": admin_headers, "patient": patient, "appointment": appointment}


def _new_appointment_for(client, db_connection, ctx: dict, doctor_name: str) -> dict:
    """A second, later visit for the same patient (used by Scenario 7 to
    prove the allergy check reads the patient's allergy list globally,
    not scoped to the encounter that recorded it)."""
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name=doctor_name,
        department_name=f"{doctor_name} Dept",
        appointment_type_name=f"{doctor_name} Type",
    )
    scheduling_date = _next_weekday(date.today() + timedelta(days=20))
    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": ctx["patient"]["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T09:00:00+05:30",
        },
        headers=ctx["admin_headers"],
    ).json()
    checkin = client.post(
        f"/api/appointments/{appointment['id']}/confirm-and-checkin", headers=ctx["admin_headers"]
    )
    assert checkin.status_code == 200
    return appointment


def _add_allergy(client, patient_id, admin_headers, allergen, severity="MODERATE"):
    response = client.post(
        f"/api/patients/{patient_id}/allergies",
        json={"allergen": allergen, "severity": severity, "reaction": "Rash"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    return response.json()


def _latest_audit_action(db_connection, resource_type: str, resource_id: int) -> str | None:
    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT action FROM audit_log
            WHERE resource_type = %s AND resource_id = %s
            ORDER BY id DESC LIMIT 1
            """,
            (resource_type, resource_id),
        )
        row = cur.fetchone()
        return row[0] if row else None


def test_no_allergies_prescription_succeeds_with_no_warning(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Allergy None")
    appointment_id = ctx["appointment"]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Paracetamol", "quantity": 10},
        headers=ctx["admin_headers"],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["allergy_warning"] is None
    assert len(body["prescription"]["items"]) == 1
    assert body["prescription"]["items"][0]["medicine_name"] == "Paracetamol"


def test_unrelated_allergy_prescription_succeeds_with_no_warning(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Allergy Unrelated")
    _add_allergy(client, ctx["patient"]["id"], ctx["admin_headers"], "Peanuts")
    appointment_id = ctx["appointment"]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Paracetamol", "quantity": 10},
        headers=ctx["admin_headers"],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["allergy_warning"] is None
    assert len(body["prescription"]["items"]) == 1


def test_unrelated_drug_class_not_caught_by_design(client, db_connection):
    """Documents the matching limitation directly: a Penicillin allergy
    does NOT flag an Amoxicillin prescription, because nothing in this
    schema encodes that clinical (same drug-class) relationship -- see
    app/services/allergy_check_service.py's own docstring. This is the
    honest boundary of same-text matching, not a bug."""
    ctx = _checked_in_patient(client, db_connection, "Dr Allergy Class")
    _add_allergy(client, ctx["patient"]["id"], ctx["admin_headers"], "Penicillin")
    appointment_id = ctx["appointment"]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Amoxicillin", "quantity": 10},
        headers=ctx["admin_headers"],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["allergy_warning"] is None
    assert len(body["prescription"]["items"]) == 1


def test_matching_allergy_returns_warning_and_does_not_add_item(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Allergy Match")
    _add_allergy(client, ctx["patient"]["id"], ctx["admin_headers"], "Penicillin", severity="SEVERE")
    appointment_id = ctx["appointment"]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Penicillin V", "quantity": 10},
        headers=ctx["admin_headers"],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["allergy_warning"] is not None
    conflicts = body["allergy_warning"]["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["allergen"] == "Penicillin"
    assert conflicts[0]["severity"] == "SEVERE"
    assert conflicts[0]["matched_against"] == "penicillin v"
    # Not finalized -- the prescription must still have zero items.
    assert body["prescription"]["items"] == []

    assert _latest_audit_action(db_connection, "prescription", body["prescription"]["id"]) == (
        "prescription.allergy_warning_shown"
    )


def test_generic_name_also_checked(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Allergy Generic")
    _add_allergy(client, ctx["patient"]["id"], ctx["admin_headers"], "Ibuprofen")
    appointment_id = ctx["appointment"]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Brufen", "generic_name": "Ibuprofen", "quantity": 10},
        headers=ctx["admin_headers"],
    )

    body = response.json()
    assert body["allergy_warning"] is not None
    assert body["allergy_warning"]["conflicts"][0]["matched_against"] == "ibuprofen"


def test_clinician_cancels_no_item_created_and_audited(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Allergy Cancel")
    _add_allergy(client, ctx["patient"]["id"], ctx["admin_headers"], "Sulfa")
    appointment_id = ctx["appointment"]["id"]

    warned = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Sulfamethoxazole", "quantity": 10},
        headers=ctx["admin_headers"],
    ).json()
    assert warned["allergy_warning"] is not None
    prescription_id = warned["prescription"]["id"]

    cancelled = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Sulfamethoxazole", "quantity": 10, "allergy_decision": "cancel"},
        headers=ctx["admin_headers"],
    )

    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["allergy_warning"] is None
    assert body["prescription"]["items"] == []  # never created

    assert _latest_audit_action(db_connection, "prescription", prescription_id) == (
        "prescription.allergy_warning_cancelled"
    )


def test_clinician_overrides_item_created_and_audited(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Allergy Override")
    _add_allergy(client, ctx["patient"]["id"], ctx["admin_headers"], "Codeine")
    appointment_id = ctx["appointment"]["id"]

    warned = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Codeine Phosphate", "quantity": 10},
        headers=ctx["admin_headers"],
    ).json()
    assert warned["allergy_warning"] is not None
    prescription_id = warned["prescription"]["id"]

    overridden = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Codeine Phosphate", "quantity": 10, "allergy_decision": "continue"},
        headers=ctx["admin_headers"],
    )

    assert overridden.status_code == 200
    body = overridden.json()
    assert body["allergy_warning"] is None
    assert len(body["prescription"]["items"]) == 1
    assert body["prescription"]["items"][0]["medicine_name"] == "Codeine Phosphate"

    assert _latest_audit_action(db_connection, "prescription", prescription_id) == (
        "prescription.allergy_warning_overridden"
    )


def test_duplicate_submission_behavior_unchanged(client, db_connection):
    """No item-level idempotency key exists in this system (confirmed
    pre-existing behavior, not introduced or removed by the allergy
    check) -- two identical, non-conflicting add-item calls create two
    separate items, exactly as before this change."""
    ctx = _checked_in_patient(client, db_connection, "Dr Allergy Dup")
    appointment_id = ctx["appointment"]["id"]
    payload = {"medicine_name": "Paracetamol", "quantity": 10}

    first = client.post(
        f"/api/appointments/{appointment_id}/prescription/items", json=payload, headers=ctx["admin_headers"]
    ).json()
    second = client.post(
        f"/api/appointments/{appointment_id}/prescription/items", json=payload, headers=ctx["admin_headers"]
    ).json()

    assert len(first["prescription"]["items"]) == 1
    assert len(second["prescription"]["items"]) == 2


def test_resolved_allergy_does_not_trigger_warning(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Allergy Resolved")
    allergy = _add_allergy(client, ctx["patient"]["id"], ctx["admin_headers"], "Latex")
    resolve = client.post(
        f"/api/patients/{ctx['patient']['id']}/allergies/{allergy['id']}/resolve",
        json={"reason": "Documented in error"},
        headers=ctx["admin_headers"],
    )
    assert resolve.status_code == 200
    appointment_id = ctx["appointment"]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Latex-Free Gloves", "quantity": 1},
        headers=ctx["admin_headers"],
    )

    assert response.json()["allergy_warning"] is None


def test_historical_allergy_from_an_earlier_visit_participates_in_the_check(client, db_connection):
    """Allergies are patient-scoped, not encounter-scoped (patient_
    allergies.patient_id, migrations/0042) -- an allergy recorded during
    an earlier visit must still be checked during a later, unrelated
    visit for the same patient."""
    ctx = _checked_in_patient(client, db_connection, "Dr Allergy History1")
    _add_allergy(client, ctx["patient"]["id"], ctx["admin_headers"], "Aspirin")

    later_appointment = _new_appointment_for(client, db_connection, ctx, "Dr Allergy History2")

    response = client.post(
        f"/api/appointments/{later_appointment['id']}/prescription/items",
        json={"medicine_name": "Aspirin", "quantity": 10},
        headers=ctx["admin_headers"],
    )

    body = response.json()
    assert body["allergy_warning"] is not None
    assert body["allergy_warning"]["conflicts"][0]["allergen"] == "Aspirin"
