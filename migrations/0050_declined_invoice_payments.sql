-- Phase 13 end-to-end validation surfaced a real gap: unlike the
-- original appointment-level consultation-fee flow (appointments.
-- payment_status already models UNPAID/PAID/FAILED/WAIVED/REFUNDED,
-- see migrations/0018), the newer itemized-billing `payments` table
-- (migrations/0033) only ever inserts COMPLETED rows -- a declined
-- card/UPI attempt at the actual invoice-payment endpoint left no row
-- at all, no audit trail of what was tried. This adds the missing
-- DECLINED status, mirroring the existing FAILED pattern: still
-- records the amount that was *attempted*, doesn't count toward the
-- invoice's paid total (app/services/billing_services.py's
-- paid_effective sum already filters to status = 'COMPLETED', so this
-- needs no change there), and can be retried by recording a new
-- payment.

ALTER TABLE payments DROP CONSTRAINT payments_status_check;
ALTER TABLE payments
    ADD CONSTRAINT payments_status_check
    CHECK (status IN ('COMPLETED', 'VOIDED', 'DECLINED'));
