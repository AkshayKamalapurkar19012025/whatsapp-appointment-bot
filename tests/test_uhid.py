"""
M6 (HospitalOS build plan) tests: UHID format and the concurrency
guarantee the plan's own DoD calls out explicitly -- "concurrent
registration produces no duplicates" -- using the same threading.Barrier
pattern as tests/test_concurrency.py.
"""

import threading

from tests.helpers import register_patient


def test_new_patient_gets_a_uhid(client, db_connection):
    register_patient(client, "+919800000001", "UHID Patient One")

    with db_connection.cursor() as cur:
        cur.execute("SELECT uhid FROM patients WHERE whatsapp_number = %s", ("+919800000001",))
        uhid = cur.fetchone()[0]

    assert uhid is not None
    assert uhid.startswith("MAIN-")
    sequence = uhid.split("-")[1]
    assert len(sequence) == 6
    assert sequence.isdigit()


def test_concurrent_registration_produces_no_duplicate_uhids(client, db_connection):
    numbers = [f"+9198000{str(i).zfill(5)}" for i in range(10)]
    barrier = threading.Barrier(len(numbers))

    def do_register(number, key):
        barrier.wait()
        register_patient(client, number, f"Concurrent UHID {key}")

    threads = [
        threading.Thread(target=do_register, args=(number, i))
        for i, number in enumerate(numbers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT uhid FROM patients WHERE whatsapp_number = ANY(%s)",
            (numbers,),
        )
        uhids = [row[0] for row in cur.fetchall()]

    assert len(uhids) == len(numbers), f"expected {len(numbers)} patients, found {len(uhids)}"
    assert all(u is not None for u in uhids), f"expected every patient to have a UHID, got {uhids}"
    assert len(set(uhids)) == len(uhids), f"expected all UHIDs unique, got {uhids}"
