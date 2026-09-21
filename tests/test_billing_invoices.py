"""
Tests for the new invoice/charge/payment model (OPD/HIMS master spec
Phase 9, migrations/0033_billing_invoices.sql):
GET/PATCH /api/appointments/{id}/bill, .../bill/unbilled,
.../bill/void, .../bill/charges[/{id}/void],
.../bill/payments[/{id}/void|/refund].

Deliberately separate from, and never touching, the existing
appointments.consultation_fee/payment_status flow (tests/
test_consultation_payments.py) -- see billing_services.py's module
docstring for why.
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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9193{abs(hash(doctor_name)) % 10**8:08d}"},
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


def _add_charge(client, appointment_id, admin_headers, **overrides):
    payload = {"description": "CBC", "amount": 500}
    payload.update(overrides)
    return client.post(
        f"/api/appointments/{appointment_id}/bill/charges", json=payload, headers=admin_headers
    ).json()


# ---------------------------------------------------------------------
# Invoice basics -- not gated on CHECKED_IN, unlike every clinical write
# ---------------------------------------------------------------------


def test_get_invoice_creates_one_even_before_check_in(client, db_connection):
    ctx = _seed_pending_appointment(client, db_connection, "Dr. Bill NotCheckedIn")
    response = client.get(f"/api/appointments/{ctx['appointment']['id']}/bill", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OPEN"
    assert body["gross_amount"] == 0
    assert body["payment_status"] == "PAID"  # nothing owed yet


def test_get_invoice_is_idempotent(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill Idempotent")
    first = client.get(f"/api/appointments/{ctx['appointment']['id']}/bill", headers=ctx["admin_headers"]).json()
    second = client.get(f"/api/appointments/{ctx['appointment']['id']}/bill", headers=ctx["admin_headers"]).json()
    assert first["id"] == second["id"]


# ---------------------------------------------------------------------
# Charges
# ---------------------------------------------------------------------


def test_add_charge_requires_admin(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill ChargeStaffOnly")
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/bill/charges",
        json={"description": "CBC", "amount": 500},
        headers=staff_headers,
    )
    assert response.status_code == 403


def test_add_charge_updates_gross_and_balance(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill AddCharge")
    appointment_id = ctx["appointment"]["id"]
    invoice = _add_charge(client, appointment_id, ctx["admin_headers"], description="CBC", amount=500)
    assert invoice["gross_amount"] == 500
    assert invoice["net_amount"] == 500
    assert invoice["balance"] == 500
    assert invoice["payment_status"] == "UNPAID"
    assert len(invoice["charges"]) == 1


def test_charge_linked_to_order_from_a_different_encounter_is_rejected(client, db_connection):
    ctx_a = _checked_in_context(client, db_connection, "Dr. Bill CrossEncounterA")
    ctx_b = _checked_in_context(client, db_connection, "Dr. Bill CrossEncounterB")

    order = client.post(
        f"/api/appointments/{ctx_a['appointment']['id']}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=ctx_a["admin_headers"],
    ).json()

    response = client.post(
        f"/api/appointments/{ctx_b['appointment']['id']}/bill/charges",
        json={"description": "CBC", "amount": 500, "source_type": "LAB", "source_order_id": order["id"]},
        headers=ctx_b["admin_headers"],
    )
    assert response.status_code == 422


def test_charge_for_the_same_order_twice_is_rejected(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill DuplicateOrderCharge")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=admin_headers,
    ).json()

    payload = {"description": "CBC", "amount": 500, "source_type": "LAB", "source_order_id": order["id"]}
    first = client.post(f"/api/appointments/{appointment_id}/bill/charges", json=payload, headers=admin_headers)
    assert first.status_code == 200
    second = client.post(f"/api/appointments/{appointment_id}/bill/charges", json=payload, headers=admin_headers)
    assert second.status_code == 409


def test_unbilled_sources_lists_orders_and_drops_once_billed(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill Unbilled")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "RADIOLOGY", "description": "Chest X-ray"},
        headers=admin_headers,
    ).json()

    unbilled = client.get(f"/api/appointments/{appointment_id}/bill/unbilled", headers=admin_headers).json()
    assert any(o["order_id"] == order["id"] for o in unbilled["orders"])

    client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={
            "description": "Chest X-ray",
            "amount": 800,
            "source_type": "RADIOLOGY",
            "source_order_id": order["id"],
        },
        headers=admin_headers,
    )

    unbilled_after = client.get(f"/api/appointments/{appointment_id}/bill/unbilled", headers=admin_headers).json()
    assert not any(o["order_id"] == order["id"] for o in unbilled_after["orders"])


def test_void_charge_removes_it_from_gross(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill VoidCharge")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    invoice = _add_charge(client, appointment_id, admin_headers, amount=500)
    charge_id = invoice["charges"][0]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/bill/charges/{charge_id}/void",
        json={"reason": "Entered in error"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["gross_amount"] == 0
    assert body["charges"][0]["status"] == "VOIDED"


# ---------------------------------------------------------------------
# Discount / tax
# ---------------------------------------------------------------------


def test_discount_and_tax_computed_correctly(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill DiscountTax")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_charge(client, appointment_id, admin_headers, amount=1000)

    response = client.patch(
        f"/api/appointments/{appointment_id}/bill",
        json={"discount_amount": 100, "tax_rate": 18},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["gross_amount"] == 1000
    assert body["discount_amount"] == 100
    assert body["taxable_amount"] == 900
    assert body["tax_amount"] == 162.0  # 18% of 900
    assert body["net_amount"] == 1062.0
    assert body["balance"] == 1062.0


def test_update_invoice_terms_requires_admin(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill TermsStaffOnly")
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    response = client.patch(
        f"/api/appointments/{ctx['appointment']['id']}/bill",
        json={"tax_rate": 18},
        headers=staff_headers,
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------
# Payments -- partial, duplicate-prevention, refund, void
# ---------------------------------------------------------------------


def test_partial_payment_then_full_settlement(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill PartialPayment")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_charge(client, appointment_id, admin_headers, amount=1000)

    first = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 400, "method": "CASH"},
        headers=admin_headers,
    )
    assert first.status_code == 200
    assert first.json()["payment_status"] == "PARTIALLY_PAID"
    assert first.json()["balance"] == 600

    second = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 600, "method": "UPI", "transaction_id": "TXN-1"},
        headers=admin_headers,
    )
    assert second.status_code == 200
    assert second.json()["payment_status"] == "PAID"
    assert second.json()["balance"] == 0


def test_payment_exceeding_balance_is_rejected(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill Overpay")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_charge(client, appointment_id, admin_headers, amount=500)

    response = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 600, "method": "CASH"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_double_click_payment_is_rejected_by_balance_check(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill DoubleClick")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_charge(client, appointment_id, admin_headers, amount=500)

    first = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 500, "method": "CASH"},
        headers=admin_headers,
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 500, "method": "CASH"},
        headers=admin_headers,
    )
    assert second.status_code == 422


def test_duplicate_transaction_id_is_rejected(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill DupeTxn")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_charge(client, appointment_id, admin_headers, amount=1000)

    client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 400, "method": "UPI", "transaction_id": "TXN-DUP"},
        headers=admin_headers,
    )
    response = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 100, "method": "UPI", "transaction_id": "TXN-DUP"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_record_payment_allowed_for_plain_staff(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill StaffCanPay")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_charge(client, appointment_id, admin_headers, amount=200)

    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    response = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 200, "method": "CASH"},
        headers=staff_headers,
    )
    assert response.status_code == 200


def test_refund_payment(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill Refund")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_charge(client, appointment_id, admin_headers, amount=500)
    payment = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 500, "method": "CASH"},
        headers=admin_headers,
    ).json()["payments"][0]

    response = client.post(
        f"/api/appointments/{appointment_id}/bill/payments/{payment['id']}/refund",
        json={"amount": 200, "reason": "Partial service refused"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["paid_amount"] == 300
    assert body["balance"] == 200
    assert body["payment_status"] == "PARTIALLY_PAID"


def test_refund_exceeding_payment_is_rejected(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill RefundOver")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_charge(client, appointment_id, admin_headers, amount=500)
    payment = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 500, "method": "CASH"},
        headers=admin_headers,
    ).json()["payments"][0]

    response = client.post(
        f"/api/appointments/{appointment_id}/bill/payments/{payment['id']}/refund",
        json={"amount": 600, "reason": "Too much"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_void_payment_excludes_it_from_paid(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill VoidPayment")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_charge(client, appointment_id, admin_headers, amount=500)
    payment = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 500, "method": "CASH"},
        headers=admin_headers,
    ).json()["payments"][0]

    response = client.post(
        f"/api/appointments/{appointment_id}/bill/payments/{payment['id']}/void",
        json={"reason": "Recorded against the wrong patient"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["paid_amount"] == 0
    assert body["balance"] == 500


# ---------------------------------------------------------------------
# Void invoice
# ---------------------------------------------------------------------


def test_void_invoice_blocked_once_payment_exists(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill VoidInvoiceBlocked")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_charge(client, appointment_id, admin_headers, amount=500)
    client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 500, "method": "CASH"},
        headers=admin_headers,
    )

    response = client.post(
        f"/api/appointments/{appointment_id}/bill/void",
        json={"reason": "Wrong patient"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_void_invoice_succeeds_before_any_payment(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Bill VoidInvoiceOk")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_charge(client, appointment_id, admin_headers, amount=500)

    response = client.post(
        f"/api/appointments/{appointment_id}/bill/void",
        json={"reason": "Duplicate bill"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "VOID"

    blocked = client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Late add", "amount": 100},
        headers=admin_headers,
    )
    assert blocked.status_code == 409


def test_billing_independent_of_existing_consultation_payment_flow(client, db_connection):
    # The existing check-in -> consultation-fee payment -> queue-token
    # flow (app/services/appointment_services.py) must keep working
    # completely unaffected by this new invoice existing alongside it.
    ctx = _checked_in_context(client, db_connection, "Dr. Bill Coexist")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    settle = client.post(f"/api/appointments/{appointment_id}/settle-free-visit", headers=admin_headers)
    assert settle.status_code == 200
    assert settle.json()["payment_status"] == "WAIVED"
    assert settle.json()["token_number"] is not None

    # New invoice is independent -- still its own, separate UNPAID/PAID
    # state, unaffected by the old system's WAIVED consultation fee.
    invoice = client.get(f"/api/appointments/{appointment_id}/bill", headers=admin_headers).json()
    assert invoice["gross_amount"] == 0

    _add_charge(client, appointment_id, admin_headers, amount=300)
    invoice_after = client.get(f"/api/appointments/{appointment_id}/bill", headers=admin_headers).json()
    assert invoice_after["balance"] == 300
