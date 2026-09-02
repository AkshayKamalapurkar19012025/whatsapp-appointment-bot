-- Database-level backstop against double-booking: a second, independent
-- line of defense underneath the application-level pg_advisory_xact_lock
-- serialization used by both booking paths (app/api/booking.py and
-- app/api/appointments.py -- see their code comments at the lock/insert
-- sites). This does NOT replace the application-level lock: the lock is
-- what makes normal concurrent traffic behave correctly and cheaply;
-- this constraint is what makes an overlapping double-booking physically
-- impossible to persist even if some future code path -- a bulk import,
-- an admin script, a bug -- ever bypassed the lock.
--
-- Verified before writing this migration: both the dev and test
-- databases were queried for existing overlapping non-cancelled
-- appointments for the same doctor. Zero found in either. This
-- constraint would fail to apply (blocking this migration, not
-- silently skipping the check) if any existed.
--
-- Half-open interval semantics: tstzrange(start_at, end_at, '[)') means
-- start_at is inclusive, end_at is exclusive -- identical to the
-- overlap test already used everywhere in the app
-- (app/utils/timezone.py's overlaps(): "start_at < existing_end and
-- end_at > existing_start"). This is why two back-to-back appointments
-- (09:00-09:30 followed by 09:30-10:00) do NOT count as overlapping,
-- matching existing, already-tested application behaviour exactly.
--
-- WHERE (status <> 'CANCELLED') mirrors the predicate already used by
-- every overlap-checking query in the codebase (not "status = 'BOOKED'",
-- to stay byte-for-byte consistent with the application's own definition
-- of "counts as occupying the slot" in case a third status is ever
-- introduced without this constraint being revisited in lockstep).
--
-- btree_gist is required for the GiST index to support equality (=) on
-- an integer column (doctor_id) alongside range overlap (&&) on
-- tstzrange in the same exclusion constraint.

CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE appointments
    ADD CONSTRAINT no_overlapping_booked_appointments
    EXCLUDE USING gist (
        doctor_id WITH =,
        tstzrange(start_at, end_at, '[)') WITH &&
    )
    WHERE (status <> 'CANCELLED');
