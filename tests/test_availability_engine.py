"""
Tests for app/services/availability_engine.py, added in the WEB P1
phase.

booking_window()/is_within_booking_window() are pure functions (no DB)
covering the "current month + next 3 calendar months" web booking window
-- calendar-month based, not a fixed day count, per the product spec.

test_availability_endpoint_handles_overnight_schedule is a regression
test proving the P1 consolidation: app/api/availability.py's old inline
slot logic did not handle overnight schedules (schedule_end <=
schedule_start), unlike app/api/booking.py's. Both now call the same
app/services/availability_engine.get_available_slots, so the REST
endpoint must now handle it too -- this documents that as a deliberate
fix, not a silent behavior change.

list_available_dates_in_range() is new, for a future web calendar view;
no endpoint calls it yet in this phase.
"""

from datetime import date, datetime, timedelta

from app.services.availability_engine import (
    booking_window,
    is_within_booking_window,
    list_available_dates_in_range,
)

from tests.helpers import seed_basic_doctor


# ---------------------------------------------------------------------
# booking_window() / is_within_booking_window() -- pure, no DB needed.
# ---------------------------------------------------------------------

def test_booking_window_is_calendar_month_based_within_year():
    today = date(2026, 9, 2)
    window_start, window_end = booking_window(today)
    assert window_start == date(2026, 9, 2)
    # September + 3 more calendar months = December, last day 31.
    assert window_end == date(2026, 12, 31)


def test_booking_window_rolls_over_year_boundary():
    today = date(2026, 11, 15)
    window_start, window_end = booking_window(today)
    # November + 3 more calendar months = February 2027, last day 28
    # (2027 is not a leap year).
    assert window_end == date(2027, 2, 28)


def test_is_within_booking_window_allows_today():
    today = date(2026, 9, 2)
    assert is_within_booking_window(today, today=today) is True


def test_is_within_booking_window_allows_last_day_of_window():
    today = date(2026, 9, 2)
    _, window_end = booking_window(today)
    assert is_within_booking_window(window_end, today=today) is True


def test_is_within_booking_window_rejects_day_after_window():
    today = date(2026, 9, 2)
    _, window_end = booking_window(today)
    day_after = window_end + timedelta(days=1)
    assert is_within_booking_window(day_after, today=today) is False


def test_is_within_booking_window_rejects_past_date():
    today = date(2026, 9, 2)
    yesterday = today - timedelta(days=1)
    assert is_within_booking_window(yesterday, today=today) is False


# ---------------------------------------------------------------------
# Overnight-schedule regression, exercised through the REST endpoint.
# ---------------------------------------------------------------------

def test_availability_endpoint_handles_overnight_schedule(client, db_connection):
    """
    app/api/doctor_schedule.py's own POST endpoint rejects end_time <=
    start_time (its DoctorScheduleCreate validator requires end_time
    strictly after start_time) -- so an overnight schedule can't be
    created through the admin API today at all, only by direct SQL, as
    this test does. That's a pre-existing inconsistency (the database
    has no such CHECK constraint -- see migrations/0001's documented
    reasoning -- but the API layer above it does), separate from this
    phase's engine-consolidation work; flagged in the WEB P1 report as a
    finding for WEB P7 (doctor availability management), not fixed here.
    The engine itself has always handled it (it's booking.py's original
    logic), so this test proves the REST endpoint now shares that
    handling too, however the schedule row gets in.
    """
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Overnight",
        appointment_type_name="Overnight Consultation",
        schedule_days=(),
        duration_minutes=30,
    )

    candidate = date.today()
    while (candidate.weekday() + 1) != 3:
        candidate += timedelta(days=1)

    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO doctor_schedule (doctor_id, day_of_week, start_time, end_time)
            VALUES (%s, 3, '22:00', '02:00')
            """,
            (seeded["doctor_id"],),
        )
    db_connection.commit()

    response = client.post(
        "/api/availability",
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "date": candidate.isoformat(),
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["slots"], (
        "expected overnight slots (22:00 -> 02:00 next day), got none -- "
        "overnight handling regressed after the P1 consolidation"
    )
    assert len(body["slots"]) == 8

    first_start = datetime.fromisoformat(body["slots"][0]["start_at"])
    last_start = datetime.fromisoformat(body["slots"][-1]["start_at"])
    assert first_start.hour == 22
    assert last_start.hour == 1


# ---------------------------------------------------------------------
# list_available_dates_in_range()
# ---------------------------------------------------------------------

def test_list_available_dates_in_range(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Range",
        appointment_type_name="Range Consultation",
        schedule_days=(1, 2, 3, 4, 5),
    )

    start = date.today()
    end = start + timedelta(days=9)

    with db_connection.cursor() as cur:
        result = list_available_dates_in_range(
            cur,
            seeded["doctor_id"],
            seeded["appointment_type_id"],
            start,
            end,
        )

    expected_keys = {
        (start + timedelta(days=i)).isoformat()
        for i in range((end - start).days + 1)
    }
    assert set(result.keys()) == expected_keys

    for iso_date, is_available in result.items():
        weekday = date.fromisoformat(iso_date).weekday() + 1
        expected = weekday in (1, 2, 3, 4, 5)
        assert is_available is expected, (
            f"{iso_date}: expected availability={expected}, got {is_available}"
        )
