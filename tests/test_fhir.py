"""
Tests for the FHIR R4 read-only interoperability layer (OPD/HIMS
interoperability master prompt Phase 8): GET /fhir/r4/{Resource}/{id}.
See docs/architecture/FHIR_FOUNDATION.md for the mapping design each
test here verifies against a real, complete patient journey rather than
synthetic fixtures.
"""

import secrets
from datetime import date, timedelta

import pytest

from app.services.staff_auth import login as _staff_login
from tests.helpers import create_admin_and_get_headers, create_staff_for_test, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _new_hospital(db_connection) -> int:
    """Same pattern tests/test_module_licensing.py already established
    for a genuine cross-tenant negative test."""
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO hospitals (code, name) VALUES (%s, %s) RETURNING id",
            (f"TEST-FHIR-{secrets.token_hex(4)}", "FHIR Test Hospital"),
        )
        (hospital_id,) = cur.fetchone()
    db_connection.commit()
    return hospital_id


def _admin_for_hospital(db_connection, hospital_id: int) -> dict:
    username = f"fhirtest-admin-{secrets.token_hex(4)}"
    password = "fhirtest-admin-password"  # noqa: S105 -- test-only
    account = create_staff_for_test(db_connection, username=username, password=password, role="ADMIN")
    with db_connection.cursor() as cur:
        cur.execute("UPDATE staff SET hospital_id = %s WHERE id = %s", (hospital_id, account["id"]))
        result = _staff_login(cur, username, password)
    db_connection.commit()
    return {"Authorization": f"Bearer {result['session_token']}"}


