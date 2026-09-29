"""
Phase 10B (Billing Ledger Coexistence, ADR-009 Option B) acceptance
tests for the specific gap this phase exists to close: before this
phase, GET /dashboard/billing's "collected" total only ever reflected
the consultation fee (Ledger A), even though a real visit routinely also
carries LAB/RADIOLOGY/PHARMACY/PROCEDURE charges that only ever existed
in Ledger B (invoices/charges/payments, migrations/0033) -- a patient
who paid ₹500 for the consultation and ₹1200 for a lab order showed as
"₹500 collected", silently dropping the ₹1200. See
docs/architecture/BILLING_LEDGER_COEXISTENCE.md for the full writeup
and the read-only reconciliation SQL this file's second half exercises.

This is a from-scratch replacement for the same-named file that existed
only on a separate investigation branch (never merged here) as an
xfail(strict=True) proof of the gap. There is nothing to "un-xfail" on
this branch -- app/api/dashboard.py's get_billing_report already reads
Ledger B for collections (see its own docstring), so this file goes
straight to proving the gap is actually closed, plus the reconciliation
invariant that has to hold for that fix to be trustworthy.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _checked_in_appointment(client, db_connection, admin_headers, seeded, patient_id, hour=9):
    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient_id,
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T{hour:02d}:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    response = client.post(f"/api/appointments/{created['id']}/confirm-and-checkin", headers=admin_headers)
    assert response.status_code == 200
    return created["id"]


def _set_visited_at_days_ago(db_connection, appointment_id, days_ago):
    """Same recipe as tests/test_consultation_payments.py's helper of the
    same name -- a fixed, safe mid-morning UTC anchor so the 3-day-
    revisit waiver eligibility check isn't sensitive to what time of day
    the suite happens to run."""
    anchor = datetime.now(dt_timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0)
    visited_at = anchor - timedelta(days=days_ago)
    with db_connection.cursor() as cur:
        cur.execute("UPDATE appointments SET visited_at = %s WHERE id = %s", (visited_at, appointment_id))
    db_connection.commit()


def test_dashboard_billing_total_unifies_consultation_and_other_charges(client, db_connection):
    """The exact scenario the gap was named for: Consultation ₹500 (paid
    through the legacy /payment endpoint, Ledger A + Ledger B mirror)
    plus a LAB charge ₹1200 (paid through the invoice/payments endpoint,
    Ledger B only) on the same encounter. Before Phase 10B,
    total_collected was 500. It must now be 1700."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Reconciliation Gap",
        department_name="Reconciliation Gap Dept", appointment_type_name="Reconciliation Gap Type",
    )
    client.put(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{seeded['appointment_type_id']}",
        json={"duration_minutes": 30, "consultation_fee": 500},
        headers=admin_headers,
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Reconciliation Gap Patient", "whatsapp_number": "+919711100001"},
        headers=admin_headers,
    ).json()
    appointment_id = _checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"])

    paid = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert paid.status_code == 200

    charge = client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "CBC + Lipid Panel", "amount": 1200, "source_type": "LAB"},
        headers=admin_headers,
    )
    assert charge.status_code == 200
    lab_payment = client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 1200, "method": "UPI"},
        headers=admin_headers,
    )
    assert lab_payment.status_code == 200
    assert lab_payment.json()["payment_status"] == "PAID"

    report = client.get("/api/dashboard/billing", headers=admin_headers).json()
    assert float(report["total_collected"]) == 1700.0

    methods = {row["method"]: float(row["amount"]) for row in report["collections_by_method"]}
    assert methods["CASH"] == 500.0
    assert methods["UPI"] == 1200.0


