"""
Shared appointment create/cancel logic.

create_appointment_service() and cancel_appointment_service() are a pure
move of the bodies of app/api/appointments.py's POST/DELETE handlers --
extracted so a future authenticated web endpoint can call the exact same
scheduling rules (schedule/block/overlap checks, the pg_advisory_xact_lock
serialization, the EXCLUDE-constraint backstop) instead of a third
reimplementation. app/api/appointments.py's router functions are now
thin wrappers: build the connection/cursor, call the service, translate
its typed exceptions (app/services/exceptions.py) into the exact same
HTTPException status codes and messages they raised before this move --
verified by the existing test suite (tests/test_concurrency.py,
tests/test_exclusion_constraint.py) passing unchanged.

Two parameters were added on top of the moved logic, both opt-in and
both defaulting to today's exact behavior:

- create_appointment_service(..., enforce_scheduling_window=False): when
  True, rejects a start_at outside the current-month+3-calendar-months
  web scheduling window (app/services/availability_engine.py). Defaults to
  False so the existing REST endpoint and every existing test keeps
  scheduling any future date, same as before this phase. A future web
  scheduling endpoint (WEB P3/P4) passes True.

- cancel_appointment_service(..., requesting_patient_id=None): when
  given a patient id that does not own the appointment, raises
  NotAppointmentOwner. Defaults to None, which skips the check entirely
  -- today's DELETE /api/appointments/{id} endpoint still doesn't
  authenticate its caller (WEB P2 hasn't been built yet), so passing a
  patient id here today would either be a no-op (if guessed correctly)
  or trivially spoofable (patient ids are not secret), which is not a
  real fix and would be misleading to present as one. What this move
  does provide: once WEB P2 gives the web endpoints a real, authenticated
  patient id, the very first web cancel endpoint (WEB P4) can pass it
  here and get genuine, tested ownership enforcement for free, without
  a fourth reimplementation of the cancel rules. See
  docs/WEB_EXPANSION_ARCHITECTURE.md section 10 item 4 and this phase's
  report for the full explanation of why the ownership gap cannot be
  genuinely closed before patient identity exists.
"""

from datetime import date, datetime, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

import psycopg

from app.utils.timezone import overlaps, convert_to_timezone, validate_timezone
from app.services.availability_engine import (
    get_appointment_type_for_doctor,
    is_within_scheduling_window,
)
from app.services.exceptions import (
    DoctorNotFound,
    PatientNotFound,
    AppointmentTypeNotAssigned,
    OutsideDoctorSchedule,
    DoctorBlockConflict,
    SlotOverlap,
    OutsideSchedulingWindow,
    AppointmentNotFound,
    AlreadyCancelled,
    NotAppointmentOwner,
    InvalidStatusTransition,
    AppointmentNotStarted,
)

logger = logging.getLogger(__name__)

# The appointment lifecycle (migrations/0011_appointment_lifecycle_
# statuses.sql): every appointment starts PENDING and moves through
# staff-driven transitions. CANCELLED and REJECTED are the two "never
# happened" terminal states -- both release the doctor's slot; every
# other status (including PENDING itself, so two patients can't be
# offered the same slot while one request awaits confirmation) counts as
# occupying it. This mirrors the exact predicate used by the EXCLUDE
# constraint and the two partial indexes in that same migration -- keep
# all three in lockstep if this set ever changes again.
RELEASED_STATUSES = ("CANCELLED", "REJECTED")

# Statuses a patient/staff can still cancel or reschedule out of.
ACTIONABLE_STATUSES = ("PENDING", "CONFIRMED")


