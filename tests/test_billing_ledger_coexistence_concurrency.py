"""
Phase 10B (Billing Ledger Coexistence, ADR-009 Option B) concurrency and
transaction-safety tests for the Ledger A -> Ledger B mirror
(app/services/billing_services.py's mirror_legacy_consultation_payment_
service, called only from app/services/appointment_services.py's
_write_consultation_payment_status chokepoint).

Four scenarios:
  * Two threads racing the SAME PAID event on the SAME appointment,
    through the real HTTP endpoint -- proves record_payment_service's
    existing _lock_appointment_for_payment (FOR UPDATE) + idempotent-
    return-on-already-PAID guard (unchanged by this phase) means the
    mirror only ever runs once, not that it merely doesn't crash twice.
  * Two threads racing "ensure this encounter has an invoice" from two
    DIFFERENT entry points (the mirror's own _ensure_invoice vs. an
    ad-hoc /bill/charges call) -- exercises billing_services.py's
    existing ON CONFLICT (encounter_id) DO NOTHING invoice upsert as a
    NEW caller (the mirror) racing an old one, not a new mechanism.
  * Two threads calling mirror_legacy_consultation_payment_service
    directly (bypassing the appointment-row lock on purpose, with two
    separate raw connections) for the SAME appointment_id -- the one
    scenario the API's own locking never lets happen in practice, but
    the mirror's own charges_one_consultation_per_legacy_appointment
    unique index + `except psycopg.errors.UniqueViolation` fallback
    exist specifically to survive it if it ever did. Without this test,
    that fallback branch has no coverage at all (every real call site
    is already serialized before it can be reached).
  * A forced mirror failure (an invoice voided out from under a pending
    consultation payment) -- proves the same-transaction guarantee:
    Ledger A's own UPDATE, even though it runs first and would
    otherwise have succeeded on its own, is rolled back too when the
    mirror it shares a cursor/transaction with fails.
"""

import threading

import psycopg

from app.config import DATABASE_URL
from app.services.billing_services import mirror_legacy_consultation_payment_service
from tests.helpers import create_admin_and_get_headers, seed_basic_doctor
from tests.test_billing_report import _create_checked_in_appointment, _create_patient, _set_fee


def _checked_in_appointment_with_fee(client, db_connection, doctor_name, fee, hour=9):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name=doctor_name,
        department_name=f"{doctor_name} Dept", appointment_type_name=f"{doctor_name} Type",
    )
    _set_fee(client, admin_headers, seeded, fee)
    patient = _create_patient(client, admin_headers, f"{doctor_name} Patient", f"+9197{abs(hash(doctor_name)) % 10**7:07d}")
    appointment_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=hour)
    return admin_headers, appointment_id


