"""
Tests for the "past dates/times are never schedulable" fix in
app/services/availability_engine.py's get_available_slots().

Before this fix, get_available_slots() computed slots purely from
schedule/blocks/appointments with no concept of "now" at all -- a date
entirely in the past, or a slot on today whose start time had already
passed, would still be reported as open as long as the doctor's weekly
schedule matched. This was masked for the patient-facing web calendar
(app/api/patient_booking.py's GET /web/calendar*, via
is_within_scheduling_window(), whose window_start is always today) but not
for the admin calendar (GET /appointments/calendar, deliberately exempt
from that window so staff can schedule far in the future) -- which is
exactly the bug reported against the admin "Book Appointment" screen:
2 Sep showing as available when today was 5 Sep.

The fix lives in the one shared engine function every scheduling path
(admin REST, patient web both flows, WhatsApp) calls, so it's verified
here directly against that function plus the two REST endpoints most
directly implicated (the admin calendar, and the admin/WhatsApp-shared
POST /api/availability slot list) -- not re-testing every consumer,
since they're all thin wrappers already covered by their own test files.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.availability_engine import get_available_slots, list_available_dates_in_range
from tests.helpers import create_admin_and_get_headers, seed_basic_doctor

TZ = "Asia/Kolkata"


def _doctor_local_now() -> datetime:
    return datetime.now(ZoneInfo(TZ))


# ---------------------------------------------------------------------
# Past dates -- never schedulable, regardless of what the weekly schedule
# says about that weekday.
# ---------------------------------------------------------------------


def test_past_date_returns_no_slots(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Past Date",
        department_name="Past Date Dept",
        appointment_type_name="Past Date Type",
        timezone=TZ,
        # Every day of the week, wide open hours -- so the *only*
        # possible reason for an empty result is the past-date check
        # itself, not a schedule/weekday mismatch.
        schedule_days=(1, 2, 3, 4, 5, 6, 7),
        start_time="00:00",
        end_time="23:45",
    )
    yesterday = _doctor_local_now().date() - timedelta(days=1)

    with db_connection.cursor() as cur:
        slots = get_available_slots(
            cur, seeded["doctor_id"], seeded["appointment_type_id"], yesterday
        )

    assert slots == []


def test_far_past_date_returns_no_slots(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Far Past Date",
        department_name="Far Past Date Dept",
        appointment_type_name="Far Past Date Type",
        timezone=TZ,
        schedule_days=(1, 2, 3, 4, 5, 6, 7),
        start_time="00:00",
        end_time="23:45",
    )
    long_ago = _doctor_local_now().date() - timedelta(days=30)

    with db_connection.cursor() as cur:
        slots = get_available_slots(
            cur, seeded["doctor_id"], seeded["appointment_type_id"], long_ago
        )

    assert slots == []


def test_admin_calendar_never_reports_a_past_date_as_available(client, db_connection):
    # End-to-end reproduction of the reported bug: GET /appointments/
    # calendar (the admin Book Appointment screen's data source) must
    # never mark a day before today as available, for any weekday.
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Admin Calendar Past",
        department_name="Admin Calendar Past Dept",
        appointment_type_name="Admin Calendar Past Type",
        timezone=TZ,
        schedule_days=(1, 2, 3, 4, 5, 6, 7),
        start_time="00:00",
        end_time="23:45",
    )
    today = _doctor_local_now().date()

    response = client.get(
        "/api/appointments/calendar",
        params={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "year": today.year,
            "month": today.month,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200
    dates = response.json()["dates"]

    for iso_date, is_available in dates.items():
        if date.fromisoformat(iso_date) < today:
            assert is_available is False, f"{iso_date} is in the past but was reported available"


# ---------------------------------------------------------------------
# Today -- selectable only via its still-future slots.
# ---------------------------------------------------------------------


def test_todays_returned_slots_never_start_before_now(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Today Slots",
        department_name="Today Slots Dept",
        appointment_type_name="Today Slots Type",
        timezone=TZ,
        schedule_days=(1, 2, 3, 4, 5, 6, 7),
        start_time="00:00",
        end_time="23:45",
        duration_minutes=15,
    )
    now = _doctor_local_now()
    today = now.date()

    with db_connection.cursor() as cur:
        slots = get_available_slots(
            cur, seeded["doctor_id"], seeded["appointment_type_id"], today
        )

    # Not asserting slots is non-empty (it would be, all but the last
    # ~15 minutes of the day) -- the meaningful, never-flaky assertion
    # is that whatever comes back never starts at or before now. Against
    # the pre-fix code, a 00:00-23:45 schedule would include slots from
    # the start of the day regardless of the time this test happens to
    # run, so this does exercise the fix, not just pass vacuously.
    for slot in slots:
        start_at = datetime.fromisoformat(slot["start_at"])
        assert start_at > now, f"slot {slot['start_at']} has already started/passed"


def test_todays_slot_just_before_now_is_excluded_but_one_just_after_is_kept(client, db_connection):
    now = _doctor_local_now()
    # Skip near midnight: constructing "now +/- 40 minutes" as a naive
    # HH:MM schedule window would wrap across a date boundary, which
    # the admin schedule API's own validator (end_time must be after
    # start_time) rejects outright -- not what this test is about.
    if now.hour < 1 or now.hour > 22:
        pytest.skip("too close to local midnight to build a same-day straddling schedule")

    schedule_start = (now - timedelta(minutes=40)).time().replace(second=0, microsecond=0)
    schedule_end = (now + timedelta(minutes=40)).time().replace(second=0, microsecond=0)

    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Straddle Now",
        department_name="Straddle Now Dept",
        appointment_type_name="Straddle Now Type",
        timezone=TZ,
        schedule_days=(now.isoweekday(),),
        start_time=schedule_start.strftime("%H:%M"),
        end_time=schedule_end.strftime("%H:%M"),
        duration_minutes=20,
    )

    with db_connection.cursor() as cur:
        slots = get_available_slots(
            cur, seeded["doctor_id"], seeded["appointment_type_id"], now.date()
        )

    starts = [datetime.fromisoformat(s["start_at"]) for s in slots]

    # The schedule's first slot (schedule_start, 40 minutes before now)
    # has already started, and every 20-minute slot up to and including
    # the one covering "now" itself must be excluded -- none of what
    # comes back may start at or before now.
    for s in starts:
        assert s > now, f"slot starting at {s.isoformat()} has already started/passed"

    # And there must be at least one slot from the still-open second
    # half of the window (schedule_end is 40 minutes after now, with a
    # 20-minute duration, so at least one full slot starts after now) --
    # proving this isn't just an empty result.
    assert starts, "expected at least one open slot in the still-future half of the schedule"


# ---------------------------------------------------------------------
# list_available_dates_in_range (the calendar month view) -- today's
# boolean must reflect whether it has at least one remaining slot, not
# just whether the weekday matches.
# ---------------------------------------------------------------------


def test_today_unavailable_once_its_schedule_window_has_fully_passed(client, db_connection):
    now = _doctor_local_now()
    if now.hour < 1:
        pytest.skip("too close to local midnight to build a same-day already-closed schedule")

    # A schedule that closed 5 minutes ago -- today must show as
    # unavailable even though the weekday itself is scheduled.
    schedule_start = time(0, 0)
    schedule_end = (now - timedelta(minutes=5)).time().replace(second=0, microsecond=0)
    if schedule_end <= schedule_start:
        pytest.skip("too close to local midnight to build a same-day already-closed schedule")

    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Already Closed Today",
        department_name="Already Closed Today Dept",
        appointment_type_name="Already Closed Today Type",
        timezone=TZ,
        schedule_days=(now.isoweekday(),),
        start_time=schedule_start.strftime("%H:%M"),
        end_time=schedule_end.strftime("%H:%M"),
        duration_minutes=30,
    )

    with db_connection.cursor() as cur:
        result = list_available_dates_in_range(
            cur, seeded["doctor_id"], seeded["appointment_type_id"], now.date(), now.date()
        )

    assert result[now.date().isoformat()] is False


def test_today_available_when_a_slot_remains_later_today(client, db_connection):
    now = _doctor_local_now()
    if now.hour > 22:
        pytest.skip("too close to local midnight to build a same-day still-open schedule")

    schedule_start = (now + timedelta(minutes=5)).time().replace(second=0, microsecond=0)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Still Open Today",
        department_name="Still Open Today Dept",
        appointment_type_name="Still Open Today Type",
        timezone=TZ,
        schedule_days=(now.isoweekday(),),
        start_time=schedule_start.strftime("%H:%M"),
        end_time="23:45",
        duration_minutes=15,
    )

    with db_connection.cursor() as cur:
        result = list_available_dates_in_range(
            cur, seeded["doctor_id"], seeded["appointment_type_id"], now.date(), now.date()
        )

    assert result[now.date().isoformat()] is True


# ---------------------------------------------------------------------
# Timezone correctness -- must use the *doctor's* local date/time, not
# the server's (this test environment's server clock runs UTC) or the
# caller's.
# ---------------------------------------------------------------------


def test_uses_doctor_timezone_not_utc_for_past_date_check(client, db_connection):
    # A timezone far enough ahead of UTC that "UTC's today" can already
    # be *yesterday* in the doctor's own local time -- if the past-date
    # check used UTC (or the server's local clock, which this sandbox
    # also runs as UTC) instead of the doctor's configured timezone, it
    # would wrongly treat UTC's still-current date as schedulable for this
    # doctor.
    ahead_tz = "Pacific/Kiritimati"  # UTC+14
    utc_today = datetime.now(ZoneInfo("UTC")).date()
    doctor_local_today = datetime.now(ZoneInfo(ahead_tz)).date()

    if doctor_local_today == utc_today:
        pytest.skip("UTC and Pacific/Kiritimati happen to agree on today's date right now")

    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Ahead Timezone",
        department_name="Ahead Timezone Dept",
        appointment_type_name="Ahead Timezone Type",
        timezone=ahead_tz,
        schedule_days=(1, 2, 3, 4, 5, 6, 7),
        start_time="00:00",
        end_time="23:45",
    )

    with db_connection.cursor() as cur:
        slots = get_available_slots(
            cur, seeded["doctor_id"], seeded["appointment_type_id"], utc_today
        )

    # utc_today is already yesterday (or earlier) in this doctor's own
    # timezone -- must be treated as past for them, even though it's
    # still "today" in UTC.
    assert slots == []