def create_appointment_service(
    cur,
    *,
    doctor_id: int,
    patient_id: int,
    appointment_type_id: int,
    start_at,
    enforce_scheduling_window: bool = False,
):
    # Normalize seconds/microseconds.
    start_at = start_at.replace(
        second=0,
        microsecond=0,
    )

    if enforce_scheduling_window and not is_within_scheduling_window(start_at.date()):
        raise OutsideSchedulingWindow()

    # ---------------------------------------------------------
    # 1. Check doctor.
    #
    # Deliberately a plain SELECT, not FOR UPDATE. It used to be
    # FOR UPDATE (the theory being that locking the doctors row
    # would serialize appointment creation for that doctor) --
    # that was removed while adding the pg_advisory_xact_lock
    # below, for two reasons: (a) it never actually serialized
    # against the WhatsApp path anyway, since scheduling.py doesn't
    # take that same lock (this is exactly the cross-path gap
    # documented in docs/DATABASE_P1_NOTES.md item 4), and
    # (b) worse, it actively caused a real, reproduced deadlock
    # once both paths used pg_advisory_xact_lock: a transaction
    # holding FOR UPDATE on the doctors row while waiting for the
    # advisory lock, racing against a transaction holding the
    # advisory lock while waiting to INSERT (which needs an
    # implicit FOR KEY SHARE lock on that same doctors row via
    # the appointments.doctor_id foreign key) -- a circular wait.
    # Both paths must acquire locks in the same order; the
    # advisory lock below is that one, shared order.
    # ---------------------------------------------------------
    cur.execute(
        """
        SELECT id
        FROM doctors
        WHERE id = %s
          AND active = TRUE
        """,
        (doctor_id,),
    )

    if cur.fetchone() is None:
        raise DoctorNotFound()

    # ---------------------------------------------------------
    # 2. Check patient
    # ---------------------------------------------------------
    cur.execute(
        """
        SELECT id
        FROM patients
        WHERE id = %s
        """,
        (patient_id,),
    )

    if cur.fetchone() is None:
        raise PatientNotFound()

    # ---------------------------------------------------------
    # 3. Check appointment type assignment
    # ---------------------------------------------------------
    cur.execute(
        """
        SELECT
            dat.duration_minutes,
            at.name
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

    appointment_type = cur.fetchone()

    if appointment_type is None:
        raise AppointmentTypeNotAssigned()

    duration_minutes = appointment_type[0]

    end_at = start_at + timedelta(
        minutes=duration_minutes
    )

    # ---------------------------------------------------------
    # 4. Check doctor's schedule
    # ---------------------------------------------------------
    day_of_week = start_at.weekday() + 1
    start_time = start_at.time()
    end_time = end_at.time()

    cur.execute(
        """
        SELECT id
        FROM doctor_schedule
        WHERE doctor_id = %s
          AND day_of_week = %s
          AND active = TRUE
          AND start_time <= %s
          AND end_time >= %s
          AND (start_date IS NULL OR start_date <= %s)
          AND (end_date IS NULL OR end_date >= %s)
        LIMIT 1
        """,
        (
            doctor_id,
            day_of_week,
            start_time,
            end_time,
            start_at.date(),
            start_at.date(),
        ),
    )

    if cur.fetchone() is None:
        raise OutsideDoctorSchedule()

    # ---------------------------------------------------------
    # 5. Check doctor blocks
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
        FOR UPDATE
        """,
        (
            doctor_id,
            end_at,
            start_at,
        ),
    )

    blocks = cur.fetchall()

    for block_start, block_end in blocks:
        if overlaps(
            start_at,
            end_at,
            block_start,
            block_end,
        ):
            raise DoctorBlockConflict()

    # ---------------------------------------------------------
    # 6. Serialize scheduling attempts for this doctor.
    #
    # Standardized on the same primitive app/api/scheduling.py's
    # WhatsApp flow uses: pg_advisory_xact_lock(doctor_id). This
    # is a session/transaction-scoped Postgres advisory lock,
    # released automatically on commit or rollback -- it is NOT
    # the same lock as the "doctors ... FOR UPDATE" row lock
    # taken in step 1 above (that lock only blocks other
    # transactions that also do a FOR UPDATE read of the same
    # doctors row; it does not block a transaction that only
    # takes this advisory lock, or vice versa). Both scheduling
    # paths must take the *same* lock, on the *same* key
    # (doctor_id, cast to bigint for pg_advisory_xact_lock's
    # signature), or a WhatsApp scheduling and a direct REST scheduling
    # for the same doctor/slot can both pass their overlap check
    # and both insert -- this was confirmed to happen in testing
    # before this change (see docs/DATABASE_P1_NOTES.md item 4).
    #
    # Position matters: acquired after the doctor-block check
    # above (matching app/api/scheduling.py's CONFIRM_SCHEDULING flow
    # exactly) and before the final existing-appointments
    # overlap re-check below, held until this transaction
    # commits or rolls back (i.e. through the INSERT).
    # ---------------------------------------------------------
    cur.execute(
        "SELECT pg_advisory_xact_lock(%s::bigint)",
        (doctor_id,),
    )

    # ---------------------------------------------------------
    # 7. Re-check existing appointments now that the lock for
    # this doctor is held -- no other transaction can be
    # concurrently inserting/re-checking for this doctor_id.
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
          -- status::text: see availability_engine.py's get_available_slots
          -- for why (enum-typed appointments.status on some databases).
          AND NOT (status::text = ANY(%s::text[]))
        ORDER BY start_at
        """,
        (
            doctor_id,
            end_at,
            start_at,
            list(RELEASED_STATUSES),
        ),
    )

    existing_appointments = cur.fetchall()

    for existing_start, existing_end in existing_appointments:
        if overlaps(
            start_at,
            end_at,
            existing_start,
            existing_end,
        ):
            raise SlotOverlap()

    # ---------------------------------------------------------
    # 8. Create appointment.
    #
    # A second line of defense sits below this INSERT: a
    # database-level EXCLUDE constraint on (doctor_id, time
    # range) for non-cancelled appointments (see
    # migrations/0003_prevent_overlapping_bookings.sql). The
    # advisory lock above should make it impossible for two
    # concurrent requests to both reach this INSERT for an
    # overlapping slot; the constraint is what guarantees that
    # even if some future code path ever bypassed the lock.
    # ---------------------------------------------------------
    try:
        cur.execute(
            """
            INSERT INTO appointments (
                doctor_id,
                patient_id,
                appointment_type_id,
                start_at,
                end_at,
                status
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                'PENDING'
            )
            RETURNING
                id,
                doctor_id,
                patient_id,
                appointment_type_id,
                start_at,
                end_at,
                status
            """,
            (
                doctor_id,
                patient_id,
                appointment_type_id,
                start_at,
                end_at,
            ),
        )
    except psycopg.errors.ExclusionViolation:
        logger.warning(
            f"Exclusion constraint rejected overlapping scheduling for "
            f"doctor_id={doctor_id} (advisory lock should normally "
            f"prevent reaching this point -- backstop triggered)"
        )
        raise SlotOverlap()

    row = cur.fetchone()

    return {
        "id": row[0],
        "doctor_id": row[1],
        "patient_id": row[2],
        "appointment_type_id": row[3],
        "start_at": row[4].isoformat(),
        "end_at": row[5].isoformat(),
        "status": row[6],
        "duration_minutes": duration_minutes,
        "appointment_type_name": appointment_type[1],
    }


def cancel_appointment_service(
    cur,
    appointment_id: int,
    *,
    requesting_patient_id: int | None = None,
):
    cur.execute(
        """
        SELECT id, patient_id, status
        FROM appointments
        WHERE id = %s
        FOR UPDATE
        """,
        (appointment_id,),
    )

    appointment = cur.fetchone()

    if appointment is None:
        raise AppointmentNotFound()

    if (
        requesting_patient_id is not None
        and appointment[1] != requesting_patient_id
    ):
        # Checked before the already-cancelled check below on purpose --
        # a non-owner shouldn't learn an appointment's cancellation state.
        raise NotAppointmentOwner()

    # Only a still-live appointment (awaiting confirmation, or
    # confirmed) can be cancelled -- one that's already Cancelled,
    # Rejected, Visited, or Completed is done, one way or another.
    # AlreadyCancelled is reused for all of these (not just the literal
    # CANCELLED case) -- every caller already treats it as "can't act on
    # this anymore" and surfaces the same 409, so a new exception per
    # terminal status wasn't worth the ripple through every call site.
    if appointment[2] not in ACTIONABLE_STATUSES:
        raise AlreadyCancelled()

    cur.execute(
        """
        UPDATE appointments
        SET
            status = 'CANCELLED',
            updated_at = NOW()
        WHERE id = %s
        RETURNING id, status
        """,
        (appointment_id,),
    )

    row = cur.fetchone()

    return {
        "id": row[0],
        "status": row[1],
    }


def reschedule_appointment_service(
    cur,
    appointment_id: int,
    *,
    patient_id: int,
    new_start_at,
    enforce_scheduling_window: bool = False,
):
    """
    Reschedule keeps the original appointment's doctor and appointment
    type -- only the date/time changes. This is a faithful move of
    app/api/scheduling.py's RESCHEDULE_FINAL_CONFIRM logic (verified against
    the running WhatsApp flow line-by-line while writing this, not
    written from memory of what reschedule "should" do), extracted here
    for WEB P4 so the web reschedule endpoint shares the exact same rules
    instead of a second, hand-written copy that could silently drift.
    scheduling.py's own reschedule handler is refactored to call this same
    function (see its own comment at the call site) -- so there is now
    exactly one implementation of these rules, not two kept in sync by
    hand.

    patient_id is required and baked directly into the ownership check
    (WHERE id = %s AND patient_id = %s), deliberately matching
    scheduling.py's own pattern: a wrong-owner lookup raises the same
    AppointmentNotFound as a genuinely nonexistent id, so neither this
    function's caller nor an attacker probing ids can distinguish "not
    yours" from "doesn't exist" (unlike cancel_appointment_service's
    optional requesting_patient_id, which predates any authenticated
    caller and had a pre-existing unauthenticated REST endpoint to stay
    compatible with -- reschedule has no such history, so it can be
    stricter from the start).

    One known omission, inherited unchanged from scheduling.py rather than
    silently "fixed": this does not re-check doctors.active. Neither does
    the original WhatsApp flow -- the doctor_id comes from the existing
    appointment being rescheduled, not a fresh lookup, in both places.
    Flagged in the WEB P4 report as a finding, not fixed here, per "do
    not create Web-specific business rules" (fixing it would make Web
    and WhatsApp reschedule diverge, the opposite of this phase's goal).
    """
    cur.execute(
        """
        SELECT id, doctor_id, patient_id, appointment_type_id, start_at, end_at, status
        FROM appointments
        WHERE id = %s
          AND patient_id = %s
        FOR UPDATE
        """,
        (appointment_id, patient_id),
    )

    original = cur.fetchone()

    if original is None:
        raise AppointmentNotFound()

    doctor_id = original[1]
    appointment_type_id = original[3]
    status = original[6]

    if status not in ACTIONABLE_STATUSES:
        raise AlreadyCancelled()

    appointment_type = get_appointment_type_for_doctor(cur, doctor_id, appointment_type_id)

    if appointment_type is None:
        raise AppointmentTypeNotAssigned()

    duration_minutes = appointment_type["duration_minutes"]

    new_start_at = new_start_at.replace(second=0, microsecond=0)

    if enforce_scheduling_window and not is_within_scheduling_window(new_start_at.date()):
        raise OutsideSchedulingWindow()

    new_end_at = new_start_at + timedelta(minutes=duration_minutes)

    # ---------------------------------------------------------
    # Re-check doctor's schedule for the new slot -- the same check
    # create_appointment_service's own step 4 makes (and, like that
    # step, unconditional: not gated behind enforce_scheduling_window,
    # since a doctor's working hours are a different rule from the
    # patient-facing calendar-month scheduling window; staff/admin
    # reschedule bypasses the latter but never the former). Previously
    # missing here entirely -- a known, documented gap (WEB P7 and WEB
    # P10 reports both flagged it) that let a reschedule land outside
    # every defined doctor_schedule row, something a fresh scheduling has
    # never been able to do.
    #
    # doctor_schedule.day_of_week/start_time/end_time are defined in
    # the doctor's own local time -- unlike create_appointment_service's
    # step 4, new_start_at here cannot be assumed to already carry the
    # doctor-local offset: app/api/scheduling.py's WhatsApp reschedule flow
    # passes session["selected_start_at"], read back from the
    # scheduling_sessions TIMESTAMPTZ column, which (like every TIMESTAMPTZ
    # read-back in this codebase -- see app/utils/timezone.py's module
    # docstring) comes back UTC-labeled: the correct instant, but the
    # wrong wall-clock digits for a day-of-week/time-of-day comparison.
    # Explicitly convert to the doctor's own timezone first, the same
    # fix pattern used throughout this project for this exact bug class
    # (e.g. list_patient_appointments_service just above).
    # ---------------------------------------------------------
    cur.execute("SELECT timezone FROM doctors WHERE id = %s", (doctor_id,))
    doctor_tz_row = cur.fetchone()
    doctor_tz = doctor_tz_row[0] if doctor_tz_row else None
    if not doctor_tz or not validate_timezone(doctor_tz):
        doctor_tz = "Asia/Kolkata"

    new_start_at_local = convert_to_timezone(new_start_at, doctor_tz)
    new_end_at_local = convert_to_timezone(new_end_at, doctor_tz)

    day_of_week = new_start_at_local.weekday() + 1
    new_time = new_start_at_local.time()
    new_end_time = new_end_at_local.time()

    cur.execute(
        """
        SELECT id
        FROM doctor_schedule
        WHERE doctor_id = %s
          AND day_of_week = %s
          AND active = TRUE
          AND start_time <= %s
          AND end_time >= %s
          AND (start_date IS NULL OR start_date <= %s)
          AND (end_date IS NULL OR end_date >= %s)
        LIMIT 1
        """,
        (
            doctor_id,
            day_of_week,
            new_time,
            new_end_time,
            new_start_at_local.date(),
            new_start_at_local.date(),
        ),
    )

    if cur.fetchone() is None:
        raise OutsideDoctorSchedule()

    # ---------------------------------------------------------
    # Re-check doctor blocks for the new slot.
    # ---------------------------------------------------------
    cur.execute(
        """
        SELECT start_at, end_at
        FROM doctor_blocks
        WHERE doctor_id = %s
          AND active = TRUE
          AND start_at < %s
          AND end_at > %s
        """,
        (doctor_id, new_end_at, new_start_at),
    )

    for block_start, block_end in cur.fetchall():
        if overlaps(new_start_at, new_end_at, block_start, block_end):
            raise DoctorBlockConflict()

    # ---------------------------------------------------------
    # Serialize against this doctor -- same primitive, same position
    # (after the block check, before the final overlap re-check) as
    # create_appointment_service and scheduling.py's own CONFIRM_SCHEDULING.
    # ---------------------------------------------------------
    cur.execute(
        "SELECT pg_advisory_xact_lock(%s::bigint)",
        (doctor_id,),
    )

    # ---------------------------------------------------------
    # Re-check existing appointments for the new slot, excluding the
    # appointment being rescheduled (it's about to be cancelled, but
    # hasn't been yet, and would otherwise "overlap with itself").
    # ---------------------------------------------------------
    cur.execute(
        """
        SELECT start_at, end_at
        FROM appointments
        WHERE doctor_id = %s
          AND start_at < %s
          AND end_at > %s
          -- status::text: see availability_engine.py's get_available_slots
          -- for why (enum-typed appointments.status on some databases).
          AND NOT (status::text = ANY(%s::text[]))
          AND id <> %s
        """,
        (doctor_id, new_end_at, new_start_at, list(RELEASED_STATUSES), appointment_id),
    )

    for existing_start, existing_end in cur.fetchall():
        if overlaps(new_start_at, new_end_at, existing_start, existing_end):
            raise SlotOverlap()

    # ---------------------------------------------------------
    # Cancel the old appointment, then create the new one, in the same
    # transaction. If the INSERT below fails (including via the
    # EXCLUDE-constraint backstop), the whole transaction rolls back on
    # the way out of this function -- undoing this UPDATE too, so a
    # failed reschedule always leaves the original appointment intact.
    # ---------------------------------------------------------
    cur.execute(
        """
        UPDATE appointments
        SET status = 'CANCELLED',
            updated_at = NOW()
        WHERE id = %s
          AND patient_id = %s
          -- status::text: see availability_engine.py's get_available_slots
          -- for why (enum-typed appointments.status on some databases).
          AND status::text = ANY(%s::text[])
        RETURNING id
        """,
        (appointment_id, patient_id, list(ACTIONABLE_STATUSES)),
    )

    if cur.fetchone() is None:
        # Lost a race between the FOR UPDATE read above and here --
        # shouldn't happen given the row lock, kept as a defensive
        # mirror of scheduling.py's own equivalent check.
        raise AlreadyCancelled()

    try:
        cur.execute(
            """
            INSERT INTO appointments (
                doctor_id,
                patient_id,
                appointment_type_id,
                start_at,
                end_at,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING
                id,
                doctor_id,
                patient_id,
                appointment_type_id,
                start_at,
                end_at,
                status
            """,
            # Rescheduling preserves the original appointment's status
            # (PENDING stays PENDING, awaiting the same confirmation it
            # always needed; CONFIRMED stays CONFIRMED) rather than
            # resetting it -- moving the time shouldn't force an already-
            # confirmed appointment back into a confirmation queue.
            (doctor_id, patient_id, appointment_type_id, new_start_at, new_end_at, status),
        )
    except psycopg.errors.ExclusionViolation:
        # Unlike create_appointment_service's equivalent catch, this one
        # must explicitly roll back before raising: a caught Postgres
        # error leaves the transaction aborted until a ROLLBACK is
        # issued, and reschedule's caller (scheduling.py's
        # RESCHEDULE_FINAL_CONFIRM handler) needs to keep using this
        # same cursor afterward (to look up fresh available dates and
        # update the session) -- it can't just let the exception
        # propagate all the way out the way appointments.py's REST
        # handler does. The rollback also undoes the "cancel old
        # appointment" UPDATE above, in the same statement: a failed
        # reschedule must leave the original appointment intact, not
        # lose it.
        cur.connection.rollback()
        logger.warning(
            f"Exclusion constraint rejected overlapping reschedule for "
            f"doctor_id={doctor_id} (advisory lock should normally "
            f"prevent reaching this point -- backstop triggered); "
            f"original appointment preserved via transaction rollback"
        )
        raise SlotOverlap()

    row = cur.fetchone()

    return {
        "id": row[0],
        "doctor_id": row[1],
        "patient_id": row[2],
        "appointment_type_id": row[3],
        "start_at": row[4].isoformat(),
        "end_at": row[5].isoformat(),
        "status": row[6],
        "duration_minutes": duration_minutes,
        "appointment_type_name": appointment_type["name"],
    }


def list_patient_appointments_service(cur, patient_id: int):
    """
    For WEB P4's "My Appointments" page: a patient's appointments split
    into upcoming / history / cancelled. The split uses exactly the
    status/start_at semantics app/api/scheduling.py's own
    get_upcoming_scheduled_appointments() already relies on for its
    "upcoming" filter (status IN ACTIONABLE_STATUSES AND start_at > now)
    -- no new business rule invented for the web. CANCELLED and REJECTED
    appointments both go to "cancelled" (a Rejected request never
    happened either, same as a Cancelled one, and the frontend has no
    separate bucket for it); a PENDING/CONFIRMED appointment whose time
    has already passed without being resolved, or one marked CHECKED_IN/
    COMPLETED/NO_SHOW, is "history".

    Same fix as get_upcoming_scheduled_appointments (see that function's
    docstring for the full story): start_at/end_at are converted to the
    doctor's own timezone before returning, since a value read from a
    stored TIMESTAMPTZ column always comes back UTC-normalized from
    psycopg otherwise, regardless of what offset it was inserted with.
    """
    cur.execute(
        """
        SELECT
            a.id,
            a.doctor_id,
            d.name,
            d.timezone,
            a.appointment_type_id,
            at.name,
            a.start_at,
            a.end_at,
            a.status,
            a.token_number
        FROM appointments a
        JOIN doctors d
            ON d.id = a.doctor_id
        JOIN appointment_types at
            ON at.id = a.appointment_type_id
        WHERE a.patient_id = %s
        """,
        (patient_id,),
    )

    now = datetime.now(timezone.utc)
    upcoming = []
    history = []
    cancelled = []

    for row in cur.fetchall():
        doctor_tz = row[3]

        if not validate_timezone(doctor_tz):
            doctor_tz = "Asia/Kolkata"

        entry = {
            "id": row[0],
            "doctor_id": row[1],
            "doctor_name": row[2],
            "appointment_type_id": row[4],
            "appointment_type_name": row[5],
            "start_at": convert_to_timezone(row[6], doctor_tz).isoformat(),
            "end_at": convert_to_timezone(row[7], doctor_tz).isoformat(),
            "status": row[8],
            "token_number": row[9],
        }

        if row[8] in RELEASED_STATUSES:
            cancelled.append((row[6], entry))
        elif row[8] in ACTIONABLE_STATUSES and row[6] > now:
            upcoming.append((row[6], entry))
        else:
            history.append((row[6], entry))

    upcoming.sort(key=lambda pair: pair[0])
    history.sort(key=lambda pair: pair[0], reverse=True)
    cancelled.sort(key=lambda pair: pair[0], reverse=True)

    return {
        "upcoming": [entry for _, entry in upcoming],
        "history": [entry for _, entry in history],
        "cancelled": [entry for _, entry in cancelled],
    }


def _transition_appointment_status(cur, appointment_id: int, *, from_statuses, to_status: str):
    """Shared body for the four staff-driven lifecycle transitions below:
    look up the current status under a row lock, reject if it isn't one
    of `from_statuses`, otherwise set it to `to_status`. All four
    transitions are single-column updates with no side effects on
    scheduling (unlike create/cancel/reschedule, nothing here touches
    the doctor's slot -- Confirming, Rejecting, Visiting, or Completing
    an appointment doesn't change whether it "occupies" its time range,
    per RELEASED_STATUSES/migrations/0011's own reasoning), so there's
    no advisory lock or overlap re-check needed here."""
    cur.execute(
        """
        SELECT status
        FROM appointments
        WHERE id = %s
        FOR UPDATE
        """,
        (appointment_id,),
    )

    row = cur.fetchone()

    if row is None:
        raise AppointmentNotFound()

    if row[0] not in from_statuses:
        raise InvalidStatusTransition()

    cur.execute(
        """
        UPDATE appointments
        SET status = %s,
            updated_at = NOW()
        WHERE id = %s
        RETURNING id, status
        """,
        (to_status, appointment_id),
    )

    result_row = cur.fetchone()

    return {"id": result_row[0], "status": result_row[1]}


def confirm_appointment_service(cur, appointment_id: int):
    """Staff approves a Pending request."""
    return _transition_appointment_status(
        cur, appointment_id, from_statuses=("PENDING",), to_status="CONFIRMED"
    )


def reject_appointment_service(cur, appointment_id: int):
    """Staff declines a Pending request -- releases its slot, same as a
    cancellation (see RELEASED_STATUSES above)."""
    return _transition_appointment_status(
        cur, appointment_id, from_statuses=("PENDING",), to_status="REJECTED"
    )


def mark_visited_service(cur, appointment_id: int):
    """
    Staff checks a Confirmed patient in on arrival -- this is also the
    moment they're issued a queue token number (migrations/0012_
    appointment_queue_tokens.sql), so this doesn't reuse
    _transition_appointment_status above the way the other three
    transitions do.

    Tokens are scoped per doctor, per doctor-local calendar day (a
    walk-in queue is a per-doctor, per-day thing -- see the "Token
    scope" product decision this implements), and assigned in
    check-in order: the next integer after the highest token_number
    already issued to this doctor today. Concurrent check-ins for the
    same doctor are serialized with pg_advisory_xact_lock, the same
    primitive create_appointment_service uses to serialize schedulings --
    a second lock key (the day's epoch-day number) scopes it to "this
    doctor, today" specifically, so it can't collide with that other
    lock's (doctor_id) key space or with a different day's queue.
    """
    cur.execute(
        """
        SELECT a.status, a.doctor_id, d.timezone, a.start_at
        FROM appointments a
        JOIN doctors d ON d.id = a.doctor_id
        WHERE a.id = %s
        FOR UPDATE OF a
        """,
        (appointment_id,),
    )

    row = cur.fetchone()

    if row is None:
        raise AppointmentNotFound()

    status, doctor_id, doctor_tz, start_at = row

    if status != "CONFIRMED":
        raise InvalidStatusTransition()

    # start_at is TIMESTAMPTZ -- an absolute instant, so comparing it
    # directly against an aware "now" is correct regardless of which
    # timezone either side happens to be labeled in (no conversion to
    # the doctor's local wall-clock time needed, or safe, for this
    # check -- unlike the token-numbering "which doctor-local day is
    # this" question below, which does need doctor_tz).
    if start_at > datetime.now(timezone.utc):
        raise AppointmentNotStarted()

    if not validate_timezone(doctor_tz):
        doctor_tz = "Asia/Kolkata"

    today = datetime.now(ZoneInfo(doctor_tz)).date()
    day_epoch = (today - date(1970, 1, 1)).days

    cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (doctor_id, day_epoch))

    cur.execute(
        """
        SELECT COALESCE(MAX(token_number), 0) + 1
        FROM appointments
        WHERE doctor_id = %s
          AND visited_at IS NOT NULL
          AND (visited_at AT TIME ZONE %s)::date = %s
        """,
        (doctor_id, doctor_tz, today),
    )
    (next_token,) = cur.fetchone()

    cur.execute(
        """
        UPDATE appointments
        SET status = 'CHECKED_IN',
            visited_at = NOW(),
            token_number = %s,
            updated_at = NOW()
        WHERE id = %s
        RETURNING id, status, token_number, visited_at, doctor_id, patient_id
        """,
        (next_token, appointment_id),
    )

    result_row = cur.fetchone()

    return {
        "id": result_row[0],
        "status": result_row[1],
        "token_number": result_row[2],
        "visited_at": result_row[3].isoformat(),
        "doctor_id": result_row[4],
        "patient_id": result_row[5],
    }


def mark_completed_service(cur, appointment_id: int):
    """Staff closes out a Checked-In appointment once the consultation
    is finished."""
    return _transition_appointment_status(
        cur, appointment_id, from_statuses=("CHECKED_IN",), to_status="COMPLETED"
    )


def mark_no_show_service(cur, appointment_id: int):
    """Staff marks a Confirmed appointment as a no-show -- the patient
    never arrived. Manual only (front-desk action), no automatic/cron
    trigger.

    Deliberately only reachable from CONFIRMED: not from CHECKED_IN (a
    checked-in patient is physically present, so "no-show" is a
    contradiction), and not from PENDING (an unapproved request that
    was never confirmed is a different, out-of-scope problem -- it
    just goes stale, it doesn't become a no-show).

    Also requires the appointment to have already started -- same
    AppointmentNotStarted guard mark_visited_service enforces for
    check-in, for the same reason: you can't yet know a patient won't
    show up for a slot that hasn't happened yet. Doesn't reuse
    _transition_appointment_status above (unlike confirm/reject/
    complete) because of this extra time check, the same reason
    mark_visited_service doesn't either."""
    cur.execute(
        """
        SELECT status, start_at
        FROM appointments
        WHERE id = %s
        FOR UPDATE
        """,
        (appointment_id,),
    )

    row = cur.fetchone()

    if row is None:
        raise AppointmentNotFound()

    status, start_at = row

    if status != "CONFIRMED":
        raise InvalidStatusTransition()

    if start_at > datetime.now(timezone.utc):
        raise AppointmentNotStarted()

    cur.execute(
        """
        UPDATE appointments
        SET status = 'NO_SHOW',
            updated_at = NOW()
        WHERE id = %s
        RETURNING id, status
        """,
        (appointment_id,),
    )

    result_row = cur.fetchone()

    return {"id": result_row[0], "status": result_row[1]}
