"""
Shared availability/slot-computation engine.

get_appointment_type_for_doctor() and get_available_slots() are a pure
move from app/api/booking.py (the WhatsApp conversational flow) -- both
booking.py and app/api/availability.py (the REST endpoint) now call this
one implementation instead of two independently-maintained copies.

Behavior is unchanged from booking.py's prior version. That version was
already the more complete of the two: it alone handled overnight
schedules (schedule_end <= schedule_start meaning "past midnight"),
while app/api/availability.py's old inline copy did not. Consolidating
onto booking.py's version is a deliberate, documented behavior change for
availability.py specifically -- verified to have no existing test
coverage depending on the old, incomplete behavior (see git history).
booking.py's own behavior is byte-for-byte unchanged: this is a pure
move, not a rewrite.

list_available_dates_in_range(), booking_window(), and
is_within_booking_window() are new for the web expansion. They are not
used by booking.py's WhatsApp flow (which keeps its own
get_available_dates() -- a different consumer: "first N dates with
availability within a rolling window" -- untouched by this file) or by
the existing app/api/appointments.py REST endpoint (calendar-window
enforcement there is opt-in via enforce_booking_window=False by default,
see app/services/appointment_services.py) so that neither the WhatsApp
UX nor the existing REST/test behavior changes as a side effect of this
phase.
"""

from datetime import date, time, timedelta
import calendar
import logging

from app.utils.timezone import (
    make_aware_datetime,
    ensure_aware_datetime,
    overlaps,
    get_doctor_timezone,
)

logger = logging.getLogger(__name__)

# "Current month + next 3 calendar months" per the web product spec.
BOOKING_WINDOW_EXTRA_MONTHS = 3


def get_appointment_type_for_doctor(
    cur,
    doctor_id: int,
    appointment_type_id: int,
):
    cur.execute(
        """
        SELECT
            at.id,
            at.name,
            dat.duration_minutes
        FROM doctor_appointment_types dat
        JOIN appointment_types at
            ON at.id = dat.appointment_type_id
        WHERE dat.doctor_id = %s
          AND dat.appointment_type_id = %s
          AND dat.active = TRUE
          AND at.active = TRUE
        """,
        (
            doctor_id,
            appointment_type_id,
        ),
    )

    row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "duration_minutes": row[2],
    }


