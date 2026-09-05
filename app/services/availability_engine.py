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
    department_id: int | None = None,
):
    """
    Calculate available appointment slots.

    Important:
    - Schedule times come from TIME columns and are naive.
    - Blocks and appointments come from TIMESTAMPTZ and are aware.
    - We convert schedule times into timezone-aware datetimes before
      comparing them with blocks/appointments.

    department_id (migrations/0010) narrows which doctor_schedule rows
    apply: a row with a NULL department_id always applies, and a row
    with a non-NULL department_id applies only when it matches. Passing
    department_id=None here (the default) applies no department filter
    at all -- every active row for this doctor/day counts, department-
    scoped or not -- which is exactly the pre-0010 behavior every
    caller that doesn't yet have a department in scope (admin booking,
    reschedule) still gets unchanged.
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
          AND (%s::bigint IS NULL OR department_id IS NULL OR department_id = %s)
        ORDER BY start_time
        """,
        (
            doctor_id,
            day_of_week,
            selected_date,
            selected_date,
            department_id,
            department_id,
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
          -- CANCELLED and REJECTED both release the slot; every other
          -- status (PENDING included, so a request awaiting confirmation
          -- still blocks the slot) counts as occupying it -- see
          -- app/services/appointment_services.py's RELEASED_STATUSES,
          -- the canonical definition this mirrors.
          AND NOT (status = ANY(ARRAY['CANCELLED', 'REJECTED']))
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


def get_doctors_offering_appointment_type(
    cur,
    department_id: int,
    appointment_type_id: int,
):
    """
    Doctors who are BOTH assigned to this department AND actively offer
    this appointment type (with their own doctor-specific duration) --
    the candidate set for Date-First aggregation. A doctor in the
    department who does not offer this type is correctly excluded here,
    not just later when their slot count happens to be zero.
    """
    cur.execute(
        """
        SELECT DISTINCT
            d.id,
            d.name
        FROM doctor_departments dd
        JOIN doctors d
            ON d.id = dd.doctor_id
        JOIN doctor_appointment_types dat
            ON dat.doctor_id = d.id
           AND dat.appointment_type_id = %s
           AND dat.active = TRUE
        WHERE dd.department_id = %s
          AND d.active = TRUE
        ORDER BY d.name
        """,
        (appointment_type_id, department_id),
    )

    return [{"id": row[0], "name": row[1]} for row in cur.fetchall()]


def get_appointment_types_for_department(cur, department_id: int):
    """
    Every appointment type offered by at least one active doctor in this
    department -- the Date-First flow's "Appointment Type" step needs
    this before any doctor is chosen, unlike the existing per-doctor
    get_appointment_types_for_doctor()/get_doctor_appointment_types()
    (app/api/booking.py / app/api/doctor_appointment_types.py), which
    both require a doctor_id up front. Duration is deliberately not
    returned here -- it is per doctor+type (doctor_appointment_types.
    duration_minutes), so it is only meaningful once a specific doctor
    is known, same as the Doctor-First flow already assumes.
    """
    cur.execute(
        """
        SELECT DISTINCT
            at.id,
            at.name
        FROM doctor_departments dd
        JOIN doctor_appointment_types dat
            ON dat.doctor_id = dd.doctor_id
           AND dat.active = TRUE
        JOIN appointment_types at
            ON at.id = dat.appointment_type_id
           AND at.active = TRUE
        JOIN doctors d
            ON d.id = dd.doctor_id
           AND d.active = TRUE
        WHERE dd.department_id = %s
        ORDER BY at.name
        """,
        (department_id,),
    )

    # active is always True here (the query above already requires it),
    # included so this matches the frontend's existing
    # AppointmentTypeSummary shape (app/api/appointment_types.py's own
    # catalog listing) rather than needing a third, near-identical type.
    return [{"id": row[0], "name": row[1], "active": True} for row in cur.fetchall()]


def list_available_dates_for_department(
    cur,
    department_id: int,
    appointment_type_id: int,
    start_date: date,
    end_date: date,
):
    """
    Date-First's aggregate month calendar: for every date in range,
    whether ANY doctor in the department who offers this appointment
    type has at least one real, bookable slot -- not merely whether a
    doctor is scheduled to work that day (a day fully consumed by
    existing appointments/blocks is correctly reported unavailable,
    since this loops the exact same get_available_slots() a single-
    doctor calendar uses, per doctor, and ORs the per-day booleans).

    A simple loop across doctors is intentional here, not a missing
    optimization -- see this module's callers for why (small clinic,
    correctness over premature optimization).
    """
    doctors = get_doctors_offering_appointment_type(
        cur, department_id, appointment_type_id
    )

    result = {}
    current = start_date
    while current <= end_date:
        result[current.isoformat()] = False
        current += timedelta(days=1)

    for doctor in doctors:
        per_doctor = list_available_dates_in_range(
            cur,
            doctor["id"],
            appointment_type_id,
            start_date,
            end_date,
            department_id=department_id,
        )
        for iso_date, is_available in per_doctor.items():
            if is_available:
                result[iso_date] = True

    return result


def list_doctors_with_slots_for_date(
    cur,
    department_id: int,
    appointment_type_id: int,
    selected_date: date,
):
    """
    Date-First's per-date doctor list: every doctor in the department
    offering this appointment type who has at least one real slot on
    this date, each with their actual slots -- a doctor with zero valid
    slots for this date/type is omitted entirely (never returned with an
    empty slots list), so callers never need to filter again.

    Returns [{"id", "name", "slots": [...]}, ...], ordered by doctor
    name. Each doctor's slots are exactly what get_available_slots()
    already returns for that doctor -- no new slot-shape or duration
    logic here.
    """
    doctors = get_doctors_offering_appointment_type(
        cur, department_id, appointment_type_id
    )

    results = []
    for doctor in doctors:
        slots = get_available_slots(
            cur,
            doctor["id"],
            appointment_type_id,
            selected_date,
            department_id=department_id,
        )
        if slots:
            results.append(
                {
                    "id": doctor["id"],
                    "name": doctor["name"],
                    "slots": slots,
                }
            )

    return results


def list_available_dates_in_range(
    cur,
    doctor_id: int,
    appointment_type_id: int,
    start_date: date,
    end_date: date,
    department_id: int | None = None,
):
    """
    For every date in [start_date, end_date] (inclusive), whether it has
    at least one available slot. Intended for a web calendar view (one
    doctor/appointment-type at a time, one HTTP request per rendered
    month) -- not used by the WhatsApp flow or by anything yet, since no
    endpoint calls this until the web availability API is built.

    department_id is passed straight through to get_available_slots --
    see its docstring (migrations/0010).

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
            department_id=department_id,
        )
        result[current.isoformat()] = bool(slots)
        current += timedelta(days=1)

    return result
