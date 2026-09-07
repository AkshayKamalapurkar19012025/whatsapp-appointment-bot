"""
Tests for app/services/appointment_services.py's reschedule_appointment_service
(WEB P4) -- covers the phase spec's explicit test list (normal reschedule,
invalid reschedule, concurrency during reschedule, timezone) plus the
ownership/ordering guarantees documented in the service's own docstring.

Cross-path concurrency safety for reschedule (WhatsApp vs. web, web vs.
web) is already covered end-to-end by
tests/test_concurrency.py::test_concurrent_reschedule_vs_fresh_booking_same_target_slot,
which continues to pass unchanged after scheduling.py's RESCHEDULE_FINAL_CONFIRM
was refactored to call this same service -- that test is not duplicated
here. What IS added here is a deterministic proof of the specific
recovery mechanism the service's ExclusionViolation catch relies on (see
test_rollback_after_exclusion_violation_restores_cursor_usability below):
reaching that catch via genuine concurrency requires a second transaction
to bypass the advisory lock entirely (the same reason
tests/test_exclusion_constraint.py's Test D uses raw, lock-free
connections), which is a narrow, timing-dependent window not worth
chasing with a flaky thread-race test when the mechanism itself can be
proven directly and reliably instead.
"""

from datetime import date, datetime, timedelta

import psycopg
import pytest

from app.services import exceptions as svc_exc
from app.services.appointment_services import (
    create_appointment_service,
    reschedule_appointment_service,
)
from app.services.availability_engine import scheduling_window

from tests.helpers import seed_basic_doctor

IST = "+05:30"


def _next_weekday_matching(schedule_days, start_from_days_ahead=1):
    candidate = date.today() + timedelta(days=start_from_days_ahead)
    while (candidate.weekday() + 1) not in schedule_days:
        candidate += timedelta(days=1)
    return candidate


def _at(day: date, hour: int) -> datetime:
    return datetime.fromisoformat(f"{day.isoformat()}T{hour:02d}:00:00{IST}")


def _insert_synthetic_patient(cur, label: str):
    cur.execute(
        "INSERT INTO patients (name, whatsapp_number) VALUES (%s, %s) RETURNING id",
        (f"Synthetic Reschedule Patient {label}", f"+91912000{label}"),
    )
    return cur.fetchone()[0]


def _book(cur, seeded, patient_id, hour):
    return create_appointment_service(
        cur,
        doctor_id=seeded["doctor_id"],
        patient_id=patient_id,
        appointment_type_id=seeded["appointment_type_id"],
        start_at=_at(_next_weekday_matching((1, 2, 3, 4, 5)), hour),
    )


# ---------------------------------------------------------------------
# Normal reschedule
# ---------------------------------------------------------------------

def test_reschedule_moves_appointment_to_new_slot_same_doctor_and_type(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule Normal")

    with db_connection.cursor() as cur:
        patient_id = _insert_synthetic_patient(cur, "N1")
        original = _book(cur, seeded, patient_id, 9)
    db_connection.commit()

    new_start = _at(_next_weekday_matching((1, 2, 3, 4, 5), start_from_days_ahead=2), 11)

    with db_connection.cursor() as cur:
        result = reschedule_appointment_service(
            cur,
            original["id"],
            patient_id=patient_id,
            new_start_at=new_start,
        )
    db_connection.commit()

    assert result["status"] == "PENDING"
    assert result["doctor_id"] == seeded["doctor_id"]
    assert result["appointment_type_id"] == seeded["appointment_type_id"]
    assert result["id"] != original["id"]

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (original["id"],))
        assert cur.fetchone()[0] == "CANCELLED"
        cur.execute("SELECT status, doctor_id FROM appointments WHERE id = %s", (result["id"],))
        row = cur.fetchone()
        assert row == ("PENDING", seeded["doctor_id"])


# ---------------------------------------------------------------------
# Invalid reschedule
# ---------------------------------------------------------------------

