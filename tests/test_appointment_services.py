"""
Tests for the two additive, opt-in parameters added to
app/services/appointment_services.py in the WEB P1 phase:
enforce_scheduling_window (create) and requesting_patient_id (cancel).

Neither is exercised by the existing REST endpoints yet -- both default
off/None there, preserving app/api/appointments.py's prior behavior
exactly (see tests/test_concurrency.py and
tests/test_exclusion_constraint.py, unchanged and still passing). So
these tests call the service functions directly with a real
db_connection cursor, the same pattern tests/test_exclusion_constraint.py
already uses for database-level behavior with no HTTP endpoint yet.

requesting_patient_id=None is NOT a security fix for
DELETE /api/appointments/{id} -- see app/services/appointment_services.py's
module docstring and this phase's report for why genuine ownership
enforcement needs patient identity (WEB P2), which doesn't exist yet.
test_cancel_appointment_service_default_stays_unrestricted exists to
pin that today's behavior is unchanged, not to claim the gap is closed.
"""

from datetime import date, datetime, timedelta

import pytest

from app.services import exceptions as svc_exc
from app.services.appointment_services import (
    create_appointment_service,
    cancel_appointment_service,
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
        (f"Synthetic Service Test Patient {label}", f"+91911000{label}"),
    )
    return cur.fetchone()[0]


# ---------------------------------------------------------------------
# create_appointment_service(enforce_scheduling_window=...)
# ---------------------------------------------------------------------

def test_create_appointment_service_rejects_date_outside_booking_window(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Window Reject")

    _, window_end = scheduling_window()
    outside_date = window_end + timedelta(days=1)
    start_at = _at(outside_date, 9)

    with db_connection.cursor() as cur:
        patient_id = _insert_synthetic_patient(cur, "W1")
    db_connection.commit()

    with db_connection.cursor() as cur:
        with pytest.raises(svc_exc.OutsideSchedulingWindow):
            create_appointment_service(
                cur,
                doctor_id=seeded["doctor_id"],
                patient_id=patient_id,
                appointment_type_id=seeded["appointment_type_id"],
                start_at=start_at,
                enforce_scheduling_window=True,
            )
    db_connection.rollback()

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM appointments WHERE doctor_id = %s",
            (seeded["doctor_id"],),
        )
        assert cur.fetchone()[0] == 0


def test_create_appointment_service_allows_date_inside_booking_window(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Window Allow")

    scheduling_date = _next_weekday_matching((1, 2, 3, 4, 5))
    start_at = _at(scheduling_date, 9)

    with db_connection.cursor() as cur:
        patient_id = _insert_synthetic_patient(cur, "W2")
    db_connection.commit()

    with db_connection.cursor() as cur:
        result = create_appointment_service(
            cur,
            doctor_id=seeded["doctor_id"],
            patient_id=patient_id,
            appointment_type_id=seeded["appointment_type_id"],
            start_at=start_at,
            enforce_scheduling_window=True,
        )
    db_connection.commit()

    assert result["status"] == "PENDING"


def test_create_appointment_service_default_does_not_enforce_booking_window(client, db_connection):
    """enforce_scheduling_window defaults to False -- must preserve
    app/api/appointments.py's existing REST behavior of allowing any
    future date, unrestricted by the web calendar window."""
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Window Default")

    _, window_end = scheduling_window()
    outside_date = window_end + timedelta(days=1)
    while (outside_date.weekday() + 1) not in (1, 2, 3, 4, 5):
        outside_date += timedelta(days=1)
    start_at = _at(outside_date, 9)

    with db_connection.cursor() as cur:
        patient_id = _insert_synthetic_patient(cur, "W3")
    db_connection.commit()

    with db_connection.cursor() as cur:
        result = create_appointment_service(
            cur,
            doctor_id=seeded["doctor_id"],
            patient_id=patient_id,
            appointment_type_id=seeded["appointment_type_id"],
            start_at=start_at,
            # enforce_scheduling_window intentionally omitted (default False).
        )
    db_connection.commit()

    assert result["status"] == "PENDING"


# ---------------------------------------------------------------------
# cancel_appointment_service(requesting_patient_id=...)
# ---------------------------------------------------------------------

def test_cancel_appointment_service_rejects_non_owner(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Ownership Reject")
    start_at = _at(_next_weekday_matching((1, 2, 3, 4, 5)), 10)

    with db_connection.cursor() as cur:
        owner_id = _insert_synthetic_patient(cur, "O1")
        other_id = _insert_synthetic_patient(cur, "O2")
    db_connection.commit()

    with db_connection.cursor() as cur:
        created = create_appointment_service(
            cur,
            doctor_id=seeded["doctor_id"],
            patient_id=owner_id,
            appointment_type_id=seeded["appointment_type_id"],
            start_at=start_at,
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        with pytest.raises(svc_exc.NotAppointmentOwner):
            cancel_appointment_service(
                cur,
                created["id"],
                requesting_patient_id=other_id,
            )
    db_connection.rollback()

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (created["id"],))
        assert cur.fetchone()[0] == "PENDING"


def test_cancel_appointment_service_allows_owner(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Ownership Allow")
    start_at = _at(_next_weekday_matching((1, 2, 3, 4, 5)), 11)

    with db_connection.cursor() as cur:
        owner_id = _insert_synthetic_patient(cur, "O3")
    db_connection.commit()

    with db_connection.cursor() as cur:
        created = create_appointment_service(
            cur,
            doctor_id=seeded["doctor_id"],
            patient_id=owner_id,
            appointment_type_id=seeded["appointment_type_id"],
            start_at=start_at,
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        result = cancel_appointment_service(
            cur,
            created["id"],
            requesting_patient_id=owner_id,
        )
    db_connection.commit()

    assert result["status"] == "CANCELLED"


def test_cancel_appointment_service_default_stays_unrestricted(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Ownership Default")
    start_at = _at(_next_weekday_matching((1, 2, 3, 4, 5)), 12)

    with db_connection.cursor() as cur:
        owner_id = _insert_synthetic_patient(cur, "O4")
    db_connection.commit()

    with db_connection.cursor() as cur:
        created = create_appointment_service(
            cur,
            doctor_id=seeded["doctor_id"],
            patient_id=owner_id,
            appointment_type_id=seeded["appointment_type_id"],
            start_at=start_at,
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        # No requesting_patient_id passed -- must succeed exactly as the
        # current, unauthenticated REST endpoint does today.
        result = cancel_appointment_service(cur, created["id"])
    db_connection.commit()

    assert result["status"] == "CANCELLED"
