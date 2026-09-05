"""
Tests for the database-level EXCLUDE constraint added in
migrations/0003_prevent_overlapping_bookings.sql -- the second line of
defense underneath the pg_advisory_xact_lock used by both booking paths.

test_exclusion_constraint_rejects_overlap_bypassing_app_lock is "Test D"
from the concurrency remediation: it inserts directly via two separate
raw psycopg connections that never call get_connection() or go through
either app/api/booking.py or app/api/appointments.py at all -- so no
advisory lock is ever taken. This proves the constraint itself, not the
locking discipline layered on top of it, is what makes an overlapping
double-booking physically impossible to persist.

The rest of this file covers the constraint's boundary behaviour
directly (back-to-back allowed, partial overlap rejected, different
doctors allowed, a CANCELLED appointment's old slot not blocking a new
Confirmed one at the same time) -- all pure data-integrity checks, no
concurrency involved.

All patient/doctor/department names and phone numbers here are
synthetic test fixtures, not real data.
"""

import threading

import psycopg
import pytest

from app.config import DATABASE_URL


@pytest.fixture
def seeded_doctor(db_connection):
    """Minimal department/doctor/appointment-type, inserted directly via
    SQL (this file is specifically about database-level behaviour, not
    the API layer)."""
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO departments (name) VALUES (%s) RETURNING id",
            ("Exclusion Test Department",),
        )
        department_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO doctors (name) VALUES (%s) RETURNING id",
            ("Dr. Exclusion Test",),
        )
        doctor_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO appointment_types (name) VALUES (%s) RETURNING id",
            ("Exclusion Test Consultation",),
        )
        appointment_type_id = cur.fetchone()[0]
    db_connection.commit()

    return {
        "department_id": department_id,
        "doctor_id": doctor_id,
        "appointment_type_id": appointment_type_id,
    }


