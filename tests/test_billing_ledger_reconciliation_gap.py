"""
Proves the billing-ledger split is closed on both of the live reporting
surfaces that originally disagreed on one visit's total: a consultation
fee collected at check-in (the legacy appointments.payment_status
ledger -- "Ledger A", migrations/0018-0026) plus one lab charge billed
and paid through the encounter invoice (the invoices/charges/payments
model -- "Ledger B", migration 0033_billing_invoices.sql).

Two independent, differently-mechanized fixes both had to land, and
both are exercised here together:

  * GET /api/billing/payments (app/api/billing_history.py,
    app/services/billing_history_service.py:113) -- fixed by ADR-009
    Option B (docs/OPD_HIMS_ARCHITECTURE_AUDIT.md,
    migrations/0058_consultation_fee_ledger_mirror.sql): every Ledger A
    payment event is mirrored into a Ledger B charge/payment
    (app/services/billing_services.py's mirror_consultation_*
    functions), so this Ledger-B-only endpoint now sees the
    consultation fee too, alongside the lab charge it already saw.
  * GET /api/dashboard/billing (app/api/dashboard.py) -- fixed
    independently by Phase 9, Option C (docs/architecture/
    BILLING_LEDGERS.md, merged to main as PR #120 before this branch's
    Option B work landed): this endpoint combines Ledger A and Ledger B
    at *read* time, querying each ledger's own original columns
    directly rather than relying on Option B's mirror.

The two fixes were built independently and collided as a real merge
conflict when this branch caught up with main (both touched
get_billing_report). Reconciling them required patching Option C's
Ledger B queries (_ledger_b_collections_by_method/_by_doctor/
_outstanding) to exclude legacy_appointment_id IS NOT NULL rows --
without that exclusion, a mirrored consultation-fee payment would be
counted once via Ledger A's own direct query and again via Option C's
unfiltered Ledger B query, inflating this visit's total to 2000 instead
of 1700. This test's dashboard assertion exists specifically to catch
that regression, not just to prove the original gap is closed.
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

    # Payment History: sees the lab charge (Ledger B) directly, and now
    # also sees the mirrored consultation-fee payment (ADR-009 Option B,
    # migrations/0058) -- so it reports the visit's true total, 1700,
    # not 1200.
    assert payment_history_reported_total == true_total_collected, (
        f"Payment History shows {payment_history_reported_total}, "
        f"expected the true total {true_total_collected} (consultation "
        f"fee + lab charge) now that the consultation fee is mirrored"
    )

    # Dashboard: a completely independent fix (Phase 9, Option C,
    # docs/architecture/BILLING_LEDGERS.md, already merged to main as
    # PR #120 before this branch's Option B work landed) combines
    # Ledger A and Ledger B at *read* time for this specific endpoint --
    # so it also reports the true total, 1700, via its own mechanism,
    # not via Option B's mirror. Asserted equal to true_total_collected,
    # not to consultation_fee alone, specifically to catch the
    # double-counting bug this reconciliation had to fix: without the
    # legacy_appointment_id exclusion filters in
    # app/api/dashboard.py's _ledger_b_* helpers, this would read 2000
    # (500 counted twice + 1200), not 1700.
    assert dashboard_reported_total == true_total_collected, (
        f"Dashboard billing report shows {dashboard_reported_total}, "
        f"expected the true total {true_total_collected} -- either "
        f"Option C's read-time ledger combination or the "
        f"legacy_appointment_id double-counting guard has regressed"
    )

    # Stronger guard: the breakdown itself must attribute the fee to
    # Ledger A and the lab charge to Ledger B, not e.g. 1000/700 or any
    # other split that still happens to sum to 1700 -- a subtler
    # double-counting bug (one side over-counting, the other under-
    # counting by the same amount) would pass the total-only assertion
    # above but fail this one.
    breakdown = dashboard_billing["ledger_breakdown"]
    assert float(breakdown["consultation_fee"]) == consultation_fee
    assert float(breakdown["itemized_billing"]) == lab_charge