def _full_patient_journey(client, db_connection, doctor_name: str) -> dict:
    """One complete, realistic visit -- patient identity, an allergy,
    a checked-in encounter, a completed consultation with a coded
    diagnosis, a LAB order with a coded-unit abnormal result, and a
    prescription item linked to a Medication Master row. Every id
    the resource-mapping tests below need comes from here, not a
    synthetic/fabricated row."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name=doctor_name,
        department_name=f"{doctor_name} Dept", appointment_type_name=f"{doctor_name} Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9191{abs(hash(doctor_name)) % 10**8:08d}"},
        headers=admin_headers,
    ).json()
    allergy = client.post(
        f"/api/patients/{patient['id']}/allergies",
        json={"allergen": "Penicillin", "reaction": "Rash", "severity": "MODERATE"},
        headers=admin_headers,
    ).json()

    scheduling_date = _next_weekday(date.today() + timedelta(days=15))
    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T10:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    checkin = client.post(
        f"/api/appointments/{appointment['id']}/confirm-and-checkin", headers=admin_headers
    ).json()
    encounter = client.get(f"/api/appointments/{appointment['id']}/encounter", headers=admin_headers).json()

    vitals = client.post(
        f"/api/appointments/{appointment['id']}/vitals",
        json={
            "bp_systolic": 138,
            "bp_diastolic": 88,
            "pulse": 96,
            "temperature_celsius": 37.2,
            "spo2": 97,
            "respiratory_rate": 18,
            "weight_kg": 82.0,
            "height_cm": 170.0,
            "pain_score": 2,
            "chief_complaint": "Excessive thirst and fatigue",
        },
        headers=admin_headers,
    ).json()

    client.put(
        f"/api/appointments/{appointment['id']}/consultation",
        json={
            "chief_complaint": "Excessive thirst and fatigue",
            "diagnosis": "Type 2 diabetes mellitus",
            "diagnosis_code_system": "ICD-10",
            "diagnosis_code": "E11",
            "diagnosis_code_display": "Type 2 diabetes mellitus",
        },
        headers=admin_headers,
    )
    consultation = client.post(
        f"/api/appointments/{appointment['id']}/consultation/complete", headers=admin_headers
    ).json()

    order = client.post(
        f"/api/appointments/{appointment['id']}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=admin_headers,
    ).json()
    order_after_result = client.post(
        f"/api/appointments/{appointment['id']}/orders/{order['id']}/result",
        json={
            "items": [
                {
                    "parameter": "Hemoglobin",
                    "result_value": "9.8",
                    "unit": "g/dL",
                    "unit_system": "UCUM",
                    "unit_code": "g/dL",
                    "reference_range": "13-17",
                    "is_abnormal": True,
                }
            ]
        },
        headers=admin_headers,
    ).json()
    result = order_after_result["results"][0]

    # A LAB order's result now lands at RESULT_ENTERED, not COMPLETED --
    # a real lifecycle change introduced by the (separately-landed)
    # diagnostic-workflow migration this branch was rebased/merged onto
    # (migrations/0054_diagnostic_workflow.sql). Drive it through the
    # same verify -> release steps that lifecycle actually requires so
    # this fixture's LAB order reaches the real terminal COMPLETED
    # state the ServiceRequest tests below expect -- an ADMIN account
    # may verify its own entered result (order_services.py's own
    # documented ADMIN override), so no second staff account is needed.
    client.post(f"/api/appointments/{appointment['id']}/orders/{order['id']}/verify", headers=admin_headers)
    client.post(f"/api/appointments/{appointment['id']}/orders/{order['id']}/release", headers=admin_headers)

    medication = client.post(
        "/api/pharmacy/medications",
        json={"generic_name": f"{doctor_name} Metformin", "brand_name": "Glyciphage", "strength": "500mg"},
        headers=admin_headers,
    ).json()
    prescription = client.post(
        f"/api/appointments/{appointment['id']}/prescription/items",
        json={
            "medicine_name": "Glyciphage",
            "medication_id": medication["id"],
            "dosage": "500mg",
            "route": "Oral",
            "frequency": "1-0-1",
            "duration": "30 days",
            "quantity": 60,
        },
        headers=admin_headers,
    ).json()["prescription"]
    prescription_item = prescription["items"][0]

    # A second, free-text-only prescription item (no medication_id) to
    # exercise the medicationCodeableConcept branch separately.
    prescription2 = client.post(
        f"/api/appointments/{appointment['id']}/prescription/items",
        json={"medicine_name": "Paracetamol", "quantity": 10},
        headers=admin_headers,
    ).json()["prescription"]
    freetext_item = prescription2["items"][-1]

    # Send to pharmacy -- prescriptions.status DRAFT -> PRESCRIBED, which
    # the MedicationRequest mapper maps to 'active', not 'draft'.
    client.post(f"/api/appointments/{appointment['id']}/prescription/prescribe", headers=admin_headers)

    return {
        "admin_headers": admin_headers,
        "patient": patient,
        "allergy": allergy,
        "appointment": appointment,
        "encounter": encounter,
        "vitals": vitals,
        "consultation": consultation,
        "doctor_id": seeded["doctor_id"],
        "order": order,
        "result": result,
        "medication": medication,
        "prescription_item": prescription_item,
        "freetext_item": freetext_item,
    }


# ---------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------


def test_fhir_patient(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Patient")
    response = client.get(f"/fhir/r4/Patient/{ctx['patient']['id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/fhir+json")
    body = response.json()
    assert body["resourceType"] == "Patient"
    assert body["id"] == str(ctx["patient"]["id"])
    assert body["active"] is True
    assert body["name"][0]["text"] == ctx["patient"]["name"]
    assert any(i["value"] == ctx["patient"]["uhid"] for i in body["identifier"])
    assert any(t["value"] == ctx["patient"]["whatsapp_number"] for t in body["telecom"])
    # ABHA must never be claimed/invented.
    assert not any("abha" in i.get("system", "").lower() for i in body["identifier"])
    # patients.updated_at is a real, maintained column -- meta.lastUpdated
    # is populated; meta.versionId is never fabricated (no real version
    # counter exists anywhere in this schema).
    assert "meta" in body and "lastUpdated" in body["meta"]
    assert "versionId" not in body["meta"]


# ---------------------------------------------------------------------
# Practitioner
# ---------------------------------------------------------------------


def test_fhir_practitioner(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Practitioner")
    response = client.get(f"/fhir/r4/Practitioner/{ctx['doctor_id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Practitioner"
    assert body["id"] == str(ctx["doctor_id"])
    assert body["active"] is True
    assert "name" in body


# ---------------------------------------------------------------------
# PractitionerRole
# ---------------------------------------------------------------------


def test_fhir_practitioner_role(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR PractRole")
    response = client.get(f"/fhir/r4/PractitionerRole/{ctx['doctor_id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "PractitionerRole"
    assert body["active"] is True
    assert body["practitioner"]["reference"] == f"Practitioner/{ctx['doctor_id']}"
    assert body["organization"]["reference"] == "Organization/1"
    assert "meta" in body and "lastUpdated" in body["meta"]
    # doctors.specialization is uncoded free text -- must not be forced
    # into a fabricated specialty coding, and department is an
    # operational grouping, not a FHIR Location/specialty concept.
    assert "specialty" not in body
    assert "location" not in body


# ---------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------


def test_fhir_organization(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Org")
    response = client.get("/fhir/r4/Organization/1", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Organization"
    assert body["id"] == "1"
    assert body["name"]


def test_fhir_organization_rejects_other_hospital_id(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Org Other")
    other_hospital_id = _new_hospital(db_connection)
    response = client.get(f"/fhir/r4/Organization/{other_hospital_id}", headers=ctx["admin_headers"])
    assert response.status_code == 404
    assert response.json()["resourceType"] == "OperationOutcome"


# ---------------------------------------------------------------------
# Appointment
# ---------------------------------------------------------------------


def test_fhir_appointment(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Appt")
    response = client.get(f"/fhir/r4/Appointment/{ctx['appointment']['id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Appointment"
    assert body["status"] == "checked-in"
    assert body["participant"][0]["actor"]["reference"] == f"Patient/{ctx['patient']['id']}"
    assert body["participant"][1]["actor"]["reference"] == f"Practitioner/{ctx['doctor_id']}"
    assert "start" in body and "end" in body


# ---------------------------------------------------------------------
# Encounter
# ---------------------------------------------------------------------


def test_fhir_encounter(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Encounter")
    response = client.get(f"/fhir/r4/Encounter/{ctx['encounter']['encounter_id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Encounter"
    assert body["status"] == "in-progress"
    assert body["class"]["code"] == "AMB"
    assert body["subject"]["reference"] == f"Patient/{ctx['patient']['id']}"
    assert body["participant"][0]["individual"]["reference"] == f"Practitioner/{ctx['doctor_id']}"
    assert body["appointment"][0]["reference"] == f"Appointment/{ctx['appointment']['id']}"


# ---------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------


def test_fhir_condition(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Condition")
    response = client.get(f"/fhir/r4/Condition/{ctx['consultation']['id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Condition"
    assert body["code"]["text"] == "Type 2 diabetes mellitus"
    assert body["code"]["coding"][0]["code"] == "E11"
    assert body["code"]["coding"][0]["system"] == "ICD-10"
    assert body["subject"]["reference"] == f"Patient/{ctx['patient']['id']}"
    assert body["encounter"]["reference"] == f"Encounter/{ctx['encounter']['encounter_id']}"


def test_fhir_condition_absent_when_no_diagnosis_documented(client, db_connection):
    """A DRAFT consultation with nothing documented yet has no
    Condition to represent -- 404, not a 200 with an empty resource."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr FHIR Condition Draft",
        department_name="Dr FHIR Condition Draft Dept", appointment_type_name="Dr FHIR Condition Draft Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "FHIR Condition Draft Patient", "whatsapp_number": "+919100000099"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=16))
    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T10:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    client.post(f"/api/appointments/{appointment['id']}/confirm-and-checkin", headers=admin_headers)
    consultation = client.get(
        f"/api/appointments/{appointment['id']}/consultation", headers=admin_headers
    ).json()

    response = client.get(f"/fhir/r4/Condition/{consultation['id']}", headers=admin_headers)
    assert response.status_code == 404
    assert response.json()["resourceType"] == "OperationOutcome"


