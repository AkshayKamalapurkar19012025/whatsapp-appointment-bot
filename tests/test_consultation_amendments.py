"""
Tests for controlled amendment of a COMPLETED consultation (OPD/HIMS
master spec Phase 14, section 70: "Clinical/financial records should
have controlled amendment/void processes"): POST/GET .../consultation/
amend[ments], migrations/0041_consultation_amendments.sql.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9198{abs(hash(doctor_name)) % 10**8:08d}"},
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
    return {"admin_headers": admin_headers, "patient": patient, "appointment": created}


def _checked_in_context(client, db_connection, doctor_name: str) -> dict:
    ctx = _seed_pending_appointment(client, db_connection, doctor_name)
    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/confirm-and-checkin",
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    return ctx


def _completed_consultation_context(client, db_connection, doctor_name: str) -> dict:
    ctx = _checked_in_context(client, db_connection, doctor_name)
    appointment_id = ctx["appointment"]["id"]
    client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={"chief_complaint": "Fever", "diagnosis": "Viral fever"},
        headers=ctx["admin_headers"],
    )
    complete = client.post(
        f"/api/appointments/{appointment_id}/consultation/complete", headers=ctx["admin_headers"]
    )
    assert complete.status_code == 200
    return ctx


def test_amend_draft_consultation_rejected(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Amend Draft")
    appointment_id = ctx["appointment"]["id"]
    client.get(f"/api/appointments/{appointment_id}/consultation", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{appointment_id}/consultation/amend",
        json={"reason": "typo", "chief_complaint": "Fever", "diagnosis": "Flu"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 409


def test_amend_requires_reason(client, db_connection):
    ctx = _completed_consultation_context(client, db_connection, "Dr. Amend NoReason")
    appointment_id = ctx["appointment"]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/consultation/amend",
        json={"chief_complaint": "Fever", "diagnosis": "Viral fever, confirmed"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 422


def test_amend_cannot_blank_required_fields(client, db_connection):
    ctx = _completed_consultation_context(client, db_connection, "Dr. Amend Blank")
    appointment_id = ctx["appointment"]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/consultation/amend",
        json={"reason": "Correcting diagnosis", "chief_complaint": "Fever", "diagnosis": ""},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 422


def test_amend_requires_permission(client, db_connection):
    ctx = _completed_consultation_context(client, db_connection, "Dr. Amend NoPerm")
    appointment_id = ctx["appointment"]["id"]
    staff_headers = create_staff_and_get_headers(db_connection)

    response = client.post(
        f"/api/appointments/{appointment_id}/consultation/amend",
        json={"reason": "Correcting diagnosis", "chief_complaint": "Fever", "diagnosis": "Dengue"},
        headers=staff_headers,
    )
    assert response.status_code == 403


def test_amend_updates_fields_and_archives_previous_values(client, db_connection):
    ctx = _completed_consultation_context(client, db_connection, "Dr. Amend Success")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    response = client.post(
        f"/api/appointments/{appointment_id}/consultation/amend",
        json={
            "reason": "Lab-confirmed dengue, not viral fever",
            "chief_complaint": "Fever",
            "diagnosis": "Dengue fever",
            "clinical_notes": "NS1 antigen positive",
        },
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["diagnosis"] == "Dengue fever"
    assert body["clinical_notes"] == "NS1 antigen positive"

    history = client.get(
        f"/api/appointments/{appointment_id}/consultation/amendments", headers=admin_headers
    )
    assert history.status_code == 200
    entries = history.json()
    assert len(entries) == 1
    assert entries[0]["reason"] == "Lab-confirmed dengue, not viral fever"
    assert entries[0]["previous_diagnosis"] == "Viral fever"
    assert entries[0]["amended_by_username"]


def test_amend_archives_previous_disposition(client, db_connection):
    """migrations/0047_consultation_disposition.sql -- disposition
    follows the same archive-then-update amendment pattern as every
    other consultation field."""
    ctx = _checked_in_context(client, db_connection, "Dr. Amend Disposition")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={"chief_complaint": "Fever", "diagnosis": "Viral fever", "disposition": "FOLLOW_UP"},
        headers=admin_headers,
    )
    client.post(f"/api/appointments/{appointment_id}/consultation/complete", headers=admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/consultation/amend",
        json={
            "reason": "Patient deteriorated, needs admission",
            "chief_complaint": "Fever",
            "diagnosis": "Dengue fever",
            "disposition": "ADMIT_TO_IPD",
            "disposition_notes": "Bed requested",
        },
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["disposition"] == "ADMIT_TO_IPD"

    history = client.get(
        f"/api/appointments/{appointment_id}/consultation/amendments", headers=admin_headers
    ).json()
    assert history[0]["previous_disposition"] == "FOLLOW_UP"


def test_amend_archives_previous_diagnosis_code(client, db_connection):
    """OPD/HIMS interoperability master prompt Phase 6 -- diagnosis_code*
    follows the same archive-then-update amendment pattern as every
    other consultation field (see test_amend_archives_previous_
    disposition for the identical pattern on disposition)."""
    ctx = _checked_in_context(client, db_connection, "Dr. Amend Dx Code")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={
            "chief_complaint": "Fever",
            "diagnosis": "Viral fever",
            "diagnosis_code_system": "ICD-10",
            "diagnosis_code": "R50.9",
        },
        headers=admin_headers,
    )
    client.post(f"/api/appointments/{appointment_id}/consultation/complete", headers=admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/consultation/amend",
        json={
            "reason": "Lab-confirmed dengue, not viral fever",
            "chief_complaint": "Fever",
            "diagnosis": "Dengue fever",
            "diagnosis_code_system": "ICD-10",
            "diagnosis_code": "A90",
        },
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["diagnosis_code"] == "A90"

    history = client.get(
        f"/api/appointments/{appointment_id}/consultation/amendments", headers=admin_headers
    ).json()
    assert history[0]["previous_diagnosis_code_system"] == "ICD-10"
    assert history[0]["previous_diagnosis_code"] == "R50.9"


def test_amend_after_visit_closed(client, db_connection):
    """The whole point of amendment: correcting a record after the
    visit -- and the whole encounter -- has already closed. Not gated
    on CHECKED_IN like every other clinical write."""
    ctx = _completed_consultation_context(client, db_connection, "Dr. Amend AfterClose")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    close_response = client.post(f"/api/appointments/{appointment_id}/complete", headers=admin_headers)
    assert close_response.status_code == 200

    response = client.post(
        f"/api/appointments/{appointment_id}/consultation/amend",
        json={"reason": "Follow-up lab result changed the diagnosis", "chief_complaint": "Fever", "diagnosis": "Dengue fever"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["diagnosis"] == "Dengue fever"


def test_second_amendment_archives_the_first_amendments_values_not_the_original(client, db_connection):
    ctx = _completed_consultation_context(client, db_connection, "Dr. Amend Twice")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    client.post(
        f"/api/appointments/{appointment_id}/consultation/amend",
        json={"reason": "First correction", "chief_complaint": "Fever", "diagnosis": "Dengue fever"},
        headers=admin_headers,
    )
    client.post(
        f"/api/appointments/{appointment_id}/consultation/amend",
        json={"reason": "Second correction", "chief_complaint": "Fever", "diagnosis": "Malaria"},
        headers=admin_headers,
    )

    history = client.get(
        f"/api/appointments/{appointment_id}/consultation/amendments", headers=admin_headers
    ).json()
    assert len(history) == 2
    # Newest first.
    assert history[0]["reason"] == "Second correction"
    assert history[0]["previous_diagnosis"] == "Dengue fever"
    assert history[1]["reason"] == "First correction"
    assert history[1]["previous_diagnosis"] == "Viral fever"


def test_amendments_list_404_for_nonexistent_consultation(client, db_connection):
    ctx = _seed_pending_appointment(client, db_connection, "Dr. Amend Missing")
    response = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/consultation/amendments",
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 404