def get_available_slots(
    cur,
    doctor_id: int,
    appointment_type_id: int,
    selected_date: date,
):
    """
    Calculate available appointment slots.

    Important:
    - Schedule times come from TIME columns and are naive.
    - Blocks and appointments come from TIMESTAMPTZ and are aware.
    - We convert schedule times into timezone-aware datetimes before
      comparing them with blocks/appointments.
    """

    # ---------------------------------------------------------
    # Get appointment duration
    # ---------------------------------------------------------

    appointment_type = get_appointment_type_for_doctor(
        cur,
        doctor_id,
        appointment_type_id,
    )

    if appointment_type is None:
        return []

    duration_minutes = appointment_type["duration_minutes"]

    # ---------------------------------------------------------
    # Get doctor timezone
    # ---------------------------------------------------------

    try:
        doctor_tz = get_doctor_timezone(cur, doctor_id)
    except Exception as e:
        logger.error(f"Failed to get timezone for doctor {doctor_id}: {e}")
        # Fallback to Asia/Kolkata
        doctor_tz = "Asia/Kolkata"

    # ---------------------------------------------------------
    # Weekday
    # Monday = 1 ... Sunday = 7
    # ---------------------------------------------------------

    day_of_week = selected_date.weekday() + 1

    # ---------------------------------------------------------
    # Doctor schedule
    # ---------------------------------------------------------

    cur.execute(
        """
        SELECT
            start_time,
            end_time
        FROM doctor_schedule
        WHERE doctor_id = %s
          AND day_of_week = %s
          AND active = TRUE
          AND (start_date IS NULL OR start_date <= %s)
          AND (end_date IS NULL OR end_date >= %s)
        ORDER BY start_time
        """,
        (
            doctor_id,
            day_of_week,
            selected_date,
            selected_date,
        ),
    )

    schedules = cur.fetchall()

    if not schedules:
        return []

    # ---------------------------------------------------------
    # Full requested day
    # ---------------------------------------------------------

    day_start = make_aware_datetime(
        selected_date,
        time.min,
        doctor_tz,
    )

    day_end = day_start + timedelta(days=1)

    # ---------------------------------------------------------
    # Doctor blocks
    # ---------------------------------------------------------

    cur.execute(
        """
        SELECT
            start_at,
            end_at
        FROM doctor_blocks
        WHERE doctor_id = %s
          AND active = TRUE
          AND start_at < %s
          AND end_at > %s
        ORDER BY start_at
        """,
        (
            doctor_id,
            day_end,
            day_start,
        ),
    )

    blocks = cur.fetchall()

    # ---------------------------------------------------------
    # Existing appointments
    # ---------------------------------------------------------

    cur.execute(
        """
        SELECT
            start_at,
            end_at
        FROM appointments
        WHERE doctor_id = %s
          AND start_at < %s
          AND end_at > %s
          AND status <> 'CANCELLED'
        ORDER BY start_at
        """,
        (
            doctor_id,
            day_end,
            day_start,
        ),
    )

    appointments = cur.fetchall()

    # ---------------------------------------------------------
    # Calculate slots
    # ---------------------------------------------------------

    slots = []

    for schedule_start, schedule_end in schedules:

        # TIME values from PostgreSQL are naive.
        # Convert them to aware datetimes using the business timezone.

        current_start = make_aware_datetime(
            selected_date,
            schedule_start,
            doctor_tz,
        )

        schedule_end_at = make_aware_datetime(
            selected_date,
            schedule_end,
            doctor_tz,
        )

        # Handle overnight schedules.
        if schedule_end_at <= current_start:
            schedule_end_at += timedelta(days=1)

        while (
            current_start
            + timedelta(minutes=duration_minutes)
            <= schedule_end_at
        ):
            current_end = (
                current_start
                + timedelta(minutes=duration_minutes)
            )

            slot_available = True

            # -------------------------------------------------
            # Check blocks
            # -------------------------------------------------

            for block_start, block_end in blocks:

                # Ensure timezone-aware
                block_start = ensure_aware_datetime(block_start, doctor_tz)
                block_end = ensure_aware_datetime(block_end, doctor_tz)

                if overlaps(
                    current_start,
                    current_end,
                    block_start,
                    block_end,
                ):
                    slot_available = False
                    break

            # -------------------------------------------------
            # Check appointments
            # -------------------------------------------------

            if slot_available:
                for appointment_start, appointment_end in appointments:

                    # Ensure timezone-aware
                    appointment_start = ensure_aware_datetime(
                        appointment_start,
                        doctor_tz
                    )
                    appointment_end = ensure_aware_datetime(
                        appointment_end,
                        doctor_tz
                    )

                    if overlaps(
                        current_start,
                        current_end,
                        appointment_start,
                        appointment_end,
                    ):
                        slot_available = False
                        break

            if slot_available:
                slots.append(
                    {
                        "start_at": current_start.isoformat(),
                        "end_at": current_end.isoformat(),
                    }
                )

            current_start = current_end

    return slots


def booking_window(today: date | None = None) -> tuple[date, date]:
    """
    Return (window_start, window_end), both inclusive: the web booking
    window is "current month + next BOOKING_WINDOW_EXTRA_MONTHS calendar
    months" per the product spec -- a calendar-month boundary, not a
    fixed day count (e.g. booking in September: window_end is 31 Dec,
    regardless of September having 30 days).
    """
    if today is None:
        today = date.today()

    window_start = today

    end_year = today.year
    end_month = today.month + BOOKING_WINDOW_EXTRA_MONTHS
    while end_month > 12:
        end_month -= 12
        end_year += 1

    last_day_of_end_month = calendar.monthrange(end_year, end_month)[1]
    window_end = date(end_year, end_month, last_day_of_end_month)

    return window_start, window_end


def is_within_booking_window(candidate: date, today: date | None = None) -> bool:
    """
    True if candidate is not in the past and falls on or before the last
    day of the current month + BOOKING_WINDOW_EXTRA_MONTHS.
    """
    if today is None:
        today = date.today()

    window_start, window_end = booking_window(today)

    return window_start <= candidate <= window_end


def list_available_dates_in_range(
    cur,
    doctor_id: int,
    appointment_type_id: int,
    start_date: date,
    end_date: date,
):
    """
    For every date in [start_date, end_date] (inclusive), whether it has
    at least one available slot. Intended for a web calendar view (one
    doctor/appointment-type at a time, one HTTP request per rendered
    month) -- not used by the WhatsApp flow or by anything yet, since no
    endpoint calls this until the web availability API is built.

    Returns {"YYYY-MM-DD": bool, ...}.
    """
    result = {}
    current = start_date

    while current <= end_date:
        slots = get_available_slots(
            cur,
            doctor_id,
            appointment_type_id,
            current,
        )
        result[current.isoformat()] = bool(slots)
        current += timedelta(days=1)

    return result
