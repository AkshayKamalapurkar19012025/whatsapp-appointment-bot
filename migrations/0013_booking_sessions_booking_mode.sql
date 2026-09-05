-- Smallest possible schema change to support the WhatsApp Date-First
-- booking flow (Department -> Appointment Type -> Date -> Available
-- Doctors -> Doctor -> Slot -> Review -> Confirm) alongside the existing
-- Doctor-First flow, without altering booking_sessions' existing reset
-- architecture.
--
-- Every existing update_session()/create_or_update_session() call site
-- in app/api/booking.py is unaffected: booking_mode defaults to NULL
-- both at the column level and via update_session's new keyword
-- argument default, so every pre-existing call (which does not pass
-- booking_mode) continues to reset it to NULL exactly like every other
-- unlisted field already does today -- no existing call site needed to
-- change. It is set to 'DATE_FIRST' only by the new Date-First state
-- transitions in app/api/booking.py, and is what lets SELECT_SLOT's
-- "back" and CONFIRM_BOOKING's "change slot"/slot-taken fallback know
-- whether to return to SELECT_DATE (Doctor-First: pick another date for
-- the same, already-chosen doctor) or SELECT_AVAILABLE_DOCTOR_DATE_FIRST
-- (Date-First: pick a different doctor for the same date) -- the one
-- place a plain "same step name for both flows" design cannot work,
-- since by that point both flows share identical session shape
-- (department_id/doctor_id/appointment_type_id/selected_date all set)
-- and "which flow got me here" is otherwise unrecoverable more than one
-- step back.

ALTER TABLE booking_sessions
    ADD COLUMN booking_mode TEXT;

COMMENT ON COLUMN booking_sessions.booking_mode IS
    'NULL (default) = Doctor-First flow. ''DATE_FIRST'' = Date-First flow (set only while inside its state chain; cleared, like every other selection field, on any transition that does not explicitly carry it forward).';
