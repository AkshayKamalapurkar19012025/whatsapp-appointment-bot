"""
Phase 10B (Billing Ledger Coexistence, ADR-009 Option B) tests for
migrations/0059_billing_ledger_coexistence_backfill.sql -- run directly
against test data shaped like it predates migrations/0058 (Ledger A
populated, no Ledger B mirror yet), since the test database's own
_clean_tables fixture means there is no real "historical data" for the
migration to encounter when it actually applies during test-session
setup.

Simulates that pre-coexistence shape by creating real PAID/FAILED/
REFUNDED/WAIVED/UNPAID appointments through the real API (so Ledger A's
columns are populated exactly as production data would be), then
deleting the Ledger B mirror rows the API's own chokepoint just created
-- leaving exactly what a genuinely pre-migrations/0058 row would look
like: Ledger A fully populated, nothing in charges/payments referencing
it.
"""

from pathlib import Path

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor
from tests.test_billing_report import _create_checked_in_appointment, _create_patient, _set_fee

BACKFILL_SQL = Path(__file__).resolve().parent.parent / "migrations" / "0059_billing_ledger_coexistence_backfill.sql"


def _run_backfill(db_connection):
    sql = BACKFILL_SQL.read_text()
    with db_connection.cursor() as cur:
        cur.execute(sql)
    db_connection.commit()


def _strip_ledger_b_mirror(db_connection, appointment_id):
    """Simulates 'this row predates migrations/0058' by deleting
    whatever the live chokepoint/mirror just created for it, without
    touching Ledger A at all."""
    with db_connection.cursor() as cur:
        cur.execute("DELETE FROM payments WHERE legacy_appointment_id = %s", (appointment_id,))
        cur.execute("DELETE FROM charges WHERE legacy_appointment_id = %s", (appointment_id,))
    db_connection.commit()


def _mirror_counts(db_connection, appointment_id):
    with db_connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM charges WHERE legacy_appointment_id = %s", (appointment_id,))
        (charges,) = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM payments WHERE legacy_appointment_id = %s", (appointment_id,))
        (payments,) = cur.fetchone()
    return charges, payments


def test_backfill_mirrors_a_historical_paid_appointment(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Backfill Paid",
        department_name="Backfill Paid Dept", appointment_type_name="Backfill Paid Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Backfill Paid Patient", "+919712200001")
    appointment_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    _strip_ledger_b_mirror(db_connection, appointment_id)
    assert _mirror_counts(db_connection, appointment_id) == (0, 0)

    _run_backfill(db_connection)

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT method, status, amount FROM payments WHERE legacy_appointment_id = %s",
            (appointment_id,),
        )
        rows = cur.fetchall()
    assert rows == [("CASH", "COMPLETED", 500)]

    # Second run: genuinely 0 new rows, not just "no error".
    charges_before, payments_before = _mirror_counts(db_connection, appointment_id)
    _run_backfill(db_connection)
    assert _mirror_counts(db_connection, appointment_id) == (charges_before, payments_before)


def test_backfill_mirrors_a_historical_failed_appointment(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Backfill Failed",
        department_name="Backfill Failed Dept", appointment_type_name="Backfill Failed Type",
    )
    _set_fee(client, admin_headers, seeded, 300)
    patient = _create_patient(client, admin_headers, "Backfill Failed Patient", "+919712200002")
    appointment_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "UPI", "outcome": "FAILED"},
        headers=admin_headers,
    )
    _strip_ledger_b_mirror(db_connection, appointment_id)

    _run_backfill(db_connection)

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT method, status, amount FROM payments WHERE legacy_appointment_id = %s",
            (appointment_id,),
        )
        rows = cur.fetchall()
    assert rows == [("UPI", "DECLINED", 300)]