def test_dashboard_billing_total_reflects_refund_of_mirrored_consultation_payment(client, db_connection):
    """A refunded consultation fee must stop counting as collected in
    the unified Ledger B total too, not just in Ledger A -- proves the
    mirror's REFUNDED branch (billing_services.
    mirror_legacy_consultation_payment_service) actually reduces the
    payment's effective amount, not just Ledger A's own status word."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Reconciliation Refund",
        department_name="Reconciliation Refund Dept", appointment_type_name="Reconciliation Refund Type",
    )
    client.put(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{seeded['appointment_type_id']}",
        json={"duration_minutes": 30, "consultation_fee": 500},
        headers=admin_headers,
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Reconciliation Refund Patient", "whatsapp_number": "+919711100002"},
        headers=admin_headers,
    ).json()
    appointment_id = _checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    refunded = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 500, "reason": "Reconciliation refund test"},
        headers=admin_headers,
    )
    assert refunded.status_code == 200

    report = client.get("/api/dashboard/billing", headers=admin_headers).json()
    assert float(report["total_collected"]) == 0.0


# ---------------------------------------------------------------------
# Reconciliation invariant: for every Ledger A consultation-fee event,
# Ledger B's mirror must either not be required (WAIVED -- see below)
# or must correctly reflect that event. This is the same query
# documented, read-only, in docs/architecture/BILLING_LEDGER_
# COEXISTENCE.md -- kept in sync with it by hand since it's short enough
# that a shared-constant indirection would cost more clarity than it
# saves.
# ---------------------------------------------------------------------

RECONCILIATION_MISMATCH_SQL = """
    SELECT a.id AS appointment_id, a.payment_status AS problem_status
    FROM appointments a
    WHERE
      (a.payment_status = 'PAID' AND a.payment_amount > 0 AND NOT EXISTS (
          SELECT 1 FROM payments p
          WHERE p.legacy_appointment_id = a.id AND p.status = 'COMPLETED'
            AND p.method = a.payment_method AND p.amount = a.payment_amount
            AND p.refunded_amount = 0
      ))
      OR
      (a.payment_status = 'FAILED' AND a.payment_amount > 0 AND NOT EXISTS (
          SELECT 1 FROM payments p
          WHERE p.legacy_appointment_id = a.id AND p.status = 'DECLINED'
            AND p.amount = a.payment_amount
      ))
      OR
      (a.payment_status = 'REFUNDED' AND NOT EXISTS (
          SELECT 1 FROM payments p
          WHERE p.legacy_appointment_id = a.id AND p.status = 'COMPLETED'
            AND p.refunded_amount = COALESCE(a.refund_amount, 0)
      ))
      OR
      -- WAIVED is never REQUIRED to have a mirror -- Ledger A's own
      -- WAIVED write always records payment_amount = 0, for both a
      -- real forgiven fee (waive_consultation_fee_service) and an
      -- always-zero visit (settle_free_visit_service), so Ledger A
      -- alone cannot tell which case a given row is. Only check that
      -- a mirror, if one exists, is well-formed.
      (a.payment_status = 'WAIVED' AND EXISTS (
          SELECT 1 FROM payments p
          WHERE p.legacy_appointment_id = a.id AND p.method = 'WAIVED'
            AND (p.amount <= 0 OR p.status != 'COMPLETED')
      ))