def test_concurrent_identical_paid_calls_mirror_exactly_once(client, db_connection):
    admin_headers, appointment_id = _checked_in_appointment_with_fee(
        client, db_connection, "Dr. Coexist Concurrency A", fee=500
    )

    results = {}
    barrier = threading.Barrier(2)

    def do_pay(key):
        barrier.wait()
        response = client.post(
            f"/api/appointments/{appointment_id}/payment",
            json={"method": "CASH", "outcome": "PAID"},
            headers=admin_headers,
        )
        results[key] = response.status_code

    threads = [threading.Thread(target=do_pay, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Both calls are idempotent-PAID, not a winner/loser race -- both
    # must return 200 (record_payment_service's own "already PAID ->
    # return early" guard, unchanged by this phase).
    assert results == {"a": 200, "b": 200}, results

    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM charges
            WHERE legacy_appointment_id = %s AND source_type = 'CONSULTATION' AND status = 'ACTIVE'
            """,
            (appointment_id,),
        )
        (charge_count,) = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM payments WHERE legacy_appointment_id = %s", (appointment_id,))
        (payment_count,) = cur.fetchone()

    assert charge_count == 1, f"expected exactly one mirrored charge, found {charge_count}"
    assert payment_count == 1, f"expected exactly one mirrored payment, found {payment_count}"


def test_concurrent_invoice_creation_from_two_entry_points_yields_one_invoice(client, db_connection):
    """The mirror's own _ensure_invoice races an unrelated /bill/charges
    call for the SAME encounter -- two different code paths converging
    on the same "does this encounter have an invoice yet" question at
    the same instant."""
    admin_headers, appointment_id = _checked_in_appointment_with_fee(
        client, db_connection, "Dr. Coexist Concurrency B", fee=500
    )

    results = {}
    barrier = threading.Barrier(2)

    def do_pay():
        barrier.wait()
        response = client.post(
            f"/api/appointments/{appointment_id}/payment",
            json={"method": "CASH", "outcome": "PAID"},
            headers=admin_headers,
        )
        results["payment"] = response.status_code

    def do_charge():
        barrier.wait()
        response = client.post(
            f"/api/appointments/{appointment_id}/bill/charges",
            json={"description": "Lab panel", "amount": 250, "source_type": "LAB"},
            headers=admin_headers,
        )
        results["charge"] = response.status_code

    threads = [threading.Thread(target=do_pay), threading.Thread(target=do_charge)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results["payment"] == 200, results
    assert results["charge"] == 200, results

    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM invoices i
            JOIN encounters e ON e.id = i.encounter_id
            JOIN appointments a ON a.encounter_id = e.id
            WHERE a.id = %s
            """,
            (appointment_id,),
        )
        (invoice_count,) = cur.fetchone()
    assert invoice_count == 1, f"expected exactly one invoice for this encounter, found {invoice_count}"


def test_concurrent_direct_mirror_calls_survive_the_unique_index_race(client, db_connection):
    """Bypasses the appointment-row lock entirely (two raw connections
    calling the mirror service function directly) -- the one race the
    real API never lets happen (record_payment_service always locks the
    appointment first), but the ONLY way to actually exercise the
    mirror's own `except psycopg.errors.UniqueViolation` fallback, which
    otherwise has zero coverage under the API's normal serialization."""
    admin_headers, appointment_id = _checked_in_appointment_with_fee(
        client, db_connection, "Dr. Coexist Concurrency C", fee=500
    )
    # Force the invoice to exist up front so both racing connections
    # reach the charge-insert step, not the (already separately-tested)
    # invoice-creation race.
    client.get(f"/api/appointments/{appointment_id}/bill", headers=admin_headers)

    # _clean_tables truncates `staff` with RESTART IDENTITY before every
    # test, and _checked_in_appointment_with_fee's create_admin_and_get_
    # headers call is the first staff row this test creates -- so id 1
    # is this admin, reliably, without needing to reverse the session
    # token's hash to look it up.
    staff_id = 1

    barrier = threading.Barrier(2)
    outcomes = {}

    def race(key):
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                barrier.wait()
                try:
                    mirror_legacy_consultation_payment_service(
                        cur, appointment_id, event="PAID", staff_id=staff_id, amount=500, method="CASH",
                    )
                    outcomes[key] = "ok"
                except Exception as exc:  # noqa: BLE001 -- proving NEITHER thread raises
                    outcomes[key] = f"error: {exc!r}"
                    raise

    threads = [threading.Thread(target=race, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes == {"a": "ok", "b": "ok"}, outcomes

    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM charges
            WHERE legacy_appointment_id = %s AND source_type = 'CONSULTATION' AND status = 'ACTIVE'
            """,
            (appointment_id,),
        )
        (charge_count,) = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM payments WHERE legacy_appointment_id = %s", (appointment_id,))
        (payment_count,) = cur.fetchone()

    assert charge_count == 1, f"the DB-enforced unique index must still leave exactly one charge, found {charge_count}"
    # Each direct call inserts its own payment row -- the mirror itself
    # has no "already paid" concept (that guard lives one layer up, in
    # record_payment_service, deliberately bypassed by this test), so
    # two independent PAID events correctly produce two payment rows
    # against the one shared charge.
    assert payment_count == 2, f"expected two independent payment rows, found {payment_count}"


def test_invoice_voided_mid_payment_rolls_back_ledger_a_too(client, db_connection):
    """Same-transaction guarantee: void this visit's invoice (a
    Ledger-B-only action) after check-in but before the consultation fee
    is ever paid, then attempt to pay it. The mirror's _ensure_invoice
    raises InvoiceVoided partway through -- Ledger A's own UPDATE
    (executed just before, same cursor, same transaction) must be rolled
    back too, not left as a silently-orphaned PAID row with no Ledger B
    counterpart."""
    admin_headers, appointment_id = _checked_in_appointment_with_fee(
        client, db_connection, "Dr. Coexist Concurrency D", fee=500
    )
    client.get(f"/api/appointments/{appointment_id}/bill", headers=admin_headers)
    void_response = client.post(
        f"/api/appointments/{appointment_id}/bill/void",
        json={"reason": "Concurrency/rollback test"},
        headers=admin_headers,
    )
    assert void_response.status_code == 200

    response = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert response.status_code == 409

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT payment_status, payment_amount, payment_method FROM appointments WHERE id = %s",
            (appointment_id,),
        )
        payment_status, payment_amount, payment_method = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM payments WHERE legacy_appointment_id = %s", (appointment_id,))
        (payment_count,) = cur.fetchone()

    assert payment_status == "UNPAID", "Ledger A's own UPDATE must have been rolled back, not left as PAID"
    assert payment_amount is None
    assert payment_method is None
    assert payment_count == 0, "no orphaned Ledger B payment row should exist for a rolled-back attempt"
