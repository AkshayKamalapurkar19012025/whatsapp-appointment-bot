-- Patient queue tokens: when staff check a Confirmed patient in (mark
-- them Visited -- app/services/appointment_services.py's mark_visited_
-- service), the appointment is issued a token_number for that doctor's
-- queue on that calendar day, and visited_at records the exact instant
-- of check-in (used both to display "checked in at HH:MM" and, via its
-- doctor-local calendar date, to scope "today's queue" when computing
-- the next token number).
--
-- Scope is per doctor, per doctor-local day (a walk-in queue is a
-- per-doctor, per-day concept -- see mark_visited_service's own
-- docstring): token_number resets to 1 for a new day and is never
-- reused across doctors even for appointments checked in at the exact
-- same instant.

ALTER TABLE appointments
    ADD COLUMN visited_at TIMESTAMPTZ,
    ADD COLUMN token_number INTEGER;

COMMENT ON COLUMN appointments.visited_at IS
    'Set exactly once, when status transitions PENDING/CONFIRMED -> VISITED (check-in).';
COMMENT ON COLUMN appointments.token_number IS
    'Queue token for this doctor''s day, assigned at check-in (visited_at). NULL until checked in.';

-- Staff-triggered check-in notifications (the patient''s token number)
-- reuse the existing mock notification outbox (migrations/0007) as a
-- new kind, alongside booking/cancellation/reschedule.
ALTER TABLE mock_sms_outbox DROP CONSTRAINT mock_sms_outbox_kind_check;

ALTER TABLE mock_sms_outbox
    ADD CONSTRAINT mock_sms_outbox_kind_check
    CHECK (kind IN ('OTP', 'BOOKING_CONFIRMATION', 'CANCELLATION', 'RESCHEDULE', 'CHECK_IN'));