def test_fhir_condition_uncoded_diagnosis(client, db_connection):
    """A real diagnosis was documented (free text), but no terminology
    code was ever attached -- Condition.code must still be produced
    (text-only), never omitted, and no code system may be invented to
    fill the gap. This is the middle case between "no diagnosis at all"
    (404, tested above) and "diagnosis + full ICD-10 code" (tested by
    test_fhir_condition)."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr FHIR Condition Uncoded",
        department_name="Dr FHIR Condition Uncoded Dept", appointment_type_name="Dr FHIR Condition Uncoded Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "FHIR Condition Uncoded Patient", "whatsapp_number": "+919100000097"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=18))
    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T10:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    client.post(f"/api/appointments/{appointment['id']}/confirm-and-checkin", headers=admin_headers)
    client.put(
        f"/api/appointments/{appointment['id']}/consultation",
        json={"chief_complaint": "Sore throat", "diagnosis": "Viral pharyngitis"},
        headers=admin_headers,
    )
    consultation = client.post(
        f"/api/appointments/{appointment['id']}/consultation/complete", headers=admin_headers
    ).json()

    response = client.get(f"/fhir/r4/Condition/{consultation['id']}", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["code"]["text"] == "Viral pharyngitis"
    assert "coding" not in body["code"]


# ---------------------------------------------------------------------
# AllergyIntolerance
# ---------------------------------------------------------------------


def test_fhir_allergy(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Allergy")
    response = client.get(f"/fhir/r4/AllergyIntolerance/{ctx['allergy']['id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "AllergyIntolerance"
    assert body["code"]["text"] == "Penicillin"
    assert body["clinicalStatus"]["coding"][0]["code"] == "active"
    assert body["reaction"][0]["manifestation"][0]["text"] == "Rash"
    assert body["reaction"][0]["severity"] == "moderate"
    assert body["patient"]["reference"] == f"Patient/{ctx['patient']['id']}"
    # recorded_by is a staff_id, never a Practitioner -- must not be
    # fabricated into a recorder/asserter reference.
    assert "recorder" not in body
    assert "asserter" not in body


# ---------------------------------------------------------------------
# Medication
# ---------------------------------------------------------------------


def test_fhir_medication(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Medication")
    response = client.get(f"/fhir/r4/Medication/{ctx['medication']['id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Medication"
    assert "Metformin" in body["code"]["text"]
    assert "Glyciphage" in body["code"]["text"]
    assert body["status"] == "active"


# ---------------------------------------------------------------------
# MedicationRequest
# ---------------------------------------------------------------------


def test_fhir_medication_request_with_medication_link(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR MedReq Linked")
    response = client.get(
        f"/fhir/r4/MedicationRequest/{ctx['prescription_item']['id']}", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "MedicationRequest"
    assert body["status"] == "active"
    assert body["intent"] == "order"
    assert body["medicationReference"]["reference"] == f"Medication/{ctx['medication']['id']}"
    assert "medicationCodeableConcept" not in body
    assert body["subject"]["reference"] == f"Patient/{ctx['patient']['id']}"
    assert body["encounter"]["reference"] == f"Encounter/{ctx['encounter']['encounter_id']}"
    assert body["requester"]["reference"] == f"Practitioner/{ctx['doctor_id']}"
    assert body["dosageInstruction"][0]["route"]["text"] == "Oral"
    assert body["dispenseRequest"]["quantity"]["value"] == 60


def test_fhir_medication_request_free_text_only(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR MedReq FreeText")
    response = client.get(f"/fhir/r4/MedicationRequest/{ctx['freetext_item']['id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["medicationCodeableConcept"]["text"] == "Paracetamol"
    assert "medicationReference" not in body


# ---------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------


def test_fhir_observation(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Observation")
    response = client.get(f"/fhir/r4/Observation/or-{ctx['result']['id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Observation"
    assert body["id"] == f"or-{ctx['result']['id']}"
    assert body["status"] == "final"
    assert body["code"]["text"] == "Hemoglobin"
    assert body["valueQuantity"]["value"] == 9.8
    assert body["valueQuantity"]["unit"] == "g/dL"
    assert body["valueQuantity"]["code"] == "g/dL"
    assert body["valueQuantity"]["system"] == "UCUM"
    assert body["referenceRange"][0]["text"] == "13-17"
    assert body["interpretation"][0]["coding"][0]["code"] == "A"
    assert body["subject"]["reference"] == f"Patient/{ctx['patient']['id']}"
    assert body["encounter"]["reference"] == f"Encounter/{ctx['encounter']['encounter_id']}"
    # order_results has no updated_at column -- no meta fabricated.
    assert "meta" not in body


def test_fhir_observation_non_numeric_value_stays_string(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Observation Text")
    order = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/orders",
        json={"order_type": "RADIOLOGY", "description": "Chest X-ray"},
        headers=ctx["admin_headers"],
    ).json()
    result = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Findings", "result_value": "No acute abnormality"}]},
        headers=ctx["admin_headers"],
    ).json()["results"][0]

    response = client.get(f"/fhir/r4/Observation/or-{result['id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["valueString"] == "No acute abnormality"
    assert "valueQuantity" not in body
    assert "interpretation" not in body


def test_fhir_observation_unrecognized_id_prefix_is_404(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Observation BadPrefix")
    response = client.get(f"/fhir/r4/Observation/{ctx['result']['id']}", headers=ctx["admin_headers"])
    assert response.status_code == 404
    response = client.get(f"/fhir/r4/Observation/or-notanumber", headers=ctx["admin_headers"])
    assert response.status_code == 404


# ---------------------------------------------------------------------
# Observation (from vitals) -- Phase 9
# ---------------------------------------------------------------------


def test_fhir_observation_from_vitals_panel(client, db_connection):
    """One Observation per vitals row, with every recorded measurement
    as a component -- including bp_systolic/bp_diastolic as TWO
    components of the SAME observation, not two separate resources."""
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Vitals Panel")
    response = client.get(f"/fhir/r4/Observation/vt-{ctx['vitals']['id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Observation"
    assert body["id"] == f"vt-{ctx['vitals']['id']}"
    assert body["status"] == "final"
    assert body["category"][0]["coding"][0]["code"] == "vital-signs"
    assert body["subject"]["reference"] == f"Patient/{ctx['patient']['id']}"
    assert body["encounter"]["reference"] == f"Encounter/{ctx['encounter']['encounter_id']}"
    # vitals has no updated_at column -- no meta fabricated.
    assert "meta" not in body

    components_by_label = {c["code"]["text"]: c for c in body["component"]}
    assert components_by_label["Systolic blood pressure"]["valueQuantity"]["value"] == 138
    assert components_by_label["Systolic blood pressure"]["valueQuantity"]["unit"] == "mmHg"
    assert components_by_label["Diastolic blood pressure"]["valueQuantity"]["value"] == 88
    assert components_by_label["Pulse"]["valueQuantity"]["value"] == 96
    assert components_by_label["Oxygen saturation"]["valueQuantity"]["value"] == 97
    assert components_by_label["Body mass index"]["valueQuantity"]["value"] == pytest.approx(28.4, abs=0.2)

    # Critical rule: no invented LOINC/UCUM codes anywhere in this
    # resource -- every code is text-only, every unit is a plain string.
    for component in body["component"]:
        assert "coding" not in component["code"]
        assert "system" not in component["valueQuantity"]
        assert "code" not in component["valueQuantity"]
    assert "coding" not in body["code"]


def test_fhir_observation_from_vitals_absent_when_nothing_recorded(client, db_connection):
    """A triage row that only logs a chief complaint, with no actual
    measurement, has nothing to represent as an Observation -- 404, same
    as a diagnosis-less Condition."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr FHIR Vitals Empty",
        department_name="Dr FHIR Vitals Empty Dept", appointment_type_name="Dr FHIR Vitals Empty Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "FHIR Vitals Empty Patient", "whatsapp_number": "+919100000098"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=17))
    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T10:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    client.post(f"/api/appointments/{appointment['id']}/confirm-and-checkin", headers=admin_headers)
    vitals = client.post(
        f"/api/appointments/{appointment['id']}/vitals",
        json={"chief_complaint": "Headache"},
        headers=admin_headers,
    ).json()

    response = client.get(f"/fhir/r4/Observation/vt-{vitals['id']}", headers=admin_headers)
    assert response.status_code == 404
    assert response.json()["resourceType"] == "OperationOutcome"


