"""
Proves docs/OPD_HIMS_ARCHITECTURE_AUDIT.md's P0 finding with a real,
running scenario rather than a hypothetical: one OPD visit with a
consultation fee collected at check-in (the legacy
appointments.payment_status ledger -- "Ledger A", migrations/0018-0026)
plus one lab charge billed and paid through the encounter invoice (the
invoices/charges/payments model -- "Ledger B", migration
0033_billing_invoices.sql).

This is not a hypothetical or an IPD-driven concern: both ledgers are
exercised by pure, today's OPD usage. tests/test_billing_invoices.py's
own module docstring already states the two are "deliberately separate
from, and never touching" each other; this test shows what that
separation actually produces when both are read back through the two
live reporting surfaces that exist today:

  * GET /api/dashboard/billing (app/api/dashboard.py:155) -- reads only
    appointments.payment_status/payment_amount (Ledger A).
  * GET /api/billing/payments (app/api/billing_history.py,
    app/services/billing_history_service.py:113) -- reads only
    invoices/charges/payments (Ledger B).

Expected (asserted below, and currently FAILING): a hospital's true
total collected for this visit is consultation_fee + lab_charge. Marked
xfail(strict=True) per this codebase's own established convention for a
confirmed, reproducible architectural gap that is tracked but not yet
fixed (see tests/test_concurrency.py's history for the same pattern) --
this test is expected to start passing, and the xfail marker to be
removed, once docs/OPD_HIMS_ARCHITECTURE_AUDIT.md's ADR-009 (billing
ledger unification) is implemented.
"""

from datetime import date, timedelta

import pytest

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Confirmed, reproducible gap: the consultation fee (Ledger A, "
        "appointments.payment_status) and the lab charge (Ledger B, "
        "invoices/charges/payments) are each fully visible only to a "
        "different reporting endpoint, so neither GET /api/dashboard/"
        "billing nor GET /api/billing/payments reports this "
        "visit's true total collected. See docs/"
        "OPD_HIMS_ARCHITECTURE_AUDIT.md ADR-009."
    ),
)
def test_dashboard_billing_and_payment_history_disagree_on_one_visits_total(
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

    # What a hospital administrator actually sees today, on the two
    # live reporting screens that exist for exactly this purpose:
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

    # This is the P0 finding: neither report reflects the true total
    # collected for this one visit. The Dashboard's billing report only
    # ever sees Ledger A (the consultation fee); Payment History only
    # ever sees Ledger B (the lab charge). Today's actual values:
    #   dashboard_reported_total       == 500  (consultation fee only)
    #   payment_history_reported_total == 1200 (lab charge only)
    #   true_total_collected           == 1700 (neither report shows this)
    assert dashboard_reported_total == true_total_collected, (
        f"Dashboard billing report shows {dashboard_reported_total}, "
        f"missing the {lab_charge} lab charge recorded through Ledger B"
    )
    assert payment_history_reported_total == true_total_collected, (
        f"Payment History shows {payment_history_reported_total}, "
        f"missing the {consultation_fee} consultation fee recorded "
        f"through Ledger A"
    )
