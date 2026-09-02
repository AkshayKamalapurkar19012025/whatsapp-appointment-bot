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

from datetime import timedelta
import logging

import psycopg

from app.utils.timezone import overlaps
from app.services.availability_engine import is_within_booking_window
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
