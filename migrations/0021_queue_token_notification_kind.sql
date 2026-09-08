-- Phase 4 of the patient arrival workflow decouples queue-token
-- issuance from check-in (mark_visited_service no longer generates a
-- token; record_payment_service's PAID outcome and
-- waive_consultation_fee_service now do). The check-in notification
-- (kind CHECK_IN) can therefore no longer reference a token number at
-- check-in time -- a new QUEUE_TOKEN kind fires instead, once payment
-- succeeds or is waived and a token actually exists.

ALTER TABLE mock_sms_outbox DROP CONSTRAINT mock_sms_outbox_kind_check;

ALTER TABLE mock_sms_outbox
    ADD CONSTRAINT mock_sms_outbox_kind_check
    CHECK (kind IN ('OTP', 'SCHEDULING_CONFIRMATION', 'CANCELLATION', 'RESCHEDULE', 'CHECK_IN', 'QUEUE_TOKEN'));