def test_backfill_mirrors_a_historical_refunded_appointment(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Backfill Refunded",
        department_name="Backfill Refunded Dept", appointment_type_name="Backfill Refunded Type",
    )
    _set_fee(client, admin_headers, seeded, 700)
    patient = _create_patient(client, admin_headers, "Backfill Refunded Patient", "+919712200003")
    appointment_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CARD", "outcome": "PAID"},
        headers=admin_headers,
    )
    client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 700, "reason": "Backfill test refund"},
        headers=admin_headers,
    )
    _strip_ledger_b_mirror(db_connection, appointment_id)

    _run_backfill(db_connection)

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT method, status, amount, refunded_amount FROM payments WHERE legacy_appointment_id = %s",
            (appointment_id,),
        )
        rows = cur.fetchall()
    assert rows == [("CARD", "COMPLETED", 700, 700)]


def test_backfill_never_touches_waived_or_unpaid_appointments(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Backfill Waived",
        department_name="Backfill Waived Dept", appointment_type_name="Backfill Waived Type",
    )
    _set_fee(client, admin_headers, seeded, 0)
    patient_waived = _create_patient(client, admin_headers, "Backfill Waived Patient", "+919712200004")
    waived_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient_waived["id"], hour=9)
    client.post(f"/api/appointments/{waived_id}/settle-free-visit", headers=admin_headers)

    patient_unpaid = _create_patient(client, admin_headers, "Backfill Unpaid Patient", "+919712200005")
    unpaid_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient_unpaid["id"], hour=10)
    # Deliberately never paid.

    _run_backfill(db_connection)

    assert _mirror_counts(db_connection, waived_id) == (0, 0)
    assert _mirror_counts(db_connection, unpaid_id) == (0, 0)


def test_backfill_skips_a_paid_appointment_with_no_encounter(client, db_connection):
    """A row this migration explicitly refuses to guess at -- see its
    own header comment. encounter_id is nullable (migrations/0028), so
    this is a real, if rare, historical shape."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Backfill NoEncounter",
        department_name="Backfill NoEncounter Dept", appointment_type_name="Backfill NoEncounter Type",
    )
    _set_fee(client, admin_headers, seeded, 400)
    patient = _create_patient(client, admin_headers, "Backfill NoEncounter Patient", "+919712200006")
    appointment_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    _strip_ledger_b_mirror(db_connection, appointment_id)
    with db_connection.cursor() as cur:
        cur.execute("UPDATE appointments SET encounter_id = NULL WHERE id = %s", (appointment_id,))
    db_connection.commit()

    _run_backfill(db_connection)

    assert _mirror_counts(db_connection, appointment_id) == (0, 0)


def test_backfill_reconciliation_is_clean_across_mixed_historical_states(client, db_connection):
    """The same reconciliation invariant tests/test_billing_ledger_
    reconciliation_gap.py checks for live events, run here against
    backfilled ones instead -- 0 mismatches after the backfill covers
    PAID/FAILED/REFUNDED, and WAIVED's documented absence is still not
    flagged."""
    from tests.test_billing_ledger_reconciliation_gap import RECONCILIATION_MISMATCH_SQL

    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Backfill Reconcile",
        department_name="Backfill Reconcile Dept", appointment_type_name="Backfill Reconcile Type",
    )
    _set_fee(client, admin_headers, seeded, 350)

    ids = []
    for i, outcome in enumerate(["PAID", "FAILED"]):
        patient = _create_patient(client, admin_headers, f"Backfill Reconcile Patient {i}", f"+91971220001{i}")
        appt_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=9 + i)
        client.post(
            f"/api/appointments/{appt_id}/payment", json={"method": "CASH", "outcome": outcome}, headers=admin_headers
        )
        _strip_ledger_b_mirror(db_connection, appt_id)
        ids.append(appt_id)

    _run_backfill(db_connection)

    with db_connection.cursor() as cur:
        cur.execute(RECONCILIATION_MISMATCH_SQL)
        mismatches = cur.fetchall()
    assert mismatches == [], f"reconciliation mismatches after backfill: {mismatches}"