def _insert_appointment(cur, doctor_id, patient_id, appointment_type_id, start_at, end_at, status="CONFIRMED"):
    cur.execute(
        """
        INSERT INTO appointments (doctor_id, patient_id, appointment_type_id, start_at, end_at, status)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (doctor_id, patient_id, appointment_type_id, start_at, end_at, status),
    )
    return cur.fetchone()[0]


def _insert_synthetic_patient(cur, label: str):
    cur.execute(
        "INSERT INTO patients (name, whatsapp_number) VALUES (%s, %s) RETURNING id",
        (f"Synthetic Test Patient {label}", f"+91900000{label}"),
    )
    return cur.fetchone()[0]


def test_exclusion_constraint_rejects_overlap_bypassing_app_lock(seeded_doctor, db_connection):
    """Test D: two raw connections race to INSERT overlapping Confirmed
    appointments directly, with NO advisory lock taken by either --
    neither goes through app/api/booking.py or app/api/appointments.py
    at all. Exactly one must succeed; the constraint alone must reject
    the other.

    The rejection can surface as either psycopg.errors.ExclusionViolation
    (the common case: one transaction's INSERT is checked after the
    other has already committed) or psycopg.errors.DeadlockDetected
    (when both transactions' INSERTs are checked against each other at
    truly the same instant, each waiting on the other's not-yet-resolved
    transaction -- a real, separately-documented Postgres behavior for
    GiST-based exclusion constraints under true concurrency, confirmed
    while writing this test). Both are Postgres correctly preventing the
    double booking; this scenario is exactly why the application code
    (app/api/booking.py, app/api/appointments.py) takes the advisory
    lock *before* its INSERT -- that serializes the two paths so
    neither ever reaches this raw, lock-free race in practice. Only
    catching ExclusionViolation there (not DeadlockDetected) is
    therefore deliberate, not an oversight: see the exception handling
    in both files' INSERT sites."""
    with db_connection.cursor() as cur:
        patient_a = _insert_synthetic_patient(cur, "A1")
        patient_b = _insert_synthetic_patient(cur, "B1")
    db_connection.commit()

    start_at = "2026-10-05T09:00:00+00:00"
    end_at = "2026-10-05T09:30:00+00:00"

    results = {}
    barrier = threading.Barrier(2)

    def insert_direct(patient_id, key):
        conn = psycopg.connect(DATABASE_URL)
        try:
            barrier.wait()
            try:
                with conn.cursor() as cur:
                    _insert_appointment(
                        cur,
                        seeded_doctor["doctor_id"],
                        patient_id,
                        seeded_doctor["appointment_type_id"],
                        start_at,
                        end_at,
                    )
                conn.commit()
                results[key] = "OK"
            except (psycopg.errors.ExclusionViolation, psycopg.errors.DeadlockDetected):
                conn.rollback()
                results[key] = "REJECTED"
        finally:
            conn.close()

    t1 = threading.Thread(target=insert_direct, args=(patient_a, "a"))
    t2 = threading.Thread(target=insert_direct, args=(patient_b, "b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    accepted = [k for k, v in results.items() if v == "OK"]
    rejected = [k for k, v in results.items() if v == "REJECTED"]

    assert len(accepted) == 1, f"expected exactly one direct insert to succeed, got {results}"
    assert len(rejected) == 1, f"expected the constraint to reject the other, got {results}"

    # Connection/transaction health: a fresh query against the pool's
    # own connection still works normally after the rejection.
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM appointments WHERE doctor_id = %s AND status = 'CONFIRMED'",
            (seeded_doctor["doctor_id"],),
        )
        booked_count = cur.fetchone()[0]

    assert booked_count == 1, f"expected exactly one Confirmed appointment, found {booked_count}"


def test_back_to_back_appointments_are_allowed(seeded_doctor, db_connection):
    """09:00-09:30 followed immediately by 09:30-10:00, same doctor:
    must both be allowed (half-open interval semantics -- the
    constraint must not treat a shared boundary instant as overlap)."""
    with db_connection.cursor() as cur:
        patient_a = _insert_synthetic_patient(cur, "A2")
        patient_b = _insert_synthetic_patient(cur, "B2")

        _insert_appointment(
            cur, seeded_doctor["doctor_id"], patient_a, seeded_doctor["appointment_type_id"],
            "2026-10-06T09:00:00+00:00", "2026-10-06T09:30:00+00:00",
        )
        # Must not raise.
        _insert_appointment(
            cur, seeded_doctor["doctor_id"], patient_b, seeded_doctor["appointment_type_id"],
            "2026-10-06T09:30:00+00:00", "2026-10-06T10:00:00+00:00",
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM appointments WHERE doctor_id = %s AND status = 'CONFIRMED'",
            (seeded_doctor["doctor_id"],),
        )
        assert cur.fetchone()[0] == 2


def test_partial_overlap_is_rejected(seeded_doctor, db_connection):
    """09:00-09:30 then 09:15-09:45, same doctor: the second must be
    rejected by the constraint."""
    with db_connection.cursor() as cur:
        patient_a = _insert_synthetic_patient(cur, "A3")
        patient_b = _insert_synthetic_patient(cur, "B3")

        _insert_appointment(
            cur, seeded_doctor["doctor_id"], patient_a, seeded_doctor["appointment_type_id"],
            "2026-10-07T09:00:00+00:00", "2026-10-07T09:30:00+00:00",
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        with pytest.raises(psycopg.errors.ExclusionViolation):
            _insert_appointment(
                cur, seeded_doctor["doctor_id"], patient_b, seeded_doctor["appointment_type_id"],
                "2026-10-07T09:15:00+00:00", "2026-10-07T09:45:00+00:00",
            )
    db_connection.rollback()

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM appointments WHERE doctor_id = %s AND status = 'CONFIRMED'",
            (seeded_doctor["doctor_id"],),
        )
        assert cur.fetchone()[0] == 1


def test_different_doctors_can_have_identical_overlapping_times(db_connection):
    """The exclusion constraint keys on (doctor_id, time range) --
    two different doctors booked at the exact same instant must both
    be allowed."""
    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO doctors (name) VALUES (%s) RETURNING id", ("Dr. Overlap A",))
        doctor_a = cur.fetchone()[0]
        cur.execute("INSERT INTO doctors (name) VALUES (%s) RETURNING id", ("Dr. Overlap B",))
        doctor_b = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO appointment_types (name) VALUES (%s) RETURNING id",
            ("Overlap Test Type",),
        )
        appointment_type_id = cur.fetchone()[0]
        patient_a = _insert_synthetic_patient(cur, "A4")
        patient_b = _insert_synthetic_patient(cur, "B4")

        _insert_appointment(
            cur, doctor_a, patient_a, appointment_type_id,
            "2026-10-08T09:00:00+00:00", "2026-10-08T09:30:00+00:00",
        )
        # Must not raise -- different doctor_id.
        _insert_appointment(
            cur, doctor_b, patient_b, appointment_type_id,
            "2026-10-08T09:00:00+00:00", "2026-10-08T09:30:00+00:00",
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM appointments WHERE doctor_id IN (%s, %s) AND status = 'CONFIRMED'",
            (doctor_a, doctor_b),
        )
        assert cur.fetchone()[0] == 2


def test_cancelled_appointment_does_not_block_new_booking_at_same_time(seeded_doctor, db_connection):
    """A CANCELLED appointment at 09:00-09:30 must not prevent a new
    Confirmed appointment at that identical time -- the constraint's
    WHERE (status NOT IN ('CANCELLED', 'REJECTED')) clause excludes
    cancelled/rejected rows."""
    with db_connection.cursor() as cur:
        patient_a = _insert_synthetic_patient(cur, "A5")
        patient_b = _insert_synthetic_patient(cur, "B5")

        original_id = _insert_appointment(
            cur, seeded_doctor["doctor_id"], patient_a, seeded_doctor["appointment_type_id"],
            "2026-10-09T09:00:00+00:00", "2026-10-09T09:30:00+00:00",
        )
        cur.execute(
            "UPDATE appointments SET status = 'CANCELLED' WHERE id = %s",
            (original_id,),
        )

        # Must not raise, even though the time range is identical.
        _insert_appointment(
            cur, seeded_doctor["doctor_id"], patient_b, seeded_doctor["appointment_type_id"],
            "2026-10-09T09:00:00+00:00", "2026-10-09T09:30:00+00:00",
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT status, patient_id FROM appointments WHERE doctor_id = %s ORDER BY id",
            (seeded_doctor["doctor_id"],),
        )
        rows = cur.fetchall()

    assert rows == [("CANCELLED", patient_a), ("CONFIRMED", patient_b)]


def test_rejected_appointment_does_not_block_new_booking_at_same_time(seeded_doctor, db_connection):
    """Same shape as the CANCELLED case above, but for REJECTED --
    added to the exclusion constraint's release set in migrations/0011_
    appointment_lifecycle_statuses.sql alongside CANCELLED, since a
    rejected request never happened either."""
    with db_connection.cursor() as cur:
        patient_a = _insert_synthetic_patient(cur, "A6")
        patient_b = _insert_synthetic_patient(cur, "B6")

        original_id = _insert_appointment(
            cur, seeded_doctor["doctor_id"], patient_a, seeded_doctor["appointment_type_id"],
            "2026-10-10T09:00:00+00:00", "2026-10-10T09:30:00+00:00",
            status="PENDING",
        )
        cur.execute(
            "UPDATE appointments SET status = 'REJECTED' WHERE id = %s",
            (original_id,),
        )

        # Must not raise, even though the time range is identical.
        _insert_appointment(
            cur, seeded_doctor["doctor_id"], patient_b, seeded_doctor["appointment_type_id"],
            "2026-10-10T09:00:00+00:00", "2026-10-10T09:30:00+00:00",
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT status, patient_id FROM appointments WHERE doctor_id = %s ORDER BY id",
            (seeded_doctor["doctor_id"],),
        )
        rows = cur.fetchall()

    assert rows == [("REJECTED", patient_a), ("CONFIRMED", patient_b)]