"""


def _reconciliation_mismatches(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(RECONCILIATION_MISMATCH_SQL)
        return cur.fetchall()


def test_reconciliation_invariant_holds_across_paid_failed_waived_refunded(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Reconciliation Invariant",
        department_name="Reconciliation Invariant Dept", appointment_type_name="Reconciliation Invariant Type",
    )
    client.put(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{seeded['appointment_type_id']}",
        json={"duration_minutes": 30, "consultation_fee": 300},
        headers=admin_headers,
    )

    def _patient(suffix):
        return client.post(
            "/api/patients",
            json={"name": f"Invariant Patient {suffix}", "whatsapp_number": f"+9197111100{suffix}"},
            headers=admin_headers,
        ).json()

    # PAID
    patient_paid = _patient("10")
    appt_paid = _checked_in_appointment(client, db_connection, admin_headers, seeded, patient_paid["id"], hour=9)
    assert client.post(
        f"/api/appointments/{appt_paid}/payment", json={"method": "CASH", "outcome": "PAID"}, headers=admin_headers
    ).status_code == 200

    # FAILED, then retried successfully -- proves retry-safety: the
    # first (FAILED) mirror attempt and the second (PAID) one must
    # coexist as two payment rows against the SAME mirrored charge, not
    # collide or leave the invariant query flagging the FAILED leg.
    patient_retry = _patient("11")
    appt_retry = _checked_in_appointment(client, db_connection, admin_headers, seeded, patient_retry["id"], hour=10)
    assert client.post(
        f"/api/appointments/{appt_retry}/payment", json={"method": "CASH", "outcome": "FAILED"}, headers=admin_headers
    ).status_code == 200
    assert client.post(
        f"/api/appointments/{appt_retry}/payment", json={"method": "UPI", "outcome": "PAID"}, headers=admin_headers
    ).status_code == 200

    # WAIVED (a real, nonzero fee forgiven) -- waive_consultation_fee_
    # service's own eligibility rule requires a prior visit with the
    # same doctor within 3 days (tests/test_consultation_payments.py);
    # settle_free_visit_service's always-zero shortcut doesn't apply
    # here since this doctor's fee is a real, nonzero 300.
    patient_waived = _patient("12")
    prior_waived = _checked_in_appointment(client, db_connection, admin_headers, seeded, patient_waived["id"], hour=13)
    assert client.post(f"/api/appointments/{prior_waived}/complete", headers=admin_headers).status_code == 200
    _set_visited_at_days_ago(db_connection, prior_waived, 2)

    appt_waived = _checked_in_appointment(client, db_connection, admin_headers, seeded, patient_waived["id"], hour=14)
    _set_visited_at_days_ago(db_connection, appt_waived, 0)
    waive_response = client.post(
        f"/api/appointments/{appt_waived}/waive-payment",
        json={"reason": "Reconciliation invariant test"},
        headers=admin_headers,
    )
    assert waive_response.status_code == 200

    # REFUNDED
    patient_refunded = _patient("13")
    appt_refunded = _checked_in_appointment(
        client, db_connection, admin_headers, seeded, patient_refunded["id"], hour=12
    )
    assert client.post(
        f"/api/appointments/{appt_refunded}/payment", json={"method": "CARD", "outcome": "PAID"}, headers=admin_headers
    ).status_code == 200
    assert client.post(
        f"/api/appointments/{appt_refunded}/refund-payment",
        json={"amount": 300, "reason": "Reconciliation invariant refund"},
        headers=admin_headers,
    ).status_code == 200

    mismatches = _reconciliation_mismatches(db_connection)
    assert mismatches == [], f"Ledger A/B reconciliation mismatches: {mismatches}"


def test_waiving_a_real_fee_mirrors_a_completed_waived_payment_and_zeroes_the_invoice(client, db_connection):
    """Dedicated coverage for mirror_legacy_consultation_payment_
    service's WAIVED-with-nonzero-amount branch specifically -- the
    reconciliation invariant test above only proves this branch doesn't
    leave a *mismatch*, not that the mirror it creates has the right
    shape. This asserts that shape directly: one COMPLETED payment,
    method='WAIVED', for the exact forgiven amount, and that it's
    enough to bring the Ledger B invoice's own balance to 0 (not just
    Ledger A's payment_status word)."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waived Mirror",
        department_name="Waived Mirror Dept", appointment_type_name="Waived Mirror Type",
    )
    client.put(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{seeded['appointment_type_id']}",
        json={"duration_minutes": 30, "consultation_fee": 450},
        headers=admin_headers,
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Waived Mirror Patient", "whatsapp_number": "+919711100020"},
        headers=admin_headers,
    ).json()

    prior_id = _checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=9)
    assert client.post(f"/api/appointments/{prior_id}/complete", headers=admin_headers).status_code == 200
    _set_visited_at_days_ago(db_connection, prior_id, 1)

    appointment_id = _checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=10)
    _set_visited_at_days_ago(db_connection, appointment_id, 0)
    waived = client.post(
        f"/api/appointments/{appointment_id}/waive-payment",
        json={"reason": "Genuine fee forgiven for mirror test"},
        headers=admin_headers,
    )
    assert waived.status_code == 200
    assert waived.json()["payment_status"] == "WAIVED"
    assert float(waived.json()["payment_amount"]) == 0.0  # Ledger A's own word for WAIVED, unchanged by this phase

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT method, status, amount FROM payments WHERE legacy_appointment_id = %s",
            (appointment_id,),
        )
        rows = cur.fetchall()
    assert rows == [("WAIVED", "COMPLETED", 450)]

    invoice = client.get(f"/api/appointments/{appointment_id}/bill", headers=admin_headers).json()
    assert invoice["gross_amount"] == 450
    assert invoice["balance"] == 0
    assert invoice["payment_status"] == "PAID"
