-- OPD/HIMS master spec Phase 13: Receipt (section 42) -- "Send to
-- patient" is a staff-initiated mock notification (app/services/
-- notifications.py's KIND_RECEIPT, same staff-initiated exception as
-- KIND_CHECK_IN/KIND_QUEUE_TOKEN), so mock_sms_outbox.kind's CHECK
-- constraint needs the new value, same drop/re-add pattern every prior
-- kind addition (migrations/0007/0012/0017/0021) already used.
ALTER TABLE mock_sms_outbox DROP CONSTRAINT mock_sms_outbox_kind_check;

ALTER TABLE mock_sms_outbox
    ADD CONSTRAINT mock_sms_outbox_kind_check
    CHECK (kind IN ('OTP', 'SCHEDULING_CONFIRMATION', 'CANCELLATION', 'RESCHEDULE', 'CHECK_IN', 'QUEUE_TOKEN', 'RECEIPT'));
