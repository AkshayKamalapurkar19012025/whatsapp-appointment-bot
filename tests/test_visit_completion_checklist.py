"""
Tests for GET /api/appointments/{id}/completion-checklist (master spec
audit gap #4, section 43's Visit Completion checklist): a read-only
precondition summary for "Mark completed", not a gate on it.
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
    response = client.post(
        f"/api/appointments/{created['id']}/confirm-and-checkin",
        headers=admin_headers,
    )
    assert response.status_code == 200

    return {"admin_headers": admin_headers, "seeded": seeded, "patient": patient, "appointment": created}


def test_checklist_all_unchecked_right_after_checkin(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Checklist Fresh")
    response = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/completion-checklist",
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    assert response.json() == {
        "consultation_completed": False,
        "orders_created": False,
        "prescription_created": False,
        "billing_completed": False,
        "payment_completed": False,
        "follow_up_scheduled": False,
    }


def test_checklist_reflects_completed_consultation_and_follow_up(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Checklist Consult")
    appointment_id = ctx["appointment"]["id"]
    headers = ctx["admin_headers"]

    client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={
            "chief_complaint": "Fever",
            "diagnosis": "Viral fever",
            "follow_up_date": (date.today() + timedelta(days=7)).isoformat(),
        },
        headers=headers,
    )
    complete = client.post(f"/api/appointments/{appointment_id}/consultation/complete", headers=headers)
    assert complete.status_code == 200

    checklist = client.get(
        f"/api/appointments/{appointment_id}/completion-checklist", headers=headers
    ).json()
    assert checklist["consultation_completed"] is True
    assert checklist["follow_up_scheduled"] is True
    assert checklist["orders_created"] is False
    assert checklist["prescription_created"] is False
    assert checklist["billing_completed"] is False
    assert checklist["payment_completed"] is False


def test_checklist_reflects_orders_prescription_billing_and_payment(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Checklist Full")
    appointment_id = ctx["appointment"]["id"]
    headers = ctx["admin_headers"]

    order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=headers,
    )
    assert order.status_code == 200

    item = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Paracetamol", "quantity": 10},
        headers=headers,
    )
    assert item.status_code == 200

    charge = client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Consultation fee", "amount": 500, "source_type": "CONSULTATION"},
        headers=headers,
    )
    assert charge.status_code == 200

    payment = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 500, "method": "CASH"},
        headers=headers,
    )
    assert payment.status_code == 200

    # bill/payments records a payment against the new invoice/charge
    # model, but payment_completed reads appointments.payment_status --
    # the same field the front-desk queue already gates "Mark completed"
    # on -- which only the queue-facing consultation-payment action
    # sets. Record one here purely to exercise that field for this test.
    consultation_payment = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=headers,
    )
    assert consultation_payment.status_code == 200

    checklist = client.get(
        f"/api/appointments/{appointment_id}/completion-checklist", headers=headers
    ).json()
    assert checklist["orders_created"] is True
    assert checklist["prescription_created"] is True
    assert checklist["billing_completed"] is True
    assert checklist["payment_completed"] is True


def test_checklist_requires_staff_authentication(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Checklist Auth")
    response = client.get(f"/api/appointments/{ctx['appointment']['id']}/completion-checklist")
    assert response.status_code == 401


def test_checklist_404_for_unknown_appointment(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/appointments/999999/completion-checklist", headers=admin_headers)
    assert response.status_code == 404


def test_completing_a_visit_is_never_blocked_by_an_unchecked_item(client, db_connection):
    """The checklist is a summary, not a gate -- master spec section 43
    asks for visibility, not a hard block, and Phase 11's Exception
    Engine already separately covers the "flag this as overdue" need.
    """
    ctx = _checked_in_context(client, db_connection, "Dr. Checklist Unblocked")
    appointment_id = ctx["appointment"]["id"]

    checklist = client.get(
        f"/api/appointments/{appointment_id}/completion-checklist", headers=ctx["admin_headers"]
    ).json()
    assert not any(checklist.values())

    complete = client.post(f"/api/appointments/{appointment_id}/complete", headers=ctx["admin_headers"])
    assert complete.status_code == 200
    assert complete.json()["status"] == "COMPLETED"
