"""
Proves the billing-ledger split is closed on both of the live reporting
surfaces that originally disagreed on one visit's total: a consultation
fee collected at check-in (the legacy appointments.payment_status
ledger -- "Ledger A", migrations/0018-0026) plus one lab charge billed
and paid through the encounter invoice (the invoices/charges/payments
model -- "Ledger B", migration 0033_billing_invoices.sql).

Two independent fixes both had to land, and both are exercised here
together:

  * GET /api/billing/payments (app/api/billing_history.py,
    app/services/billing_history_service.py:113) -- sees the
    consultation fee because Phase 10 (docs/architecture/
    BILLING_LEDGER_UNIFICATION.md, migrations/0058_billing_ledger_
    unification.sql) writes it directly into Ledger B
    (billing_services.record_consultation_fee_payment_service), linked
    via appointments.consultation_payment_id -- this Ledger-B-only,
    already-ledger-2-native endpoint needed no code change at all to
    see it, alongside the lab charge it already saw.
  * GET /api/dashboard/billing (app/api/dashboard.py) -- fixed by
    Phase 9, Option C (docs/architecture/BILLING_LEDGERS.md, merged to
    main as PR #120): this endpoint combines Ledger A and Ledger B at
    *read* time, with Ledger A's own side reading the shared
    EFFECTIVE_PAYMENT_*_SQL fragment (which follows consultation_
    payment_id to the real payment Phase 10 wrote).

A separately-developed, uncoordinated dual-write mirror ("ADR-009
Option B" in docs/OPD_HIMS_ARCHITECTURE_AUDIT.md, migrations/0058_
consultation_fee_ledger_mirror.sql -- a different 0058 file from Phase
10's) once also existed alongside Phase 10's direct write, and this
endpoint's Ledger B exclusion filters (_ledger_b_collections_by_method/
_by_doctor/_outstanding) were written against that mirror's
legacy_appointment_id tag. The mirror collided with Phase 10's own
write on charges_one_consultation_per_invoice and crashed every real
payment, so it was removed -- and the exclusion filters were corrected
to match what Phase 10 actually links (consultation_payment_id /
source_type = 'CONSULTATION') instead of the removed mirror's tag,
since without a correct exclusion this same double-counting bug
resurfaces (the real, unmirrored payment counted once via the
EFFECTIVE_PAYMENT_*_SQL fragment and again via an unfiltered Ledger B
query, inflating this visit's total to 2000 instead of 1700). This
test's dashboard assertion exists specifically to catch that class of
regression, not just to prove the original gap is closed -- see
docs/OPD_HIMS_ARCHITECTURE_AUDIT.md's Sixth Addendum for the incident.
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
