"""
Tests for generate_queue_token_service (app/services/appointment_
services.py), extracted out of mark_visited_service as Phase 1 of the
patient arrival -> registration -> payment -> queue workflow. Phase 1
kept mark_visited_service calling it internally right after check-in
as a transitional, no-behavior-change step; Phase 4 cut that wire for
real (record_payment_service's PAID outcome and
waive_consultation_fee_service are this function's only callers now --
see tests/test_queue_tokens.py and tests/test_consultation_payments.py
for that end-to-end behavior). These tests target the extracted
function directly: that it's genuinely idempotent, and that it works
independently of the check-in transition itself rather than being
inlined logic with a new name.

Also covers migrations/0018_appointment_payment_status.sql: the new
column's default, its CHECK constraint, and the WAIVED backfill for
historical CHECKED_IN/COMPLETED rows.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone

import psycopg
import pytest

from app.services.appointment_services import generate_queue_token_service
from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient_name, phone_suffix, hour=9):
    """Same shape as test_queue_tokens.py's own _schedule_and_confirm --
    duplicated locally rather than imported, matching this test suite's
    existing convention of not importing across test files (only
    tests/helpers.py is shared)."""
    patient = client.post(
        "/api/patients",
        json={"name": patient_name, "whatsapp_number": f"+9198{phone_suffix:08d}"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T{hour:02d}:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    client.post(f"/api/appointments/{created['id']}/confirm", headers=admin_headers)

    anchor = datetime.now(dt_timezone.utc) - timedelta(days=1)
    past_start = anchor + timedelta(hours=hour)
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
            (past_start, past_start + timedelta(minutes=30), created["id"]),
        )
    db_connection.commit()

    return {"patient": patient, "appointment_id": created["id"]}


def test_generate_queue_token_service_is_idempotent(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Idempotent Token",
        department_name="Idempotent Token Dept", appointment_type_name="Idempotent Token Type",
    )

    a = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, "Idempotent Patient A", 20000001, hour=9)

    # Check in via the real API/service path -- as of Phase 4 this no
    # longer assigns a token itself (mark_visited_service no longer
    # calls generate_queue_token_service -- see tests/test_queue_tokens.py
    # for that behavior). Call the extracted function directly here.
    visit_response = client.post(f"/api/appointments/{a['appointment_id']}/visit", headers=admin_headers)
    assert visit_response.status_code == 200
    assert visit_response.json()["token_number"] is None

    with db_connection.cursor() as cur:
        first = generate_queue_token_service(
            cur, a["appointment_id"], doctor_id=seeded["doctor_id"], doctor_tz="Asia/Kolkata"
        )
    db_connection.commit()
    assert first["token_number"] == 1
    assert first["newly_generated"] is True

    # Calling it again for the same appointment must return the same
    # token, not assign a new one.
    with db_connection.cursor() as cur:
        replay = generate_queue_token_service(
            cur, a["appointment_id"], doctor_id=seeded["doctor_id"], doctor_tz="Asia/Kolkata"
        )
    db_connection.commit()
    assert replay["token_number"] == 1
    assert replay["newly_generated"] is False

    # A second patient afterward must still get token 2 -- the replay
    # above must not have consumed a number.
    b = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, "Idempotent Patient B", 20000002, hour=10)
    client.post(f"/api/appointments/{b['appointment_id']}/visit", headers=admin_headers)
    with db_connection.cursor() as cur:
        second_patient = generate_queue_token_service(
            cur, b["appointment_id"], doctor_id=seeded["doctor_id"], doctor_tz="Asia/Kolkata"
        )
    db_connection.commit()
    assert second_patient["token_number"] == 2


def test_generate_queue_token_service_works_independently_of_check_in(client, db_connection):
    """Calling generate_queue_token_service directly for an appointment
    that was checked in without going through mark_visited_service (its
    visited_at set by a raw UPDATE, no token assigned) still assigns a
    token correctly -- proving this is a genuinely independent function,
    not just mark_visited_service's old body renamed."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Independent Token",
        department_name="Independent Token Dept", appointment_type_name="Independent Token Type",
    )

    a = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, "Independent Patient", 20000003, hour=9)

    # Simulate "arrived, but not yet through the token-issuing step" --
    # exactly the future Phase 3/4 state (checked in, payment/queue
    # entry still pending) -- by setting visited_at directly rather than
    # calling mark_visited_service.
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET status = 'CHECKED_IN', visited_at = NOW() WHERE id = %s",
            (a["appointment_id"],),
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        result = generate_queue_token_service(
            cur, a["appointment_id"], doctor_id=seeded["doctor_id"], doctor_tz="Asia/Kolkata"
        )
    db_connection.commit()

    assert result["token_number"] == 1

    with db_connection.cursor() as cur:
        cur.execute("SELECT token_number FROM appointments WHERE id = %s", (a["appointment_id"],))
        (stored_token,) = cur.fetchone()
    assert stored_token == 1


def test_payment_status_defaults_unpaid_on_new_appointment(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Payment Default",
        department_name="Payment Default Dept", appointment_type_name="Payment Default Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Payment Default Patient", "whatsapp_number": "+919800000004"},
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

    with db_connection.cursor() as cur:
        cur.execute("SELECT payment_status FROM appointments WHERE id = %s", (created["id"],))
        (payment_status,) = cur.fetchone()

    assert payment_status == "UNPAID"


def test_payment_status_check_constraint_rejects_invalid_value(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Payment Constraint",
        department_name="Payment Constraint Dept", appointment_type_name="Payment Constraint Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Payment Constraint Patient", "whatsapp_number": "+919800000005"},
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

    with pytest.raises(psycopg.errors.CheckViolation):
        with db_connection.cursor() as cur:
            cur.execute(
                "UPDATE appointments SET payment_status = 'NOT_A_REAL_STATUS' WHERE id = %s",
                (created["id"],),
            )
    db_connection.rollback()


def test_payment_status_backfill_marks_historical_checked_in_rows_waived(client, db_connection):
    """Reproduces migrations/0018's own backfill UPDATE against a row
    inserted after the migration already ran (this test suite applies
    every migration once, up front -- see conftest.py -- so there's no
    way to observe the migration acting on genuinely pre-existing data
    within a single test run). Runs the exact same statement the
    migration file uses, directly, to verify its logic: a CHECKED_IN or
    COMPLETED row's payment_status becomes WAIVED, not the column's
    UNPAID default."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Payment Backfill",
        department_name="Payment Backfill Dept", appointment_type_name="Payment Backfill Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Payment Backfill Patient", "whatsapp_number": "+919800000006"},
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

    # Simulate a row from before payment tracking existed: CHECKED_IN
    # (or COMPLETED), still carrying the column's UNPAID default because
    # nothing set it otherwise.
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET status = 'CHECKED_IN' WHERE id = %s",
            (created["id"],),
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        cur.execute("SELECT payment_status FROM appointments WHERE id = %s", (created["id"],))
        (before,) = cur.fetchone()
    assert before == "UNPAID"

    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET payment_status = 'WAIVED' WHERE status IN ('CHECKED_IN', 'COMPLETED')"
        )
    db_connection.commit()

    with db_connection.cursor() as cur:
        cur.execute("SELECT payment_status FROM appointments WHERE id = %s", (created["id"],))
        (after,) = cur.fetchone()
    assert after == "WAIVED"
