"""
Shared appointment create/cancel logic.

create_appointment_service() and cancel_appointment_service() are a pure
move of the bodies of app/api/appointments.py's POST/DELETE handlers --
extracted so a future authenticated web endpoint can call the exact same
booking rules (schedule/block/overlap checks, the pg_advisory_xact_lock
serialization, the EXCLUDE-constraint backstop) instead of a third
reimplementation. app/api/appointments.py's router functions are now
thin wrappers: build the connection/cursor, call the service, translate
its typed exceptions (app/services/exceptions.py) into the exact same
HTTPException status codes and messages they raised before this move --
verified by the existing test suite (tests/test_concurrency.py,
tests/test_exclusion_constraint.py) passing unchanged.

Two parameters were added on top of the moved logic, both opt-in and
both defaulting to today's exact behavior:

- create_appointment_service(..., enforce_booking_window=False): when
  True, rejects a start_at outside the current-month+3-calendar-months
  web booking window (app/services/availability_engine.py). Defaults to
  False so the existing REST endpoint and every existing test keeps
  booking any future date, same as before this phase. A future web
  booking endpoint (WEB P3/P4) passes True.

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

from datetime import datetime, timedelta, timezone
import logging

import psycopg

from app.utils.timezone import overlaps, convert_to_timezone, validate_timezone
from app.services.availability_engine import (
    get_appointment_type_for_doctor,
    is_within_booking_window,
)
from app.services.exceptions import (
    DoctorNotFound,
    PatientNotFound,
    AppointmentTypeNotAssigned,
    OutsideDoctorSchedule,
    DoctorBlockConflict,
    SlotOverlap,
    OutsideBookingWindow,
    AppointmentNotFound,
    AlreadyCancelled,
    NotAppointmentOwner,
)

logger = logging.getLogger(__name__)


def create_appointment_service(
    cur,
    *,
    doctor_id: int,
    patient_id: int,
    appointment_type_id: int,
    start_at,
    enforce_booking_window: bool = False,
):
    # Normalize seconds/microseconds.
    start_at = start_at.replace(
        second=0,
        microsecond=0,
    )

    if enforce_booking_window and not is_within_booking_window(start_at.date()):
        raise OutsideBookingWindow()

    # ---------------------------------------------------------
    # 1. Check doctor.
    #
    # Deliberately a plain SELECT, not FOR UPDATE. It used to be
    # FOR UPDATE (the theory being that locking the doctors row
    # would serialize appointment creation for that doctor) --
    # that was removed while adding the pg_advisory_xact_lock
    # below, for two reasons: (a) it never actually serialized
    # against the WhatsApp path anyway, since booking.py doesn't
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
        LIMIT 1
        """,
        (
            doctor_id,
            day_of_week,
            start_time,
            end_time,
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
    # 6. Serialize booking attempts for this doctor.
    #
    # Standardized on the same primitive app/api/booking.py's
    # WhatsApp flow uses: pg_advisory_xact_lock(doctor_id). This
    # is a session/transaction-scoped Postgres advisory lock,
    # released automatically on commit or rollback -- it is NOT
    # the same lock as the "doctors ... FOR UPDATE" row lock
    # taken in step 1 above (that lock only blocks other
    # transactions that also do a FOR UPDATE read of the same
    # doctors row; it does not block a transaction that only
    # takes this advisory lock, or vice versa). Both booking
    # paths must take the *same* lock, on the *same* key
    # (doctor_id, cast to bigint for pg_advisory_xact_lock's
    # signature), or a WhatsApp booking and a direct REST booking
    # for the same doctor/slot can both pass their overlap check
    # and both insert -- this was confirmed to happen in testing
    # before this change (see docs/DATABASE_P1_NOTES.md item 4).
    #
    # Position matters: acquired after the doctor-block check
    # above (matching app/api/booking.py's CONFIRM_BOOKING flow
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
          AND status <> 'CANCELLED'
        ORDER BY start_at
        """,
        (
            doctor_id,
            end_at,
            start_at,
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
                'BOOKED'
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
            f"Exclusion constraint rejected overlapping booking for "
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

    if appointment[2] == "CANCELLED":
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
    enforce_booking_window: bool = False,
):
    """
    Reschedule keeps the original appointment's doctor and appointment
    type -- only the date/time changes. This is a faithful move of
    app/api/booking.py's RESCHEDULE_FINAL_CONFIRM logic (verified against
    the running WhatsApp flow line-by-line while writing this, not
    written from memory of what reschedule "should" do), extracted here
    for WEB P4 so the web reschedule endpoint shares the exact same rules
    instead of a second, hand-written copy that could silently drift.
    booking.py's own reschedule handler is refactored to call this same
    function (see its own comment at the call site) -- so there is now
    exactly one implementation of these rules, not two kept in sync by
    hand.

    patient_id is required and baked directly into the ownership check
    (WHERE id = %s AND patient_id = %s), deliberately matching
    booking.py's own pattern: a wrong-owner lookup raises the same
    AppointmentNotFound as a genuinely nonexistent id, so neither this
    function's caller nor an attacker probing ids can distinguish "not
    yours" from "doesn't exist" (unlike cancel_appointment_service's
    optional requesting_patient_id, which predates any authenticated
    caller and had a pre-existing unauthenticated REST endpoint to stay
    compatible with -- reschedule has no such history, so it can be
    stricter from the start).

    One known omission, inherited unchanged from booking.py rather than
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

    if status != "BOOKED":
        raise AlreadyCancelled()

    appointment_type = get_appointment_type_for_doctor(cur, doctor_id, appointment_type_id)

    if appointment_type is None:
        raise AppointmentTypeNotAssigned()

    duration_minutes = appointment_type["duration_minutes"]

    new_start_at = new_start_at.replace(second=0, microsecond=0)

    if enforce_booking_window and not is_within_booking_window(new_start_at.date()):
        raise OutsideBookingWindow()

    new_end_at = new_start_at + timedelta(minutes=duration_minutes)

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
    # create_appointment_service and booking.py's own CONFIRM_BOOKING.
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
          AND status <> 'CANCELLED'
          AND id <> %s
        """,
        (doctor_id, new_end_at, new_start_at, appointment_id),
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
          AND status = 'BOOKED'
        RETURNING id
        """,
        (appointment_id, patient_id),
    )

    if cur.fetchone() is None:
        # Lost a race between the FOR UPDATE read above and here --
        # shouldn't happen given the row lock, kept as a defensive
        # mirror of booking.py's own equivalent check.
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
            VALUES (%s, %s, %s, %s, %s, 'BOOKED')
            RETURNING
                id,
                doctor_id,
                patient_id,
                appointment_type_id,
                start_at,
                end_at,
                status
            """,
            (doctor_id, patient_id, appointment_type_id, new_start_at, new_end_at),
        )
    except psycopg.errors.ExclusionViolation:
        # Unlike create_appointment_service's equivalent catch, this one
        # must explicitly roll back before raising: a caught Postgres
        # error leaves the transaction aborted until a ROLLBACK is
        # issued, and reschedule's caller (booking.py's
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
    status/start_at semantics app/api/booking.py's own
    get_upcoming_booked_appointments() already relies on for its
    "upcoming" filter (status = 'BOOKED' AND start_at > now) -- no new
    business rule invented for the web. CANCELLED appointments go to
    "cancelled" regardless of their date; a BOOKED appointment in the
    past (no separate COMPLETED status exists anywhere in this schema)
    is "history".

    Same fix as get_upcoming_booked_appointments (see that function's
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
            a.status
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
        }

        if row[8] == "CANCELLED":
            cancelled.append((row[6], entry))
        elif row[6] > now:
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
