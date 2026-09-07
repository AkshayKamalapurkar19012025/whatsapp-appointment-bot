-- Renames the mock_sms_outbox 'BOOKING_CONFIRMATION' notification kind to
-- 'SCHEDULING_CONFIRMATION', matching app/services/notifications.py's
-- KIND_BOOKING_CONFIRMATION -> KIND_SCHEDULING_CONFIRMATION rename (same
-- "Booking" -> "Scheduling" terminology change as migrations/0016). Existing
-- rows are updated in place so historical mock-outbox data still matches
-- the check constraint below, rather than being silently orphaned.

ALTER TABLE mock_sms_outbox DROP CONSTRAINT mock_sms_outbox_kind_check;

UPDATE mock_sms_outbox SET kind = 'SCHEDULING_CONFIRMATION' WHERE kind = 'BOOKING_CONFIRMATION';

ALTER TABLE mock_sms_outbox
    ADD CONSTRAINT mock_sms_outbox_kind_check
    CHECK (kind IN ('OTP', 'SCHEDULING_CONFIRMATION', 'CANCELLATION', 'RESCHEDULE', 'CHECK_IN'));
