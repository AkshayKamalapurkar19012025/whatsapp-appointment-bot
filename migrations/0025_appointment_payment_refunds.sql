-- Closes the one gap left in the OPD billing flow (migrations
-- 0018/0019): payment_status has allowed the value 'REFUNDED' since
-- 0018, but nothing has ever set it -- there is no way to record a
-- refund today. This adds the columns a refund needs (amount, reason,
-- who, when), the same shape 0019 used for payment_recorded_by/_at,
-- kept as a separate set of columns rather than reusing payment_
-- recorded_by/_at/_amount: a refund is a distinct event from the
-- original payment, and overwriting the original payment's columns
-- would destroy the record of what was actually paid (amount, method)
-- that the refund is against.

ALTER TABLE appointments
    ADD COLUMN refund_amount NUMERIC(10, 2)
        CHECK (refund_amount IS NULL OR refund_amount >= 0),
    ADD COLUMN refund_reason TEXT,
    ADD COLUMN refunded_by BIGINT REFERENCES staff(id),
    ADD COLUMN refunded_at TIMESTAMPTZ;

COMMENT ON COLUMN appointments.refund_amount IS
    'Amount actually refunded (frozen at the time payment_status was set to REFUNDED). NULL until a refund is recorded. May be less than payment_amount for a partial refund.';
COMMENT ON COLUMN appointments.refund_reason IS
    'Required staff-entered reason for the refund. NULL until recorded.';
COMMENT ON COLUMN appointments.refunded_by IS
    'Staff member (ADMIN) who recorded the refund.';
COMMENT ON COLUMN appointments.refunded_at IS
    'When the refund was recorded.';
