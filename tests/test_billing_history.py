"""
Tests for GET /api/billing/invoices, GET /api/billing/payments (master
spec audit "subsequent gaps" list, screens 29-30): cross-visit
billing/payment history, paginated and filterable by patient name and
date range.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _checked_in_context(client, db_connection, doctor_name: str, patient_name: str) -> dict:
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
        json={"name": patient_name, "whatsapp_number": f"+9196{abs(hash(patient_name)) % 10**8:08d}"},
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
    response = client.post(
        f"/api/appointments/{created['id']}/confirm-and-checkin",
        headers=admin_headers,
    )
    assert response.status_code == 200
    return {"admin_headers": admin_headers, "patient": patient, "appointment": created}


def test_billing_history_lists_invoice_with_charge_and_payment(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Billing History", "Billing History Patient")
    appointment_id = ctx["appointment"]["id"]
    headers = ctx["admin_headers"]

    client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Consultation", "amount": 500},
        headers=headers,
    )
    client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 300, "method": "CASH"},
        headers=headers,
    )

    response = client.get("/api/billing/invoices", params={"patient_name": "Billing History Patient"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    match = next(i for i in body["items"] if i["patient_name"] == "Billing History Patient")
    assert match["gross_amount"] == 500
    assert match["paid_amount"] == 300
    assert match["balance"] == 200
    assert match["payment_status"] == "PARTIALLY_PAID"
    assert match["doctor_name"] == "Dr. Billing History"
    assert match["appointment_id"] == appointment_id


def test_payment_history_lists_recorded_payment(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Payment History", "Payment History Patient")
    appointment_id = ctx["appointment"]["id"]
    headers = ctx["admin_headers"]

    client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Consultation", "amount": 800},
        headers=headers,
    )
    client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 800, "method": "UPI"},
        headers=headers,
    )

    response = client.get(
        "/api/billing/payments", params={"patient_name": "Payment History Patient"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    match = next(p for p in body["items"] if p["patient_name"] == "Payment History Patient")
    assert match["amount"] == 800
    assert match["method"] == "UPI"
    assert match["status"] == "COMPLETED"


def test_billing_history_paginates(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Billing Page", "Billing Page Patient")
    headers = ctx["admin_headers"]

    response = client.get("/api/billing/invoices", params={"limit": 1, "offset": 0}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) <= 1
    assert body["limit"] == 1
    assert body["offset"] == 0


def test_billing_history_requires_authentication(client, db_connection):
    response = client.get("/api/billing/invoices")
    assert response.status_code == 401

    response = client.get("/api/billing/payments")
    assert response.status_code == 401


def test_payment_history_filters_by_method(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Payment Method Filter", "Payment Method Filter Patient")
    appointment_id = ctx["appointment"]["id"]
    headers = ctx["admin_headers"]

    client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Consultation", "amount": 400},
        headers=headers,
    )
    client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 400, "method": "CARD"},
        headers=headers,
    )

    matching = client.get(
        "/api/billing/payments",
        params={"patient_name": "Payment Method Filter Patient", "method": "CARD"},
        headers=headers,
    ).json()
    assert any(p["patient_name"] == "Payment Method Filter Patient" for p in matching["items"])

    non_matching = client.get(
        "/api/billing/payments",
        params={"patient_name": "Payment Method Filter Patient", "method": "CASH"},
        headers=headers,
    ).json()
    assert not any(p["patient_name"] == "Payment Method Filter Patient" for p in non_matching["items"])
