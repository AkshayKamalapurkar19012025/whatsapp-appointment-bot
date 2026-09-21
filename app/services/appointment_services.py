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
    AppointmentSlotPassed,
    QueueEntryNotInQueue,
    QueueEntryNotHeld,
    PriorityReasonRequired,
    PaymentStateConflict,
    WaiverNotEligible,
    FreeVisitNotEligible,
    RefundExceedsPayment,
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
    booking_source: str | None = None,
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
        SELECT id, timezone, hospital_id
        FROM doctors
        WHERE id = %s
          AND active = TRUE
        """,
        (doctor_id,),
    )

    doctor_row = cur.fetchone()

    if doctor_row is None:
        raise DoctorNotFound()

    doctor_timezone = doctor_row[1]
    doctor_hospital_id = doctor_row[2]

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
    #
    # doctor_schedule's day_of_week/start_time/end_time are the
    # doctor's own local wall-clock, so start_at must be converted
    # to the doctor's timezone before day_of_week()/time() are
    # pulled off it -- pulling them off whatever offset start_at
    # happens to carry is only safe by accident, when that offset
    # is already the doctor's own. A start_at read back from a
    # TIMESTAMPTZ column through psycopg carries the database
    # session's timezone instead (UTC in this app) regardless of
    # the offset it was written with -- see app/api/scheduling.py's
    # get_upcoming_scheduled_appointments docstring for a
    # previously shipped, confirmed-live bug of the identical shape
    # in a sibling code path.
    # ---------------------------------------------------------
    local_start_at = convert_to_timezone(start_at, doctor_timezone)
    local_end_at = convert_to_timezone(end_at, doctor_timezone)

    day_of_week = local_start_at.weekday() + 1
    start_time = local_start_at.time()
    end_time = local_end_at.time()

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
            local_start_at.date(),
            local_start_at.date(),
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
    # 8. Create the encounter (M3), then the appointment linked to it.
    #
    # One encounter per appointment, in the same transaction as the
    # appointment itself so the two can never diverge -- if the INSERT
    # below fails (including via the EXCLUDE-constraint backstop), this
    # encounter is rolled back along with it, same as everything else in
    # this transaction. started_at mirrors migrations/0025's own backfill
    # fallback: there is no arrived_at yet for a brand-new booking, so
    # start_at (the scheduled time) is the best available signal for
    # "when this visit is." status is always OPEN here -- none of the
    # terminal appointment statuses (COMPLETED/CANCELLED/REJECTED/
    # NO_SHOW) apply to a brand-new booking, which always starts PENDING.
    # ---------------------------------------------------------
    cur.execute(
        """
        INSERT INTO encounters (hospital_id, patient_id, doctor_id, encounter_type, status, started_at)
        VALUES (%s, %s, %s, 'OPD', 'OPEN', %s)
        RETURNING id
        """,
        (doctor_hospital_id, patient_id, doctor_id, start_at),
    )
    encounter_id = cur.fetchone()[0]

    # ---------------------------------------------------------
    # 9. Create appointment.
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
                status,
                booking_source,
                encounter_id
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                'PENDING',
                %s,
                %s
            )
            RETURNING
                id,
                doctor_id,
                patient_id,
                appointment_type_id,
                start_at,
                end_at,
                status,
                booking_source
            """,
            (
                doctor_id,
                patient_id,
                appointment_type_id,
                start_at,
                end_at,
                booking_source,
                encounter_id,
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
        "booking_source": row[7],
        "duration_minutes": duration_minutes,
        "appointment_type_name": appointment_type[1],
        "encounter_id": encounter_id,
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
            d.specialization,
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
            "doctor_specialization": row[4],
            "appointment_type_id": row[5],
            "appointment_type_name": row[6],
            "start_at": convert_to_timezone(row[7], doctor_tz).isoformat(),
            "end_at": convert_to_timezone(row[8], doctor_tz).isoformat(),
            "status": row[9],
            "token_number": row[10],
        }

        if row[9] in RELEASED_STATUSES:
            cancelled.append((row[7], entry))
        elif row[9] in ACTIONABLE_STATUSES and row[7] > now:
            upcoming.append((row[7], entry))
        else:
            history.append((row[7], entry))

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
    """Staff approves a Pending request -- refused once the requested
    slot's start_at has already gone by (AppointmentSlotPassed): a
    request nobody actioned before its time passed has nothing left to
    confirm the patient into. Doesn't reuse _transition_appointment_status
    above, since it also needs this time check (same shape as
    mark_visited_service's own AppointmentNotStarted check, mirrored)."""
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

    if status != "PENDING":
        raise InvalidStatusTransition()

    # start_at is TIMESTAMPTZ -- see mark_visited_service's identical
    # comment on why comparing it directly against an aware UTC "now" is
    # correct with no doctor-timezone conversion needed.
    if start_at <= datetime.now(timezone.utc):
        raise AppointmentSlotPassed()

    cur.execute(
        """
        UPDATE appointments
        SET status = %s,
            updated_at = NOW()
        WHERE id = %s
        RETURNING id, status
        """,
        ("CONFIRMED", appointment_id),
    )

    result_row = cur.fetchone()

    return {"id": result_row[0], "status": result_row[1]}