def test_reschedule_rejects_nonexistent_appointment(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule NotFound")
    with db_connection.cursor() as cur:
        patient_id = _insert_synthetic_patient(cur, "I1")
    db_connection.commit()

    with db_connection.cursor() as cur:
        with pytest.raises(svc_exc.AppointmentNotFound):
            reschedule_appointment_service(
                cur,
                999999999,
                patient_id=patient_id,
                new_start_at=_at(_next_weekday_matching((1, 2, 3, 4, 5)), 9),
            )
    db_connection.rollback()


def test_reschedule_rejects_appointment_belonging_to_another_patient(client, db_connection):
    """Ownership is baked into the same query as existence -- a wrong
    owner raises the identical AppointmentNotFound a truly-missing id
    would, matching scheduling.py's own WHERE id=%s AND patient_id=%s
    pattern (no existence leak)."""
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule WrongOwner")

    with db_connection.cursor() as cur:
        owner_id = _insert_synthetic_patient(cur, "I2")
        other_id = _insert_synthetic_patient(cur, "I3")
        original = _book(cur, seeded, owner_id, 9)
    db_connection.commit()

    with db_connection.cursor() as cur:
        with pytest.raises(svc_exc.AppointmentNotFound):
            reschedule_appointment_service(
                cur,
                original["id"],
                patient_id=other_id,
                new_start_at=_at(_next_weekday_matching((1, 2, 3, 4, 5), 2), 11),
            )
    db_connection.rollback()

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (original["id"],))
        assert cur.fetchone()[0] == "PENDING", "a rejected reschedule attempt must not touch the original"


def test_reschedule_rejects_already_cancelled_appointment(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule AlreadyCancelled")

    with db_connection.cursor() as cur:
        patient_id = _insert_synthetic_patient(cur, "I4")
        original = _book(cur, seeded, patient_id, 9)
        cur.execute("UPDATE appointments SET status = 'CANCELLED' WHERE id = %s", (original["id"],))
    db_connection.commit()

    with db_connection.cursor() as cur:
        with pytest.raises(svc_exc.AlreadyCancelled):
            reschedule_appointment_service(
                cur,
                original["id"],
                patient_id=patient_id,
                new_start_at=_at(_next_weekday_matching((1, 2, 3, 4, 5), 2), 11),
            )
    db_connection.rollback()


def test_reschedule_rejects_time_outside_doctor_schedule(client, db_connection):
    # seed_basic_doctor's default schedule is Monday-Friday only
    # (schedule_days=(1, 2, 3, 4, 5)) -- Saturday has no doctor_schedule
    # row at all. Before this fix, reschedule_appointment_service never
    # checked doctor_schedule (unlike create_appointment_service's own
    # step 4), so this reschedule would have silently succeeded and
    # landed the appointment outside every defined working day.
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule Schedule")

    with db_connection.cursor() as cur:
        patient_id = _insert_synthetic_patient(cur, "I10")
        original = _book(cur, seeded, patient_id, 9)
    db_connection.commit()

    saturday = date.today() + timedelta(days=2)
    while saturday.isoweekday() != 6:
        saturday += timedelta(days=1)

    with db_connection.cursor() as cur:
        with pytest.raises(svc_exc.OutsideDoctorSchedule):
            reschedule_appointment_service(
                cur,
                original["id"],
                patient_id=patient_id,
                new_start_at=_at(saturday, 10),
            )
    db_connection.rollback()

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (original["id"],))
        assert cur.fetchone()[0] == "PENDING", "a rejected reschedule must preserve the original appointment"


