"""
Tests for GET /api/patients/{id}/timeline (OPD/HIMS master spec Phase 10,
section 44: Patient 360 / unified timeline) -- app/services/
patient_timeline_service.py's read-only aggregation over Phases 3/5-9's
existing tables.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _seed_checked_in_patient(client, db_connection, doctor_name: str) -> dict:
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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9194{abs(hash(doctor_name)) % 10**8:08d}"},
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
    checkin = client.post(
        f"/api/appointments/{created['id']}/confirm-and-checkin", headers=admin_headers
    )
    assert checkin.status_code == 200

    return {
        "admin_headers": admin_headers,
        "seeded": seeded,
        "patient": patient,
        "appointment": created,
    }


def test_timeline_for_patient_with_no_visits_is_empty(db_connection, client):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = client.post(
        "/api/patients",
        json={"name": "No Visits Patient", "whatsapp_number": "+919400000001"},
        headers=admin_headers,
    ).json()

    response = client.get(f"/api/patients/{patient['id']}/timeline", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["patient_id"] == patient["id"]
    assert body["visits"] == []
    assert body["redirected_from"] is None


def test_timeline_404_for_nonexistent_patient(db_connection, client):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/patients/999999/timeline", headers=admin_headers)
    assert response.status_code == 404


def test_timeline_requires_authentication(client):
    response = client.get("/api/patients/1/timeline")
    assert response.status_code == 401


def test_timeline_includes_full_visit_detail(client, db_connection):
    ctx = _seed_checked_in_patient(client, db_connection, "Dr. Timeline Full")
    admin_headers = ctx["admin_headers"]
    appointment_id = ctx["appointment"]["id"]
    patient_id = ctx["patient"]["id"]

    # Vitals
    vitals_resp = client.post(
        f"/api/appointments/{appointment_id}/vitals",
        json={"bp_systolic": 120, "bp_diastolic": 80, "pulse": 72, "chief_complaint": "Fever"},
        headers=admin_headers,
    )
    assert vitals_resp.status_code == 200

    # Consultation
    save_resp = client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={"chief_complaint": "Fever", "diagnosis": "Viral fever"},
        headers=admin_headers,
    )
    assert save_resp.status_code == 200
    complete_resp = client.post(
        f"/api/appointments/{appointment_id}/consultation/complete", headers=admin_headers
    )
    assert complete_resp.status_code == 200

    # Order + result
    order_resp = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=admin_headers,
    )
    assert order_resp.status_code == 200
    order_id = order_resp.json()["id"]
    result_resp = client.post(
        f"/api/appointments/{appointment_id}/orders/{order_id}/result",
        json={"items": [{"parameter": "WBC", "result_value": "7.2", "unit": "10^3/uL"}]},
        headers=admin_headers,
    )
    assert result_resp.status_code == 200

    # Prescription + item + dispense
    item_resp = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Paracetamol", "quantity": 10},
        headers=admin_headers,
    )
    assert item_resp.status_code == 200
    item_id = item_resp.json()["items"][0]["id"]
    prescribe_resp = client.post(
        f"/api/appointments/{appointment_id}/prescription/prescribe", headers=admin_headers
    )
    assert prescribe_resp.status_code == 200
    dispense_resp = client.post(
        f"/api/pharmacy/items/{item_id}/dispense",
        json={"quantity": 10, "unit_price": 2.5},
        headers=admin_headers,
    )
    assert dispense_resp.status_code == 200

    # Billing: charge + payment
    charge_resp = client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Consultation fee", "amount": 500},
        headers=admin_headers,
    )
    assert charge_resp.status_code == 200
    payment_resp = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 500, "method": "CASH"},
        headers=admin_headers,
    )
    assert payment_resp.status_code == 200

    response = client.get(f"/api/patients/{patient_id}/timeline", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["patient_id"] == patient_id
    assert len(body["visits"]) == 1
    visit = body["visits"][0]

    assert visit["appointment_id"] == appointment_id
    assert visit["status"] == "OPEN"
    assert visit["doctor_name"] == "Dr. Timeline Full"

    assert len(visit["vitals"]) == 1
    assert visit["vitals"][0]["chief_complaint"] == "Fever"

    assert visit["consultation"] is not None
    assert visit["consultation"]["diagnosis"] == "Viral fever"
    assert visit["consultation"]["status"] == "COMPLETED"

    assert len(visit["orders"]) == 1
    assert visit["orders"][0]["description"] == "CBC"
    assert len(visit["orders"][0]["results"]) == 1
    assert visit["orders"][0]["results"][0]["parameter"] == "WBC"

    assert visit["prescription"] is not None
    assert visit["prescription"]["status"] == "PRESCRIBED"
    assert len(visit["prescription"]["items"]) == 1
    item = visit["prescription"]["items"][0]
    assert item["medicine_name"] == "Paracetamol"
    assert item["quantity_dispensed"] == 10
    assert len(item["dispenses"]) == 1
    assert item["dispenses"][0]["quantity"] == 10

    assert visit["invoice"] is not None
    assert len(visit["invoice"]["charges"]) == 1
    assert len(visit["invoice"]["payments"]) == 1
    assert visit["invoice"]["payments"][0]["amount"] == 500


def test_timeline_orders_visits_newest_first(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Timeline Order",
        department_name="Timeline Order Dept",
        appointment_type_name="Timeline Order Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Repeat Visit Patient", "whatsapp_number": "+919400000002"},
        headers=admin_headers,
    ).json()

    first_date = _next_weekday(date.today() + timedelta(days=5))
    second_date = _next_weekday(date.today() + timedelta(days=12))

    first = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{first_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    second = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{second_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()

    response = client.get(f"/api/patients/{patient['id']}/timeline", headers=admin_headers)
    assert response.status_code == 200
    visits = response.json()["visits"]
    assert len(visits) == 2
    # Most recent visit (later start_at, and therefore later started_at
    # once checked in -- but neither is checked in here, so this simply
    # confirms the query orders by e.started_at DESC, matching the
    # encounter opened later) comes first.
    assert visits[0]["appointment_id"] == second["id"]
    assert visits[1]["appointment_id"] == first["id"]


def test_timeline_follows_merge_redirect(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Timeline Merge",
        department_name="Timeline Merge Dept",
        appointment_type_name="Timeline Merge Type",
    )
    retired = client.post(
        "/api/patients",
        json={"name": "Duplicate Identity", "whatsapp_number": "+919400000003"},
        headers=admin_headers,
    ).json()
    survivor = client.post(
        "/api/patients",
        json={"name": "Real Identity", "whatsapp_number": "+919400000004"},
        headers=admin_headers,
    ).json()

    visit_date = _next_weekday(date.today() + timedelta(days=6))
    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": retired["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{visit_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()

    merge_resp = client.post(
        f"/api/patients/{survivor['id']}/merge",
        json={"retired_patient_id": retired["id"]},
        headers=admin_headers,
    )
    assert merge_resp.status_code == 200

    response = client.get(f"/api/patients/{retired['id']}/timeline", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["patient_id"] == survivor["id"]
    assert body["redirected_from"]["patient_id"] == retired["id"]
    assert len(body["visits"]) == 1
    assert body["visits"][0]["appointment_id"] == appointment["id"]


def test_timeline_visible_to_plain_staff(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = client.post(
        "/api/patients",
        json={"name": "Staff Readable Patient", "whatsapp_number": "+919400000005"},
        headers=admin_headers,
    ).json()

    response = client.get(f"/api/patients/{patient['id']}/timeline", headers=staff_headers)
    assert response.status_code == 200
