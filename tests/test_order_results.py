"""
Tests for order results (OPD/HIMS master spec Phase 7):
POST /api/appointments/{id}/orders/{order_id}/result, and the `results`
field GET .../orders now embeds on each order.
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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9195{abs(hash(doctor_name)) % 10**8:08d}"},
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


def _create_order(client, appointment_id, admin_headers, **overrides):
    payload = {"order_type": "LAB", "description": "CBC"}
    payload.update(overrides)
    return client.post(
        f"/api/appointments/{appointment_id}/orders", json=payload, headers=admin_headers
    ).json()


# ---------------------------------------------------------------------
# Recording a result
# ---------------------------------------------------------------------


def test_recording_a_result_completes_the_order(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Results Lab")
    appointment_id = ctx["appointment"]["id"]
    order = _create_order(client, appointment_id, ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={
            "items": [
                {"parameter": "Hemoglobin", "result_value": "13.8", "unit": "g/dL", "reference_range": "13-17"},
                {"parameter": "WBC", "result_value": "7200", "unit": "/uL", "reference_range": "4000-11000"},
                {
                    "parameter": "Platelets",
                    "result_value": "2.4",
                    "unit": "lakh/uL",
                    "reference_range": "1.5-4.5",
                    "is_abnormal": True,
                },
            ]
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["completed_at"] is not None
    assert len(body["results"]) == 3
    assert body["results"][0]["parameter"] == "Hemoglobin"
    assert body["results"][2]["is_abnormal"] is True
    assert body["results"][0]["is_critical"] is False


def test_result_with_no_unit_code(client, db_connection):
    """OPD/HIMS interoperability master prompt Phase 7: a result with no
    unit coding at all is the ordinary, unchanged case."""
    ctx = _checked_in_context(client, db_connection, "Dr. Results UnitCode None")
    appointment_id = ctx["appointment"]["id"]
    order = _create_order(client, appointment_id, ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.8", "unit": "g/dL"}]},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["unit"] == "g/dL"
    assert result["unit_system"] is None
    assert result["unit_code"] is None


def test_result_with_unit_code(client, db_connection):
    """A full, structurally valid unit_system/unit_code pair alongside
    the existing free-text unit. Phase 7 never invents or validates the
    code's real-world meaning -- this is exactly what the caller
    supplied."""
    ctx = _checked_in_context(client, db_connection, "Dr. Results UnitCode Full")
    appointment_id = ctx["appointment"]["id"]
    order = _create_order(client, appointment_id, ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={
            "items": [
                {
                    "parameter": "Hemoglobin",
                    "result_value": "13.8",
                    "unit": "g/dL",
                    "unit_system": "UCUM",
                    "unit_code": "g/dL",
                }
            ]
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["unit_system"] == "UCUM"
    assert result["unit_code"] == "g/dL"


def test_result_rejects_unit_code_without_system(client, db_connection):
    """docs/OPD_HIMS_STANDARDS_READINESS.md S9's own structural rule: a
    code without a system is meaningless -- rejected at the API layer
    before it would even reach the DB's own CHECK constraint."""
    ctx = _checked_in_context(client, db_connection, "Dr. Results UnitCode NoSystem")
    appointment_id = ctx["appointment"]["id"]
    order = _create_order(client, appointment_id, ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={
            "items": [
                {"parameter": "Hemoglobin", "result_value": "13.8", "unit": "g/dL", "unit_code": "g/dL"}
            ]
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 422


def test_result_allows_unit_system_without_code(client, db_connection):
    """No rule requires the reverse (system without a code yet) -- this
    phase deliberately doesn't invent one that isn't there."""
    ctx = _checked_in_context(client, db_connection, "Dr. Results UnitSystem Only")
    appointment_id = ctx["appointment"]["id"]
    order = _create_order(client, appointment_id, ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={
            "items": [
                {"parameter": "Hemoglobin", "result_value": "13.8", "unit": "g/dL", "unit_system": "UCUM"}
            ]
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["unit_system"] == "UCUM"
    assert result["unit_code"] is None


def test_different_parameters_in_the_same_batch_have_independent_unit_codes(client, db_connection):
    """unit_system/unit_code live on order_results (per parameter row),
    not on orders (per test/panel) -- confirms two parameters in the
    same panel can carry two different coded units, matching how
    Hemoglobin (g/dL) and WBC (/uL) already carry two different
    free-text units in the same batch."""
    ctx = _checked_in_context(client, db_connection, "Dr. Results UnitCode PerParam")
    appointment_id = ctx["appointment"]["id"]
    order = _create_order(client, appointment_id, ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={
            "items": [
                {
                    "parameter": "Hemoglobin",
                    "result_value": "13.8",
                    "unit": "g/dL",
                    "unit_system": "UCUM",
                    "unit_code": "g/dL",
                },
                {
                    "parameter": "WBC",
                    "result_value": "7200",
                    "unit": "/uL",
                    "unit_system": "UCUM",
                    "unit_code": "/uL",
                },
            ]
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["unit_code"] == "g/dL"
    assert results[1]["unit_code"] == "/uL"


def test_recording_a_result_requires_at_least_one_item(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Results Empty")
    appointment_id = ctx["appointment"]["id"]
    order = _create_order(client, appointment_id, ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": []},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 422


def test_recording_a_result_is_not_gated_on_checked_in(client, db_connection):
    # Deliberately different from vitals/consultation/order-creation: a
    # result routinely arrives after the visit itself has closed out.
    ctx = _checked_in_context(client, db_connection, "Dr. Results AfterComplete")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_order(client, appointment_id, admin_headers)

    client.post(f"/api/appointments/{appointment_id}/complete", headers=admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.8"}]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"


def test_cannot_record_a_result_for_a_cancelled_order(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Results Cancelled")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_order(client, appointment_id, admin_headers)

    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/cancel",
        json={"reason": "No longer needed"},
        headers=admin_headers,
    )

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.8"}]},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_cannot_record_a_second_result_for_an_already_completed_order(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Results DoubleRecord")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_order(client, appointment_id, admin_headers)

    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.8"}]},
        headers=admin_headers,
    )
    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "14.1"}]},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_result_requires_staff_auth(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Results Auth")
    appointment_id = ctx["appointment"]["id"]
    order = _create_order(client, appointment_id, ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.8"}]},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------
# Listing -- results embedded on each order
# ---------------------------------------------------------------------


def test_listing_orders_embeds_results(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Results List")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    with_result = _create_order(client, appointment_id, admin_headers, description="CBC")
    without_result = _create_order(client, appointment_id, admin_headers, description="ECG")

    client.post(
        f"/api/appointments/{appointment_id}/orders/{with_result['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.8"}]},
        headers=admin_headers,
    )

    response = client.get(f"/api/appointments/{appointment_id}/orders", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()

    by_id = {o["id"]: o for o in body}
    assert len(by_id[with_result["id"]]["results"]) == 1
    assert by_id[with_result["id"]]["results"][0]["parameter"] == "Hemoglobin"
    assert by_id[without_result["id"]]["results"] == []
