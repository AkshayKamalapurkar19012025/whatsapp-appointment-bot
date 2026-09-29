"""
Proves docs/OPD_HIMS_ARCHITECTURE_AUDIT.md's ADR-009 fix (Option B,
migrations/0058_consultation_fee_ledger_mirror.sql): one OPD visit with
a consultation fee collected at check-in (the legacy
appointments.payment_status ledger -- "Ledger A", migrations/0018-0026)
plus one lab charge billed and paid through the encounter invoice (the
invoices/charges/payments model -- "Ledger B", migration
0033_billing_invoices.sql) must now report the same true total on both
of the two live reporting surfaces that previously disagreed:

  * GET /api/dashboard/billing (app/api/dashboard.py:155) -- now reads
    the mirrored payment (app/services/billing_services.py's
    mirror_consultation_payment), not appointments.payment_status
    directly, for its collections figures.
  * GET /api/billing/payments (app/api/billing_history.py,
    app/services/billing_history_service.py:113) -- unchanged, already
    Ledger-B-only; now also sees the mirrored consultation-fee payment
    alongside the lab-charge payment it already saw.

This is this ADR's own acceptance criterion (docs/
OPD_HIMS_ARCHITECTURE_AUDIT.md, second/third addendum): this test
passing with no xfail marker. Before migration 0058 and the mirror
calls in appointment_services.py existed, this test failed exactly as
predicted (500 vs. 1200 vs. the true 1700) -- see the audit doc's
addenda for that proof.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def test_dashboard_billing_and_payment_history_agree_on_one_visits_total(
    client, db_connection
):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Ledger Gap",
        department_name="Ledger Gap Dept",
        appointment_type_name="Ledger Gap Type",
    )

    consultation_fee = 500
    lab_charge = 1200
    true_total_collected = consultation_fee + lab_charge

    client.put(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{seeded['appointment_type_id']}",
        json={"duration_minutes": 30, "consultation_fee": consultation_fee},
        headers=admin_headers,
    )

    patient = client.post(
        "/api/patients",
        json={"name": "Ledger Gap Patient", "whatsapp_number": "+919600009999"},
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
    appointment_id = appointment["id"]

    checkin = client.post(
        f"/api/appointments/{appointment_id}/confirm-and-checkin",
        headers=admin_headers,
    )
    assert checkin.status_code == 200

    # Ledger A: the consultation fee, collected at check-in, exactly as
    # every real OPD front desk does it (tests/test_consultation_payments.py).
    fee_payment = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert fee_payment.status_code == 200
    assert float(fee_payment.json()["payment_amount"]) == consultation_fee

    # Ledger B: a lab charge billed and paid on the same visit's
    # encounter invoice, exactly as every real OPD lab charge is
    # (tests/test_billing_invoices.py).
    client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "CBC", "amount": lab_charge},
        headers=admin_headers,
    )
    bill_payment = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": lab_charge, "method": "CASH"},
        headers=admin_headers,
    )
    assert bill_payment.status_code == 200
    assert bill_payment.json()["payment_status"] == "PAID"

    # What a hospital administrator actually sees now, on the two live
    # reporting screens that previously disagreed:
    dashboard_billing = client.get(
        "/api/dashboard/billing", headers=admin_headers
    ).json()
    payment_history = client.get(
        "/api/billing/payments", headers=admin_headers
    ).json()

    dashboard_reported_total = float(dashboard_billing["total_collected"])
    payment_history_reported_total = sum(
        float(item["amount"]) for item in payment_history["items"]
    )

    # Payment History is the actual fix: it already saw the lab charge
    # (Ledger B), and now also sees the mirrored consultation-fee
    # payment (ADR-009 Option B, migrations/0058) -- so it reports the
    # visit's true total, 1700, not 1200.
    assert payment_history_reported_total == true_total_collected, (
        f"Payment History shows {payment_history_reported_total}, "
        f"expected the true total {true_total_collected} (consultation "
        f"fee + lab charge) now that the consultation fee is mirrored"
    )

    # The Dashboard's "Billing" panel is deliberately scoped to
    # consultation-fee reconciliation only (BillingPanel.tsx's own
    # copy: "Consultation-fee collections... not lab, radiology,
    # pharmacy, or package charges") -- it is not supposed to include
    # the lab charge, so it correctly continues to report only the
    # consultation fee. Asserted explicitly, not just left unchecked,
    # so a future change that accidentally widens or narrows this
    # panel's scope gets caught here rather than silently drifting.
    assert dashboard_reported_total == consultation_fee, (
        f"Dashboard billing report shows {dashboard_reported_total}, "
        f"expected exactly the consultation fee {consultation_fee} -- "
        f"this panel is scoped to consultation-fee reconciliation only, "
        f"the lab charge should not appear here"
    )
