"""
Phase 12 hardening (master spec audit gap #7's concurrency/idempotency
sub-item): tests/test_concurrency.py only covers double-booking races
(the WhatsApp/REST scheduling paths). The other two money-and-
inventory-critical paths -- recording a payment against an invoice,
and dispensing against a pharmacy stock batch -- both already take a
`SELECT ... FOR UPDATE` row lock before their balance/quantity check
(app/services/billing_services.py's record_invoice_payment_service,
app/services/pharmacy_services.py's record_dispense_service), but that
had never actually been exercised under real concurrency, only
sequential test assertions. These two tests do that: two threads race
for the same, sized-so-only-one-can-win resource, and exactly one must
win.
"""

import threading
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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9196{abs(hash(doctor_name)) % 10**8:08d}"},
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
    response = client.post(f"/api/appointments/{created['id']}/confirm-and-checkin", headers=admin_headers)
    assert response.status_code == 200
    return {"admin_headers": admin_headers, "appointment_id": created["id"]}


def test_concurrent_payments_for_the_full_balance_only_one_wins(client, db_connection):
    """A ₹1000 balance, two threads each try to pay the full ₹1000 at
    the same instant. record_invoice_payment_service locks the invoice
    row (FOR UPDATE) before re-reading the balance, so the second
    thread's transaction must block until the first commits, then see
    the now-zero balance and be correctly refused -- not race it and
    let both payments through."""
    ctx = _checked_in_context(client, db_connection, "Dr. Concurrency Payment")
    appointment_id = ctx["appointment_id"]
    headers = ctx["admin_headers"]

    client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Consultation", "amount": 1000},
        headers=headers,
    )

    results = {}
    barrier = threading.Barrier(2)

    def do_pay(key):
        barrier.wait()
        response = client.post(
            f"/api/appointments/{appointment_id}/bill/payments",
            json={"amount": 1000, "method": "CASH"},
            headers=headers,
        )
        results[key] = response.status_code

    threads = [threading.Thread(target=do_pay, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    succeeded = [k for k, code in results.items() if code == 200]
    failed = [k for k, code in results.items() if code != 200]
    assert len(succeeded) == 1, f"expected exactly one 200, got {results}"
    assert len(failed) == 1
    assert results[failed[0]] == 422

    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM payments pay
            JOIN invoices i ON i.id = pay.invoice_id
            JOIN encounters e ON e.id = i.encounter_id
            JOIN appointments a ON a.encounter_id = e.id
            WHERE a.id = %s
            """,
            (appointment_id,),
        )
        (payment_count,) = cur.fetchone()
    assert payment_count == 1, f"expected exactly one payment row, found {payment_count}"


def test_concurrent_dispense_against_the_same_stock_batch_only_one_wins(client, db_connection):
    """A stock batch with exactly enough for one full dispense, two
    different prescription items (different patients, same medicine)
    each try to dispense that full quantity from the same batch at the
    same instant. record_dispense_service locks the pharmacy_stock row
    (FOR UPDATE) before checking quantity_on_hand, so the loser must
    see the now-empty batch and be correctly refused."""
    ctx_a = _checked_in_context(client, db_connection, "Dr. Concurrency Dispense A")
    ctx_b = _checked_in_context(client, db_connection, "Dr. Concurrency Dispense B")

    stock = client.post(
        "/api/pharmacy/stock",
        json={
            "medicine_name": "Concurrency Test Medicine",
            "batch_number": "CONC-BATCH-1",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "quantity_on_hand": 10,
            "unit_price": 2.5,
        },
        headers=ctx_a["admin_headers"],
    ).json()

    item_ids = {}
    for key, ctx in (("a", ctx_a), ("b", ctx_b)):
        prescription = client.post(
            f"/api/appointments/{ctx['appointment_id']}/prescription/items",
            json={"medicine_name": "Concurrency Test Medicine", "quantity": 10, "dosage": "1 tab", "frequency": "OD"},
            headers=ctx["admin_headers"],
        ).json()["prescription"]
        item_ids[key] = prescription["items"][0]["id"]
        client.post(f"/api/appointments/{ctx['appointment_id']}/prescription/prescribe", headers=ctx["admin_headers"])

    results = {}
    barrier = threading.Barrier(2)

    def do_dispense(key):
        barrier.wait()
        response = client.post(
            f"/api/pharmacy/items/{item_ids[key]}/dispense",
            json={"quantity": 10, "pharmacy_stock_id": stock["id"]},
            headers=ctx_a["admin_headers"],
        )
        results[key] = response.status_code

    threads = [threading.Thread(target=do_dispense, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    succeeded = [k for k, code in results.items() if code == 200]
    failed = [k for k, code in results.items() if code != 200]
    assert len(succeeded) == 1, f"expected exactly one 200, got {results}"
    assert len(failed) == 1
    assert results[failed[0]] == 409

    with db_connection.cursor() as cur:
        cur.execute("SELECT quantity_on_hand FROM pharmacy_stock WHERE id = %s", (stock["id"],))
        (remaining,) = cur.fetchone()
    assert remaining == 0, f"expected the batch fully drawn down once, found {remaining} remaining"