def test_fhir_observation_from_vitals_partial_set(client, db_connection):
    """Only pulse and SpO2 recorded (no BP, no weight/height, no pain
    score) -- component[] must contain exactly those two, proving the
    per-field NULL check is real and not an all-or-nothing panel."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr FHIR Vitals Partial",
        department_name="Dr FHIR Vitals Partial Dept", appointment_type_name="Dr FHIR Vitals Partial Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "FHIR Vitals Partial Patient", "whatsapp_number": "+919100000096"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=19))
    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T10:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    client.post(f"/api/appointments/{appointment['id']}/confirm-and-checkin", headers=admin_headers)
    vitals = client.post(
        f"/api/appointments/{appointment['id']}/vitals",
        json={"pulse": 72, "spo2": 99},
        headers=admin_headers,
    ).json()

    response = client.get(f"/fhir/r4/Observation/vt-{vitals['id']}", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    labels = {c["code"]["text"] for c in body["component"]}
    assert labels == {"Pulse", "Oxygen saturation"}


# ---------------------------------------------------------------------
# ServiceRequest
# ---------------------------------------------------------------------


def test_fhir_service_request(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR ServiceRequest")
    response = client.get(f"/fhir/r4/ServiceRequest/{ctx['order']['id']}", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "ServiceRequest"
    assert body["status"] == "completed"
    assert body["intent"] == "order"
    assert body["priority"] == "routine"
    assert body["category"][0]["text"] == "LAB"
    assert body["code"]["text"] == "CBC"
    assert body["subject"]["reference"] == f"Patient/{ctx['patient']['id']}"
    assert body["encounter"]["reference"] == f"Encounter/{ctx['encounter']['encounter_id']}"
    assert body["requester"]["reference"] == f"Practitioner/{ctx['doctor_id']}"


# ---------------------------------------------------------------------
# Negative tests -- auth, unknown id, tenant isolation
# ---------------------------------------------------------------------

_RESOURCE_ENDPOINTS = [
    "Patient", "Practitioner", "PractitionerRole", "Appointment", "Encounter", "Condition",
    "AllergyIntolerance", "Medication", "MedicationRequest", "ServiceRequest",
]


def _single_resource_ids(ctx: dict) -> dict:
    return {
        "Patient": ctx["patient"]["id"],
        "Practitioner": ctx["doctor_id"],
        "PractitionerRole": ctx["doctor_id"],
        "Appointment": ctx["appointment"]["id"],
        "Encounter": ctx["encounter"]["encounter_id"],
        "Condition": ctx["consultation"]["id"],
        "AllergyIntolerance": ctx["allergy"]["id"],
        "Medication": ctx["medication"]["id"],
        "MedicationRequest": ctx["prescription_item"]["id"],
        "ServiceRequest": ctx["order"]["id"],
    }


def test_all_fhir_endpoints_require_authentication(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR NoAuth")
    for resource, resource_id in _single_resource_ids(ctx).items():
        response = client.get(f"/fhir/r4/{resource}/{resource_id}")
        assert response.status_code == 401, resource
    for observation_id in (f"or-{ctx['result']['id']}", f"vt-{ctx['vitals']['id']}"):
        assert client.get(f"/fhir/r4/Observation/{observation_id}").status_code == 401
    assert client.get(f"/fhir/r4/Patient/{ctx['patient']['id']}/$everything").status_code == 401
    assert client.get("/fhir/r4/Appointment", params={"patient": ctx["patient"]["id"]}).status_code == 401


def test_all_fhir_endpoints_404_on_unknown_id(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Unknown")
    for resource in _RESOURCE_ENDPOINTS:
        response = client.get(f"/fhir/r4/{resource}/999999999", headers=ctx["admin_headers"])
        assert response.status_code == 404, resource
        assert response.json()["resourceType"] == "OperationOutcome"
    for observation_id in ("or-999999999", "vt-999999999"):
        response = client.get(f"/fhir/r4/Observation/{observation_id}", headers=ctx["admin_headers"])
        assert response.status_code == 404, observation_id
        assert response.json()["resourceType"] == "OperationOutcome"
    response = client.get("/fhir/r4/Patient/999999999/$everything", headers=ctx["admin_headers"])
    assert response.status_code == 404
    assert response.json()["resourceType"] == "OperationOutcome"


def test_all_fhir_endpoints_isolate_by_tenant(client, db_connection):
    """A real patient/appointment/encounter/consultation/allergy/
    medication/prescription-item/result/order/vitals in hospital 1 must
    be invisible to a staff session scoped to a different hospital --
    identical 404 to a genuinely nonexistent id, never a 403 that would
    confirm the record exists elsewhere."""
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Tenant")
    other_hospital_id = _new_hospital(db_connection)
    other_headers = _admin_for_hospital(db_connection, other_hospital_id)

    for resource, resource_id in _single_resource_ids(ctx).items():
        response = client.get(f"/fhir/r4/{resource}/{resource_id}", headers=other_headers)
        assert response.status_code == 404, resource
        assert response.json()["resourceType"] == "OperationOutcome"

    for observation_id in (f"or-{ctx['result']['id']}", f"vt-{ctx['vitals']['id']}"):
        response = client.get(f"/fhir/r4/Observation/{observation_id}", headers=other_headers)
        assert response.status_code == 404, observation_id

    response = client.get(f"/fhir/r4/Patient/{ctx['patient']['id']}/$everything", headers=other_headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------
# Search (Phase 9)
# ---------------------------------------------------------------------


def test_fhir_search_patient_by_identifier(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Search Identifier")
    response = client.get(
        "/fhir/r4/Patient", params={"identifier": ctx["patient"]["uhid"]}, headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "searchset"
    assert body["total"] == 1
    assert body["entry"][0]["resource"]["id"] == str(ctx["patient"]["id"])


def test_fhir_search_patient_by_identifier_no_match_is_empty_bundle(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Search Identifier NoMatch")
    response = client.get(
        "/fhir/r4/Patient", params={"identifier": "HOS-9999999"}, headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "searchset"
    assert body["total"] == 0
    assert body["entry"] == []


@pytest.mark.parametrize(
    "resource,ctx_key",
    [
        ("Appointment", "appointment"),
        ("Encounter", "encounter"),
        ("Condition", "consultation"),
        ("AllergyIntolerance", "allergy"),
        ("MedicationRequest", "prescription_item"),
    ],
)
def test_fhir_search_by_patient(client, db_connection, resource, ctx_key):
    ctx = _full_patient_journey(client, db_connection, f"Dr FHIR Search {resource}")
    response = client.get(
        f"/fhir/r4/{resource}", params={"patient": ctx["patient"]["id"]}, headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "searchset"
    assert body["total"] >= 1
    assert all(r["resource"]["resourceType"] == resource for r in body["entry"])


def test_fhir_search_observation_by_patient_includes_both_sources(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Search Observation")
    response = client.get(
        "/fhir/r4/Observation", params={"patient": ctx["patient"]["id"]}, headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    body = response.json()
    ids = {e["resource"]["id"] for e in body["entry"]}
    assert f"or-{ctx['result']['id']}" in ids
    assert f"vt-{ctx['vitals']['id']}" in ids


def test_fhir_search_by_patient_missing_param_is_empty_bundle(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Search NoParam")
    response = client.get("/fhir/r4/Appointment", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0


def test_fhir_search_by_patient_isolates_by_tenant(client, db_connection):
    """A patient id from hospital 1 searched by a hospital-2 session
    must yield an empty Bundle, not another hospital's data."""
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Search Tenant")
    other_hospital_id = _new_hospital(db_connection)
    other_headers = _admin_for_hospital(db_connection, other_hospital_id)

    for resource in ("Appointment", "Encounter", "Condition", "AllergyIntolerance", "MedicationRequest",
                     "Observation"):
        response = client.get(
            f"/fhir/r4/{resource}", params={"patient": ctx["patient"]["id"]}, headers=other_headers
        )
        assert response.status_code == 200, resource
        assert response.json()["total"] == 0, resource


