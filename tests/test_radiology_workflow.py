"""
Tests for the Radiology diagnostic lifecycle (OPD/HIMS master spec
Phase 7, resumed by migrations/0054_diagnostic_workflow.sql):

    ORDERED -> IN_PROGRESS (study performed) -> RESULT_ENTERED (report
    drafted: Technique/Findings/Impression, as three order_results rows
    -- see migrations/0031's own header on why this shared generic
    table already covers a radiology report's narrative sections) ->
    VERIFIED -> COMPLETED (released)

RADIOLOGY has no sample-collection step (mark_order_in_progress_service
allows ORDERED -> IN_PROGRESS directly for it) -- that's the one place
its lifecycle actually diverges from LAB's, per the source-of-truth
audit's own instruction that Lab vs Radiology "should diverge where
clinically appropriate" while sharing the same status vocabulary,
RBAC, and verify/release mechanics (see tests/test_lab_workflow.py for
those, not duplicated here).
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _checked_in_context(client, db_connection, doctor_name: str) -> dict:
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
    response = client.post(f"/api/appointments/{appointment['id']}/confirm-and-checkin", headers=admin_headers)
    assert response.status_code == 200

    return {"admin_headers": admin_headers, "seeded": seeded, "patient": patient, "appointment": appointment}


def _create_radiology_order(client, appointment_id, headers, **overrides):
    payload = {
        "order_type": "RADIOLOGY",
        "description": "Chest X-ray, PA view",
        "clinical_indication": "Persistent cough, rule out consolidation",
    }
    payload.update(overrides)
    return client.post(f"/api/appointments/{appointment_id}/orders", json=payload, headers=headers).json()


def test_full_radiology_lifecycle_ends_completed_and_visible_to_doctor(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Radiology Full")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    order = _create_radiology_order(client, appointment_id, admin_headers)
    assert order["status"] == "ORDERED"
    assert order["clinical_indication"] == "Persistent cough, rule out consolidation"

    # No sample step -- straight to "study performed".
    performed = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/start-processing",
        headers=admin_headers,
    )
    assert performed.status_code == 200
    assert performed.json()["status"] == "IN_PROGRESS"
    assert performed.json()["samples"] == []

    technician_headers = create_staff_and_get_headers(db_connection, role="LAB_TECH")
    report = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={
            "items": [
                {"parameter": "Technique", "result_value": "PA erect chest radiograph"},
                {"parameter": "Findings", "result_value": "No focal consolidation. Cardiac silhouette normal."},
                {"parameter": "Impression", "result_value": "No acute cardiopulmonary abnormality."},
            ]
        },
        headers=technician_headers,
    )
    assert report.status_code == 200
    report_body = report.json()
    assert report_body["status"] == "RESULT_ENTERED"
    assert [r["parameter"] for r in report_body["results"]] == ["Technique", "Findings", "Impression"]

    radiologist_headers = create_staff_and_get_headers(db_connection, role="DOCTOR")
    verify = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=radiologist_headers,
    )
    assert verify.status_code == 200
    assert verify.json()["status"] == "VERIFIED"

    release = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/release",
        headers=radiologist_headers,
    )
    assert release.status_code == 200
    assert release.json()["status"] == "COMPLETED"

    doctor_view = client.get(f"/api/appointments/{appointment_id}/orders", headers=admin_headers)
    released_order = next(o for o in doctor_view.json() if o["id"] == order["id"])
    assert released_order["status"] == "COMPLETED"
    impression = next(r for r in released_order["results"] if r["parameter"] == "Impression")
    assert impression["result_value"] == "No acute cardiopulmonary abnormality."


def test_radiology_order_can_skip_straight_to_result_without_marking_in_progress(client, db_connection):
    # Collection/processing tracking is optional metadata, not a hard
    # gate -- same documented stance as LAB's own
    # test_result_can_be_entered_without_prior_collection.
    ctx = _checked_in_context(client, db_connection, "Dr. Radiology SkipProcessing")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_radiology_order(client, appointment_id, admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Findings", "result_value": "Normal study"}]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "RESULT_ENTERED"


def test_radiology_order_has_no_sample_collection(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Radiology NoSample")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_radiology_order(client, appointment_id, admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "N/A"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_radiology_verification_by_a_second_authorized_staff_account(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Radiology RBAC")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_radiology_order(client, appointment_id, admin_headers)

    tech_headers = create_staff_and_get_headers(db_connection, role="LAB_TECH")
    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Findings", "result_value": "Normal study"}]},
        headers=tech_headers,
    )

    # Same account cannot verify its own drafted report.
    same_account = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=tech_headers,
    )
    assert same_account.status_code == 403

    doctor_headers = create_staff_and_get_headers(db_connection, role="DOCTOR")
    verify = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=doctor_headers,
    )
    assert verify.status_code == 200


def test_pharmacist_cannot_verify_or_release_radiology_report(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Radiology PharmacistBlocked")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_radiology_order(client, appointment_id, admin_headers)
    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Findings", "result_value": "Normal study"}]},
        headers=admin_headers,
    )

    pharmacist_headers = create_staff_and_get_headers(db_connection, role="PHARMACIST")
    verify = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=pharmacist_headers,
    )
    assert verify.status_code == 403


def test_external_referral_order_unaffected_by_diagnostic_lifecycle(client, db_connection):
    # EXTERNAL_REFERRAL is neither LAB nor RADIOLOGY -- confirms the
    # third order type most likely to be confused with a "diagnostic"
    # order stays on the original one-step lifecycle too.
    ctx = _checked_in_context(client, db_connection, "Dr. Radiology ExternalReferral")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={
            "order_type": "EXTERNAL_REFERRAL",
            "description": "MRI Brain",
            "external_destination": "City Imaging Center",
        },
        headers=admin_headers,
    ).json()

    assert client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/start-processing",
        headers=admin_headers,
    ).status_code == 422

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Report", "result_value": "Received from City Imaging Center"}]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
