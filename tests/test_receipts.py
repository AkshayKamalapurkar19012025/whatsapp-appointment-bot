"""
Tests for the payment receipt (OPD/HIMS master spec Phase 13, section
42): GET/POST /api/appointments/{id}/bill/payments/{payment_id}/receipt[/send].
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


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
    checkin = client.post(f"/api/appointments/{created['id']}/confirm-and-checkin", headers=admin_headers)
    assert checkin.status_code == 200

    return {"admin_headers": admin_headers, "patient": patient, "appointment": created}


def _billed_and_paid_context(client, db_connection, doctor_name: str, *, amount=500) -> dict:
    ctx = _checked_in_context(client, db_connection, doctor_name)
    appointment_id = ctx["appointment"]["id"]
    client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Consultation fee", "amount": amount, "source_type": "CONSULTATION"},
        headers=ctx["admin_headers"],
    )
    payment = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": amount, "method": "UPI", "transaction_id": "TXN123"},
        headers=ctx["admin_headers"],
    ).json()
    ctx["payment_id"] = payment["payments"][-1]["id"]
    return ctx


def test_get_receipt(client, db_connection):
    ctx = _billed_and_paid_context(client, db_connection, "Dr. Receipt Basic")
    appointment_id = ctx["appointment"]["id"]

    response = client.get(
        f"/api/appointments/{appointment_id}/bill/payments/{ctx['payment_id']}/receipt",
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    body = response.json()

    assert body["patient_name"] == ctx["patient"]["name"]
    assert body["patient_uhid"] == ctx["patient"]["uhid"]
    assert body["hospital_name"]
    assert body["receipt_number"].startswith("RCPT2-")
    assert body["invoice_number"].startswith("BILL-")
    assert len(body["services"]) == 1
    assert body["services"][0]["description"] == "Consultation fee"
    assert body["gross_amount"] == 500
    assert body["net_amount"] == 500
    assert body["payment_amount"] == 500
    assert body["payment_method"] == "UPI"
    assert body["transaction_id"] == "TXN123"
    assert body["cashier"].startswith("seed-admin-")


def test_receipt_shows_this_payment_not_cumulative_paid(client, db_connection):
    """A second, partial payment's own receipt reports only what THAT
    transaction collected, not the invoice's running total."""
    ctx = _checked_in_context(client, db_connection, "Dr. Receipt Partial")
    appointment_id = ctx["appointment"]["id"]
    client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Consultation fee", "amount": 1000},
        headers=ctx["admin_headers"],
    )
    client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 400, "method": "CASH"},
        headers=ctx["admin_headers"],
    )
    second = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 600, "method": "CARD"},
        headers=ctx["admin_headers"],
    ).json()
    second_payment_id = second["payments"][-1]["id"]

    response = client.get(
        f"/api/appointments/{appointment_id}/bill/payments/{second_payment_id}/receipt",
        headers=ctx["admin_headers"],
    )
    body = response.json()
    assert body["payment_amount"] == 600
    assert body["payment_method"] == "CARD"
    # The bill-level totals are still the whole invoice's context.
    assert body["gross_amount"] == 1000
    assert body["net_amount"] == 1000


def test_receipt_reflects_refund(client, db_connection):
    ctx = _billed_and_paid_context(client, db_connection, "Dr. Receipt Refund")
    appointment_id = ctx["appointment"]["id"]
    client.post(
        f"/api/appointments/{appointment_id}/bill/payments/{ctx['payment_id']}/refund",
        json={"amount": 200, "reason": "Patient overcharged"},
        headers=ctx["admin_headers"],
    )

    response = client.get(
        f"/api/appointments/{appointment_id}/bill/payments/{ctx['payment_id']}/receipt",
        headers=ctx["admin_headers"],
    )
    assert response.json()["payment_amount"] == 300


def test_receipt_404_for_nonexistent_payment(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Receipt Missing")
    response = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/bill/payments/999999/receipt",
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 404


def test_receipt_requires_authentication(client):
    response = client.get("/api/appointments/1/bill/payments/1/receipt")
    assert response.status_code == 401


def test_send_receipt_records_mock_notification(client, db_connection):
    ctx = _billed_and_paid_context(client, db_connection, "Dr. Receipt Send")
    appointment_id = ctx["appointment"]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/bill/payments/{ctx['payment_id']}/receipt/send",
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    assert response.json() == {"sent": True}

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT message_body FROM mock_sms_outbox WHERE whatsapp_number = %s AND kind = 'RECEIPT'",
            (ctx["patient"]["whatsapp_number"],),
        )
        rows = cur.fetchall()

    assert len(rows) == 1
    (message_body,) = rows[0]
    assert "500.00" in message_body
    assert "UPI" in message_body