# ---------------------------------------------------------------------
# $everything (Phase 9)
# ---------------------------------------------------------------------


def test_fhir_patient_everything(client, db_connection):
    ctx = _full_patient_journey(client, db_connection, "Dr FHIR Everything")
    response = client.get(f"/fhir/r4/Patient/{ctx['patient']['id']}/$everything", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "collection"

    by_type = {}
    for entry in body["entry"]:
        by_type.setdefault(entry["resource"]["resourceType"], []).append(entry["resource"])

    assert by_type["Patient"][0]["id"] == str(ctx["patient"]["id"])
    assert any(a["id"] == str(ctx["appointment"]["id"]) for a in by_type["Appointment"])
    assert any(e["id"] == str(ctx["encounter"]["encounter_id"]) for e in by_type["Encounter"])
    assert any(c["id"] == str(ctx["consultation"]["id"]) for c in by_type["Condition"])
    assert any(a["id"] == str(ctx["allergy"]["id"]) for a in by_type["AllergyIntolerance"])
    assert any(mr["id"] == str(ctx["prescription_item"]["id"]) for mr in by_type["MedicationRequest"])
    assert any(sr["id"] == str(ctx["order"]["id"]) for sr in by_type["ServiceRequest"])
    observation_ids = {o["id"] for o in by_type["Observation"]}
    assert f"or-{ctx['result']['id']}" in observation_ids
    assert f"vt-{ctx['vitals']['id']}" in observation_ids

    # Every non-Patient resource references (not inlines) other
    # resources -- no `contained` arrays anywhere in the Bundle.
    for entry in body["entry"]:
        assert "contained" not in entry["resource"]


def test_fhir_patient_everything_excludes_other_patients_and_tenants(client, db_connection):
    ctx1 = _full_patient_journey(client, db_connection, "Dr FHIR Everything Isolation A")
    ctx2 = _full_patient_journey(client, db_connection, "Dr FHIR Everything Isolation B")

    response = client.get(
        f"/fhir/r4/Patient/{ctx1['patient']['id']}/$everything", headers=ctx1["admin_headers"]
    )
    assert response.status_code == 200
    body = response.json()
    all_ids = {(e["resource"]["resourceType"], e["resource"]["id"]) for e in body["entry"]}
    assert ("Patient", str(ctx2["patient"]["id"])) not in all_ids
    assert ("Appointment", str(ctx2["appointment"]["id"])) not in all_ids

    other_hospital_id = _new_hospital(db_connection)
    other_headers = _admin_for_hospital(db_connection, other_hospital_id)
    response = client.get(
        f"/fhir/r4/Patient/{ctx1['patient']['id']}/$everything", headers=other_headers
    )
    assert response.status_code == 404