def reject_appointment_service(cur, appointment_id: int):
    """Staff declines a Pending request -- releases its slot, same as a
    cancellation (see RELEASED_STATUSES above)."""
    return _transition_appointment_status(
        cur, appointment_id, from_statuses=("PENDING",), to_status="REJECTED"
    )


def generate_queue_token_service(cur, appointment_id: int, *, doctor_id: int, doctor_tz: str):
    """
    Assigns the next queue token for this doctor's local day, or returns
    the appointment's existing token unchanged if one was already
    assigned -- calling this twice for the same appointment must never
    produce a second token or renumber anyone else (idempotency guard).

    Extracted out of mark_visited_service (patient-arrival-workflow
    Phase 1) so token issuance could eventually be triggered
    independently of check-in, once payment gating exists. As of
    Phase 4, it now is: record_payment_service's PAID outcome and
    waive_consultation_fee_service are this function's only callers --
    a token is only generated once the consultation charge is paid or
    waived, matching the core business rule (a patient shouldn't enter
    the queue before then). mark_visited_service no longer calls this
    at all.

    The returned dict's "newly_generated" flag distinguishes a fresh
    assignment from the idempotent-replay case (existing_token branch
    below) -- callers (the two above) use it to decide whether to send
    the one-time "you're in the queue, token X" notification, so a
    double-click/refresh doesn't re-notify the patient.

    Tokens are scoped per doctor, per doctor-local calendar day (a
    walk-in queue is a per-doctor, per-day thing -- see the "Token
    scope" product decision this implements), and assigned in
    check-in order: the next integer after the highest token_number
    already issued to this doctor today. Concurrent calls for the
    same doctor are serialized with pg_advisory_xact_lock, the same
    primitive create_appointment_service uses to serialize schedulings --
    a second lock key (the day's epoch-day number) scopes it to "this
    doctor, today" specifically, so it can't collide with that other
    lock's (doctor_id) key space or with a different day's queue.

    Requires the caller to already hold (or not need) a lock on the
    appointments row itself -- this function only locks what it needs
    (the doctor/day advisory lock) plus a row-level FOR UPDATE on the
    target appointment for its own existing-token check and UPDATE.
    """
    cur.execute(
        "SELECT token_number, visited_at FROM appointments WHERE id = %s FOR UPDATE",
        (appointment_id,),
    )

    existing_token, existing_visited_at = cur.fetchone()

    if existing_token is not None:
        return {
            "id": appointment_id,
            "token_number": existing_token,
            "visited_at": existing_visited_at.isoformat(),
            "newly_generated": False,
        }

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
        SET token_number = %s,
            updated_at = NOW()
        WHERE id = %s
        RETURNING token_number, visited_at
        """,
        (next_token, appointment_id),
    )

    token_number, visited_at = cur.fetchone()

    return {
        "id": appointment_id,
        "token_number": token_number,
        "visited_at": visited_at.isoformat(),
        "newly_generated": True,
    }


def mark_visited_service(cur, appointment_id: int):
    """
    Staff checks a Confirmed patient in on arrival -- CHECKED_IN,
    visited_at set. That's all this does now (Phase 4 of the patient
    arrival workflow): it deliberately does NOT call
    generate_queue_token_service any more. Phases 1-3 kept that call
    wired here as a transitional no-behavior-change step while nothing
    else could trigger it; now that payment/waiver exist
    (record_payment_service, waive_consultation_fee_service), a token
    is only issued once the consultation fee is paid or waived -- see
    those two functions, the workflow's actual queue-entry trigger --
    matching the core business rule (spec: "a patient should not enter
    the consultation queue until the consultation charge has been
    successfully paid, unless waived"). token_number stays NULL on
    CHECKED_IN until then.

    No longer gated on start_at: a Confirmed, physically-present
    patient can be checked in whenever the front desk actually
    processes them, regardless of their scheduled slot time (first-
    come-first-served, not slot-order) -- queue order is, and always
    was, check-in/payment order (generate_queue_token_service), not
    scheduled time. Earlier revisions raised AppointmentNotStarted
    here to keep an early arrival from jumping ahead of a patient whose
    slot had actually arrived; that rule was deliberately dropped, not
    an oversight -- see mark_no_show_service for the one remaining,
    unrelated use of an AppointmentNotStarted-style time guard (you
    can't mark someone a no-show before their slot has even happened).

    Doesn't reuse _transition_appointment_status above the way the
    other three transitions do, since it needs a wider RETURNING
    (visited_at/doctor_id/patient_id, not just id/status).
    """
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

    if row[0] != "CONFIRMED":
        raise InvalidStatusTransition()

    cur.execute(
        """
        UPDATE appointments
        SET status = 'CHECKED_IN',
            visited_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
        RETURNING id, status, visited_at, doctor_id, patient_id
        """,
        (appointment_id,),
    )

    result_row = cur.fetchone()

    return {
        "id": result_row[0],
        "status": result_row[1],
        "token_number": None,
        "visited_at": result_row[2].isoformat(),
        "doctor_id": result_row[3],
        "patient_id": result_row[4],
    }


def mark_arrived_service(cur, appointment_id: int):
    """
    Staff records that a Confirmed patient has physically arrived at the
    front desk -- independent of whether their scheduled start_at has
    passed yet. Deliberately NOT mark_visited_service: recording an
    arrival on its own still means only "physically present," never
    "formally checked in, at the front of the payment/queue gate."
    arrived_at here means only that -- an early arrival stays CONFIRMED
    with arrived_at set and visited_at still NULL, so it can never
    generate a queue token by itself (generate_queue_token_service is
    only ever reached from record_payment_service/waive_consultation_
    fee_service, both of which require status='CHECKED_IN' -- see
    _lock_appointment_for_payment). mark_visited_service no longer
    requires start_at <= now (see its own docstring), so front desk can
    check this patient in for real -- and into the queue -- as soon as
    they're ready, without waiting for their scheduled slot time.

    Idempotent: replaying this for an appointment that already has
    arrived_at set returns the existing value unchanged rather than
    overwriting it with a later timestamp (same idempotency shape as
    generate_queue_token_service above) -- a double-click at the front
    desk must not silently move a patient's recorded arrival time.
    """
    cur.execute(
        """
        SELECT status, arrived_at, start_at
        FROM appointments
        WHERE id = %s
        FOR UPDATE
        """,
        (appointment_id,),
    )

    row = cur.fetchone()

    if row is None:
        raise AppointmentNotFound()

    status, arrived_at, start_at = row

    if status != "CONFIRMED":
        raise InvalidStatusTransition()

    if arrived_at is not None:
        return {
            "id": appointment_id,
            "status": status,
            "arrived_at": arrived_at.isoformat(),
            "start_at": start_at.isoformat(),
            "newly_recorded": False,
        }

    cur.execute(
        """
        UPDATE appointments
        SET arrived_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
        RETURNING id, status, arrived_at, start_at
        """,
        (appointment_id,),
    )

    result_row = cur.fetchone()

    return {
        "id": result_row[0],
        "status": result_row[1],
        "arrived_at": result_row[2].isoformat(),
        "start_at": result_row[3].isoformat(),
        "newly_recorded": True,
    }


def confirm_and_check_in_service(cur, appointment_id: int):
    """
    The walk-in "Confirm & Check In" action: composes confirm_
    appointment_service and mark_visited_service above -- neither body
    duplicated or modified -- into the one action reception wants for a
    walk-in.

    Only reachable from PENDING or CONFIRMED. Always reaches CHECKED_IN
    (arrival_kind stays "checked_in" in the response -- kept as a field
    rather than removed so existing callers don't need a shape change --
    since mark_visited_service no longer has a start_at guard that could
    make this fall back to anything else; see that function's docstring
    for why). Never generates a queue token itself either way -- that
    still only happens via payment/waiver, same as every other path.
    """
    cur.execute(
        "SELECT status FROM appointments WHERE id = %s FOR UPDATE",
        (appointment_id,),
    )

    row = cur.fetchone()

    if row is None:
        raise AppointmentNotFound()

    status = row[0]

    if status not in ("PENDING", "CONFIRMED"):
        raise InvalidStatusTransition()

    if status == "PENDING":
        confirm_appointment_service(cur, appointment_id)

    visit_result = mark_visited_service(cur, appointment_id)
    return {
        "id": visit_result["id"],
        "status": visit_result["status"],
        "arrival_kind": "checked_in",
        "visited_at": visit_result["visited_at"],
        "arrived_at": None,
        "doctor_id": visit_result["doctor_id"],
        "patient_id": visit_result["patient_id"],
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


def get_consultation_charge_service(cur, appointment_id: int):
    """
    The fee to charge for this appointment -- always looked up
    server-side from doctor_appointment_types.consultation_fee (never
    trusts a client-supplied amount, since that's the one number in
    this whole workflow that must not be spoofable). Callable any time
    an appointment exists, independent of its current status, so
    Phase 5's UI can show "Consultation Fee: X" before check-in too.
    """
    cur.execute(
        """
        SELECT dat.consultation_fee
        FROM appointments a
        JOIN doctor_appointment_types dat
            ON dat.doctor_id = a.doctor_id
           AND dat.appointment_type_id = a.appointment_type_id
        WHERE a.id = %s
        """,
        (appointment_id,),
    )

    row = cur.fetchone()

    if row is None:
        # Either the appointment doesn't exist, or its doctor/type
        # pairing was deactivated after the appointment was created
        # (create_appointment_service required it to be active at
        # creation time, but doesn't prevent later deactivation).
        cur.execute("SELECT id FROM appointments WHERE id = %s", (appointment_id,))
        if cur.fetchone() is None:
            raise AppointmentNotFound()
        raise AppointmentTypeNotAssigned()

    return {"appointment_id": appointment_id, "consultation_fee": row[0]}


def _invoice_extra_charges_total(cur, appointment_id: int):
    """Sum of this appointment's invoice_line_items (migrations/0026)
    -- the ad-hoc charges added on top of its consultation_fee. Returns
    0, not NULL, when there are none, so callers can add it to
    consultation_fee unconditionally."""
    cur.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM invoice_line_items WHERE appointment_id = %s",
        (appointment_id,),
    )
    return cur.fetchone()[0]


def get_invoice_service(cur, appointment_id: int):
    """
    The itemized bill for this appointment: its consultation_fee, every
    ad-hoc line item added on top of it, and total_due -- exactly what
    record_payment_service charges (get_consultation_charge_service's
    return value plus _invoice_extra_charges_total). Same "callable any
    time, independent of appointment status" contract as get_
    consultation_charge_service, which this wraps.
    """
    charge = get_consultation_charge_service(cur, appointment_id)

    cur.execute(
        """
        SELECT id, description, amount, added_by, created_at
        FROM invoice_line_items
        WHERE appointment_id = %s
        ORDER BY created_at
        """,
        (appointment_id,),
    )
    line_items = [
        {
            "id": row[0],
            "description": row[1],
            "amount": row[2],
            "added_by": row[3],
            "created_at": row[4].isoformat(),
        }
        for row in cur.fetchall()
    ]

    cur.execute("SELECT invoice_number FROM appointments WHERE id = %s", (appointment_id,))
    (invoice_number,) = cur.fetchone()

    extra_charges_total = sum(item["amount"] for item in line_items)
    total_due = charge["consultation_fee"] + extra_charges_total

    return {
        "appointment_id": appointment_id,
        "invoice_number": invoice_number,
        "consultation_fee": charge["consultation_fee"],
        "line_items": line_items,
        "extra_charges_total": extra_charges_total,
        "total_due": total_due,
    }


def add_invoice_line_item_service(cur, appointment_id: int, *, description: str, amount, staff_id: int):
    """
    ADMIN adds an ad-hoc charge to an appointment's bill, on top of its
    consultation_fee (migrations/0026) -- e.g. a dressing charge or a
    minor procedure done during the same visit. ADMIN-gated at the API
    layer (app/api/appointments.py), the same authority level waive/
    refund require, since this changes how much money is owed.

    Only allowed while payment_status is UNPAID or FAILED -- once PAID/
    WAIVED/REFUNDED, the bill is frozen (payment_amount has already
    been computed and recorded against the total as it stood at that
    moment; adding a line item afterward would silently make payment_
    amount wrong). Raises PaymentStateConflict in every other state,
    the same exception record_payment_service/waive_consultation_fee_
    service use for "this payment_status doesn't allow that action".
    """
    cur.execute(
        "SELECT payment_status FROM appointments WHERE id = %s FOR UPDATE",
        (appointment_id,),
    )
    row = cur.fetchone()

    if row is None:
        raise AppointmentNotFound()

    (payment_status,) = row

    if payment_status not in ("UNPAID", "FAILED"):
        raise PaymentStateConflict()

    cur.execute(
        """
        INSERT INTO invoice_line_items (appointment_id, description, amount, added_by)
        VALUES (%s, %s, %s, %s)
        """,
        (appointment_id, description, amount, staff_id),
    )

    return get_invoice_service(cur, appointment_id)


def _lock_appointment_for_payment(cur, appointment_id: int):
    """Shared row lookup/lock for record_payment_service and
    waive_consultation_fee_service -- both gate on the same two things
    (appointment exists and is CHECKED_IN) before doing anything
    payment-specific, and both need doctor_tz afterward: record_payment_
    service and waive_consultation_fee_service each call
    generate_queue_token_service on success, which needs it for its own
    "which doctor-local day is this" token-numbering question."""
    cur.execute(
        """
        SELECT a.status, a.payment_status, a.doctor_id, a.patient_id,
               a.visited_at, d.timezone
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

    status, payment_status, doctor_id, patient_id, visited_at, doctor_tz = row

    if status != "CHECKED_IN":
        raise InvalidStatusTransition()

    if not validate_timezone(doctor_tz):
        doctor_tz = "Asia/Kolkata"

    return payment_status, doctor_id, patient_id, visited_at, doctor_tz


def record_payment_service(cur, appointment_id: int, *, method: str, outcome: str, staff_id: int):
    """
    Staff records a consultation-payment attempt at the front desk --
    method is CASH/UPI/CARD/OTHER, outcome is PAID or FAILED (a FAILED
    attempt, e.g. a declined card, can be retried by calling this again
    with a new outcome; nothing here talks to a real payment gateway,
    per the workflow spec's explicit scope boundary).

    Idempotent on an already-PAID appointment: returns the existing
    record unchanged rather than charging a second time -- guards
    against a double-click or a refresh-and-resubmit. Raises
    PaymentStateConflict for WAIVED/REFUNDED, since neither of those
    should ever be overwritten by a plain payment attempt.

    On a successful PAID outcome, also generates the queue token
    (generate_queue_token_service) -- this is the workflow's actual
    "patient enters the queue" trigger as of Phase 4, no longer
    check-in itself. A FAILED outcome never does: the patient stays
    outside the queue until payment succeeds or is waived, per the
    core business rule.

    amount charged is consultation_fee plus any ad-hoc invoice_line_
    items added for this appointment (migrations/0026, add_invoice_
    line_item_service) -- still never client-supplied, just a wider
    server-side total than the original single-fee model.
    """
    payment_status, doctor_id, patient_id, visited_at, doctor_tz = _lock_appointment_for_payment(cur, appointment_id)

    if payment_status == "PAID":
        return {**_current_payment_record(cur, appointment_id), "token_just_issued": False}

    if payment_status in ("WAIVED", "REFUNDED"):
        raise PaymentStateConflict()

    # Recorded for both outcomes -- FAILED still records the amount
    # that was *attempted* (e.g. "UPI declined for Rs. 500"), useful
    # for the front desk to see what's outstanding on retry. It does
    # not mean money changed hands; payment_status is what says that.
    charge = get_consultation_charge_service(cur, appointment_id)
    amount = charge["consultation_fee"] + _invoice_extra_charges_total(cur, appointment_id)

    cur.execute(
        """
        UPDATE appointments
        SET payment_status = %s,
            payment_method = %s,
            payment_amount = %s,
            payment_recorded_by = %s,
            payment_recorded_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
        """,
        (outcome, method, amount, staff_id, appointment_id),
    )

    token_just_issued = False
    if outcome == "PAID":
        token_result = generate_queue_token_service(cur, appointment_id, doctor_id=doctor_id, doctor_tz=doctor_tz)
        token_just_issued = token_result["newly_generated"]

    return {**_current_payment_record(cur, appointment_id), "token_just_issued": token_just_issued}


def waive_consultation_fee_service(cur, appointment_id: int, *, reason: str, staff_id: int):
    """
    Staff (ADMIN only -- enforced at the API layer, app/api/
    appointments.py) waives the consultation fee for this visit. Only
    eligible when the same patient has a COMPLETED visit with this same
    doctor within the 3 calendar days before this one's check-in --
    the clinic's stated policy ("waiver applies only if the patient
    revisits within 3 days"), not staff discretion. visited_at's
    doctor-local calendar date is compared on both sides (same pattern
    mark_visited_service/get_doctor_queue use for "which day is this"),
    not a raw 72-hour timestamp difference.

    Idempotent on an already-WAIVED appointment. Raises
    PaymentStateConflict if already PAID (a completed payment isn't
    something a waiver un-does -- that would be a refund, out of this
    phase's scope).

    On success, also generates the queue token (generate_queue_token_
    service) -- same Phase 4 trigger record_payment_service's PAID
    outcome uses: "payment complete or waived" is what admits a patient
    to the queue, not check-in itself.
    """
    payment_status, doctor_id, patient_id, visited_at, doctor_tz = _lock_appointment_for_payment(cur, appointment_id)

    if payment_status == "WAIVED":
        return {**_current_payment_record(cur, appointment_id), "token_just_issued": False}

    if payment_status in ("PAID", "REFUNDED"):
        raise PaymentStateConflict()

    this_visit_date = convert_to_timezone(visited_at, doctor_tz).date()

    cur.execute(
        """
        SELECT 1
        FROM appointments
        WHERE patient_id = %s
          AND doctor_id = %s
          AND status = 'COMPLETED'
          AND id <> %s
          AND visited_at IS NOT NULL
          AND (visited_at AT TIME ZONE %s)::date >= %s - INTERVAL '3 days'
          AND (visited_at AT TIME ZONE %s)::date <= %s
        LIMIT 1
        """,
        (patient_id, doctor_id, appointment_id, doctor_tz, this_visit_date, doctor_tz, this_visit_date),
    )

    if cur.fetchone() is None:
        raise WaiverNotEligible()

    cur.execute(
        """
        UPDATE appointments
        SET payment_status = 'WAIVED',
            payment_method = NULL,
            payment_amount = 0,
            waive_reason = %s,
            payment_recorded_by = %s,
            payment_recorded_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
        """,
        (reason, staff_id, appointment_id),
    )

    token_result = generate_queue_token_service(cur, appointment_id, doctor_id=doctor_id, doctor_tz=doctor_tz)

    return {**_current_payment_record(cur, appointment_id), "token_just_issued": token_result["newly_generated"]}


def settle_free_visit_service(cur, appointment_id: int):
    """
    System-settles a CHECKED_IN visit that has no consultation fee
    configured (consultation_fee = 0) -- the OPD front-desk flow's
    "Payment Required? No" branch (see the OPD Patient Search &
    Registration redesign report, point 9): a genuinely free visit
    (₹0 appointment type, a policy-exempt follow-up, etc.) shouldn't
    need staff to press a manual "waive" button and type a reason.

    Deliberately NOT the same thing as waive_consultation_fee_service,
    and never reuses its eligibility gate: that function's 3-day-
    revisit rule is a distinct clinic policy for waiving a REAL,
    nonzero charge, and requires ADMIN. This one has no discretion in
    it at all -- it only ever fires when there is nothing to collect
    in the first place (enforced below, not just assumed by the
    caller), so any authenticated staff member can trigger it, and it
    is not staff-attributed (payment_recorded_by stays NULL, matching
    "no one waived anything, there was nothing to waive").

    Kept entirely separate from mark_visited_service/record_payment_
    service/waive_consultation_fee_service -- none of their behavior
    changes, so every existing payment/queue-token test keeps testing
    exactly what it already tests.

    Idempotent on an already-WAIVED appointment, same replay guard as
    waive_consultation_fee_service. Raises PaymentStateConflict for
    PAID/REFUNDED (a settled real payment is never silently
    overwritten), and FreeVisitNotEligible if the configured fee turns
    out to be nonzero (e.g. a stale client retrying after the fee was
    reconfigured) -- callers must not use this as a way to skip a real
    charge. Also FreeVisitNotEligible if any ad-hoc invoice_line_items
    (migrations/0026) have been added for this appointment: a visit
    with a real extra charge on it isn't "free" just because the base
    consultation_fee happens to be 0.
    """
    payment_status, doctor_id, patient_id, visited_at, doctor_tz = _lock_appointment_for_payment(cur, appointment_id)

    if payment_status == "WAIVED":
        return {**_current_payment_record(cur, appointment_id), "token_just_issued": False}

    if payment_status in ("PAID", "REFUNDED"):
        raise PaymentStateConflict()

    charge = get_consultation_charge_service(cur, appointment_id)

    if charge["consultation_fee"] != 0 or _invoice_extra_charges_total(cur, appointment_id) != 0:
        raise FreeVisitNotEligible()

    cur.execute(
        """
        UPDATE appointments
        SET payment_status = 'WAIVED',
            payment_method = NULL,
            payment_amount = 0,
            waive_reason = 'No consultation fee configured for this visit',
            payment_recorded_by = NULL,
            payment_recorded_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
        """,
        (appointment_id,),
    )

    token_result = generate_queue_token_service(cur, appointment_id, doctor_id=doctor_id, doctor_tz=doctor_tz)

    return {**_current_payment_record(cur, appointment_id), "token_just_issued": token_result["newly_generated"]}


def record_refund_service(cur, appointment_id: int, *, amount, reason: str, staff_id: int):
    """
    ADMIN records a refund against a PAID appointment (see
    app/api/appointments.py's require_permission("appointment.refund_payment")
    on the endpoint -- the same authority level waive_consultation_fee_service requires,
    since a refund reverses real money the same way a waiver forgives
    it). Deliberately does not gate on appointments.status the way
    record_payment_service/waive_consultation_fee_service do: those
    exist to admit a patient to the queue, so they only make sense
    while a visit is CHECKED_IN. A refund is a back-office correction
    that can legitimately happen well after the visit is COMPLETED
    (a billing error found days later, a patient dispute), so the only
    precondition is payment_status = PAID.

    Only ever reachable from PAID -- not idempotent/replayable like
    record_payment_service or waive_consultation_fee_service, since a
    second refund against an already-REFUNDED appointment is never a
    harmless double-click; it's either a duplicate refund attempt (a
    real bug to surface, not silently swallow) or a second partial
    refund, which this simple model doesn't support. Both cases raise
    PaymentStateConflict, same as the other payment-state guards.

    amount must not exceed what was actually paid (payment_amount) --
    raises RefundExceedsPayment otherwise. A full refund is the common
    case (amount == payment_amount); a smaller amount is a deliberate
    partial refund, left to the caller's/UI's discretion.
    """
    cur.execute(
        """
        SELECT payment_status, payment_amount
        FROM appointments
        WHERE id = %s
        FOR UPDATE
        """,
        (appointment_id,),
    )

    row = cur.fetchone()

    if row is None:
        raise AppointmentNotFound()

    payment_status, payment_amount = row

    if payment_status != "PAID":
        raise PaymentStateConflict()

    if amount > payment_amount:
        raise RefundExceedsPayment()

    cur.execute(
        """
        UPDATE appointments
        SET payment_status = 'REFUNDED',
            refund_amount = %s,
            refund_reason = %s,
            refunded_by = %s,
            refunded_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
        """,
        (amount, reason, staff_id, appointment_id),
    )

    return _current_payment_record(cur, appointment_id)


def _current_payment_record(cur, appointment_id: int):
    cur.execute(
        """
        SELECT id, payment_status, payment_method, payment_amount,
               payment_recorded_at, waive_reason, token_number,
               refund_amount, refund_reason, refunded_at
        FROM appointments
        WHERE id = %s
        """,
        (appointment_id,),
    )

    row = cur.fetchone()

    return {
        "id": row[0],
        "payment_status": row[1],
        "payment_method": row[2],
        "payment_amount": row[3],
        "payment_recorded_at": row[4].isoformat() if row[4] else None,
        "waive_reason": row[5],
        "token_number": row[6],
        "refund_amount": row[7],
        "refund_reason": row[8],
        "refunded_at": row[9].isoformat() if row[9] else None,
    }


# ---------------------------------------------------------------------
# Queue hold/recall/priority (HIMS-style token queue) -- all three only
# operate on a ticketed (token_number IS NOT NULL) CHECKED_IN
# appointment: someone who isn't yet in today's live queue at all has
# nothing to hold, recall, or prioritize. None of the three ever touch
# token_number itself -- get_doctor_queue (app/api/doctors.py) is the
# one place serving order is computed (held entries excluded, priority
# entries called first), so a patient's token number never changes no
# matter how their place in line moves.
# ---------------------------------------------------------------------


def _locked_queue_entry(cur, appointment_id: int):
    """Shared row lock + "is this actually a live queue entry" guard for
    all three functions below."""
    cur.execute(
        "SELECT status, token_number FROM appointments WHERE id = %s FOR UPDATE",
        (appointment_id,),
    )

    row = cur.fetchone()

    if row is None:
        raise AppointmentNotFound()

    status, token_number = row

    if status != "CHECKED_IN" or token_number is None:
        raise QueueEntryNotInQueue()

    return token_number


def hold_queue_entry_service(cur, appointment_id: int):
    """Front desk skips a ticketed patient who's stepped away (restroom,
    forgotten document, a call) without losing their place in line --
    get_doctor_queue excludes a held entry from now_serving/waiting until
    recall_queue_entry_service below clears it. Idempotent: holding an
    already-held entry just refreshes queue_held_at, no error."""
    token_number = _locked_queue_entry(cur, appointment_id)

    cur.execute(
        """
        UPDATE appointments
        SET queue_held_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
        RETURNING id, status, queue_held_at
        """,
        (appointment_id,),
    )

    result_row = cur.fetchone()

    return {
        "id": result_row[0],
        "status": result_row[1],
        "token_number": token_number,
        "queue_held_at": result_row[2].isoformat(),
    }


def recall_queue_entry_service(cur, appointment_id: int):
    """Reverses hold_queue_entry_service -- the patient resumes their
    original spot in line (token_number is never renumbered), not the
    back of the queue. Raises QueueEntryNotHeld if this entry wasn't
    actually held (almost certainly a stale click against a queue view
    that's already moved on)."""
    token_number = _locked_queue_entry(cur, appointment_id)

    cur.execute("SELECT queue_held_at FROM appointments WHERE id = %s", (appointment_id,))
    (queue_held_at,) = cur.fetchone()

    if queue_held_at is None:
        raise QueueEntryNotHeld()

    cur.execute(
        """
        UPDATE appointments
        SET queue_held_at = NULL,
            updated_at = NOW()
        WHERE id = %s
        RETURNING id, status
        """,
        (appointment_id,),
    )

    result_row = cur.fetchone()

    return {"id": result_row[0], "status": result_row[1], "token_number": token_number}


def set_priority_service(cur, appointment_id: int, *, is_priority: bool, reason: str | None, staff_id: int):
    """Staff flags a ticketed patient's queue entry as priority (medical
    emergency, senior citizen, doctor's request, ...) -- get_doctor_queue
    calls priority entries to the front of the serving order, ahead of
    earlier token numbers, without ever renumbering anyone's actual
    token. Turning priority on requires a reason (PriorityReasonRequired):
    a queue-jump should be accountable, not silent. Turning it off
    leaves the last reason/who/when on the row as history rather than
    clearing it -- an audit trail that disappears the moment the flag
    does isn't an audit trail."""
    token_number = _locked_queue_entry(cur, appointment_id)

    if is_priority and not (reason and reason.strip()):
        raise PriorityReasonRequired()

    if is_priority:
        cur.execute(
            """
            UPDATE appointments
            SET is_priority = TRUE,
                priority_reason = %s,
                priority_set_by = %s,
                priority_set_at = NOW(),
                updated_at = NOW()
            WHERE id = %s
            RETURNING id, status, is_priority
            """,
            (reason.strip(), staff_id, appointment_id),
        )
    else:
        cur.execute(
            """
            UPDATE appointments
            SET is_priority = FALSE,
                updated_at = NOW()
            WHERE id = %s
            RETURNING id, status, is_priority
            """,
            (appointment_id,),
        )

    result_row = cur.fetchone()

    return {
        "id": result_row[0],
        "status": result_row[1],
        "token_number": token_number,
        "is_priority": result_row[2],
    }


def get_now_serving_token_service(cur, doctor_id: int, doctor_tz: str) -> int | None:
    """This doctor's current now-serving token number only -- no patient
    name, phone, or any other identifying detail. Shared by GET
    /doctors/{id}/queue (app/api/doctors.py, which needs the full picture
    for staff) and the public, unauthenticated queue-display endpoint
    (app/api/queue_display.py), which must never return anything
    patient-identifying. Same held/priority-aware ordering as that
    endpoint's own query, just trimmed to the one number a waiting-room
    display actually needs."""
    if not validate_timezone(doctor_tz):
        doctor_tz = "Asia/Kolkata"

    today = datetime.now(ZoneInfo(doctor_tz)).date()

    cur.execute(
        """
        SELECT token_number
        FROM appointments
        WHERE doctor_id = %s
          AND status = 'CHECKED_IN'
          AND token_number IS NOT NULL
          AND queue_held_at IS NULL
          AND (visited_at AT TIME ZONE %s)::date = %s
        ORDER BY is_priority DESC, token_number
        LIMIT 1
        """,
        (doctor_id, doctor_tz, today),
    )

    row = cur.fetchone()

    return row[0] if row else None