def test_reschedule_rejects_doctor_block_conflict(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule Blocked")

    with db_connection.cursor() as cur:
        patient_id = _insert_synthetic_patient(cur, "I5")
        original = _book(cur, seeded, patient_id, 9)

        block_day = _next_weekday_matching((1, 2, 3, 4, 5), start_from_days_ahead=2)
        cur.execute(
            """
            INSERT INTO doctor_blocks (doctor_id, start_at, end_at, reason)
            VALUES (%s, %s, %s, 'Test block')
            """,
            (
                seeded["doctor_id"],
                f"{block_day.isoformat()}T10:00:00{IST}",
                f"{block_day.isoformat()}T12:00:00{IST}",
            ),
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        with pytest.raises(svc_exc.DoctorBlockConflict):
            reschedule_appointment_service(
                cur,
                original["id"],
                patient_id=patient_id,
                new_start_at=_at(block_day, 10),
            )
    db_connection.rollback()

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (original["id"],))
        assert cur.fetchone()[0] == "PENDING"


def test_reschedule_rejects_overlapping_slot(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule Overlap")

    with db_connection.cursor() as cur:
        patient_a = _insert_synthetic_patient(cur, "I6")
        patient_b = _insert_synthetic_patient(cur, "I7")
        original = _book(cur, seeded, patient_a, 9)
        # Someone else already holds the slot we're about to try to move into.
        target_day = _next_weekday_matching((1, 2, 3, 4, 5), start_from_days_ahead=2)
        cur.execute(
            """
            INSERT INTO appointments (doctor_id, patient_id, appointment_type_id, start_at, end_at, status)
            VALUES (%s, %s, %s, %s, %s, 'CONFIRMED')
            """,
            (
                seeded["doctor_id"],
                patient_b,
                seeded["appointment_type_id"],
                f"{target_day.isoformat()}T11:00:00{IST}",
                f"{target_day.isoformat()}T11:30:00{IST}",
            ),
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        with pytest.raises(svc_exc.SlotOverlap):
            reschedule_appointment_service(
                cur,
                original["id"],
                patient_id=patient_a,
                new_start_at=_at(target_day, 11),
            )
    db_connection.rollback()

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (original["id"],))
        assert cur.fetchone()[0] == "PENDING", "a rejected reschedule must preserve the original appointment"


def test_reschedule_enforces_booking_window_when_requested(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule Window")

    with db_connection.cursor() as cur:
        patient_id = _insert_synthetic_patient(cur, "I8")
        original = _book(cur, seeded, patient_id, 9)
    db_connection.commit()

    _, window_end = scheduling_window()
    outside_date = window_end + timedelta(days=1)
    while (outside_date.weekday() + 1) not in (1, 2, 3, 4, 5):
        outside_date += timedelta(days=1)

    with db_connection.cursor() as cur:
        with pytest.raises(svc_exc.OutsideSchedulingWindow):
            reschedule_appointment_service(
                cur,
                original["id"],
                patient_id=patient_id,
                new_start_at=_at(outside_date, 9),
                enforce_scheduling_window=True,
            )
    db_connection.rollback()


# ---------------------------------------------------------------------
# Timezone correctness
# ---------------------------------------------------------------------

def test_reschedule_result_start_at_is_the_same_instant_though_utc_labeled(client, db_connection):
    """
    Checked directly, not assumed: result["start_at"] comes back
    UTC-labeled (e.g. "18:00:00+00:00" for a 14:00 EDT request), NOT in
    the doctor's own timezone -- this is not a bug, it's the same
    RETURNING-clause characteristic already established for
    create_appointment_service's return value (see that function's
    module docstring and the WEB P3 report's "UTC-normalized on
    read-back" finding): Postgres/psycopg deserialize a TIMESTAMPTZ
    according to the session's own timezone (UTC here) on ANY read,
    including a RETURNING clause of the very INSERT that just wrote it
    with a different offset -- not only on a later, separate SELECT.

    What actually matters -- and what this test verifies -- is that the
    ABSOLUTE INSTANT is unchanged: a request for 14:00 EDT must come
    back representing that exact moment, whatever offset label is
    attached to it. A caller that needs to *display* this value
    correctly in the doctor's local time must do what the WEB P3
    frontend fix and this phase's own reschedule UI do: use the
    already-known-correct local value from wherever the target slot was
    selected (e.g. the availability listing), not this response field --
    the same lesson, applied here before it could ship as a second
    instance of the same bug.
    """
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Reschedule TZ",
        timezone="America/New_York",
    )

    with db_connection.cursor() as cur:
        patient_id = _insert_synthetic_patient(cur, "I9")
        original = _book(cur, seeded, patient_id, 9)
    db_connection.commit()

    new_day = _next_weekday_matching((1, 2, 3, 4, 5), start_from_days_ahead=2)
    new_start = datetime.fromisoformat(f"{new_day.isoformat()}T14:00:00-04:00")  # EDT

    with db_connection.cursor() as cur:
        result = reschedule_appointment_service(
            cur,
            original["id"],
            patient_id=patient_id,
            new_start_at=new_start,
        )
    db_connection.commit()

    returned = datetime.fromisoformat(result["start_at"])
    assert returned == new_start, "the absolute instant must be unchanged regardless of offset label"


# ---------------------------------------------------------------------
# Recovery mechanism after the exclusion-constraint backstop
# ---------------------------------------------------------------------

def test_rollback_after_exclusion_violation_restores_cursor_usability(db_connection):
    """
    Deterministic proof of the exact recovery mechanism
    reschedule_appointment_service's ExclusionViolation catch relies on.

    This is the bug found and fixed while building this service: the
    original inline WhatsApp code called conn.rollback() immediately
    after catching ExclusionViolation, because Postgres aborts a
    transaction on any caught error until an explicit ROLLBACK -- and
    scheduling.py's RESCHEDULE_FINAL_CONFIRM handler needs to keep using
    the same cursor afterward (update_session, get_available_dates) to
    return its friendly fallback message. The first extraction of this
    logic into reschedule_appointment_service dropped that rollback;
    caught here before it shipped. Without it, this test's final
    `cur.execute("SELECT 1")` would raise
    psycopg.errors.InFailedSqlTransaction.

    Reaching this exact catch via genuine end-to-end concurrency needs a
    second transaction to slip in between the service's own post-lock
    recheck and its INSERT while bypassing the advisory lock entirely --
    the same narrow, timing-dependent window
    tests/test_exclusion_constraint.py's Test D uses raw, lock-free
    connections to force. Proving the recovery mechanism directly here
    is reliable; chasing the same proof through real thread timing would
    be flaky for no extra confidence.
    """
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO doctors (name) VALUES (%s) RETURNING id",
            ("Dr. Rollback Recovery",),
        )
        doctor_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO appointment_types (name) VALUES (%s) RETURNING id",
            ("Rollback Recovery Type",),
        )
        appointment_type_id = cur.fetchone()[0]
        patient_a = _insert_synthetic_patient(cur, "RB1")
        patient_b = _insert_synthetic_patient(cur, "RB2")

        cur.execute(
            """
            INSERT INTO appointments (doctor_id, patient_id, appointment_type_id, start_at, end_at, status)
            VALUES (%s, %s, %s, '2026-10-20T09:00:00+00:00', '2026-10-20T09:30:00+00:00', 'CONFIRMED')
            """,
            (doctor_id, patient_a, appointment_type_id),
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        # Deliberately trigger the exact same error
        # reschedule_appointment_service's try/except catches, on the
        # same cursor/connection, to reproduce the aborted-transaction
        # state precisely.
        with pytest.raises(psycopg.errors.ExclusionViolation):
            cur.execute(
                """
                INSERT INTO appointments (doctor_id, patient_id, appointment_type_id, start_at, end_at, status)
                VALUES (%s, %s, %s, '2026-10-20T09:00:00+00:00', '2026-10-20T09:30:00+00:00', 'CONFIRMED')
                """,
                (doctor_id, patient_b, appointment_type_id),
            )

        # This is the exact call reschedule_appointment_service makes.
        cur.connection.rollback()

        # Proves recovery: an unrelated query on the SAME cursor must
        # succeed, not raise InFailedSqlTransaction.
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)
