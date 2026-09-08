-- Adds appointments.payment_status, laying groundwork for the patient
-- arrival -> registration -> payment -> queue workflow (Phase 1 of that
-- effort -- see the phase plan discussed for this branch). This
-- migration only adds the column and backfills existing rows -- nothing
-- in the application reads or writes it yet (that's Phase 3's payment-
-- recording work); mark_visited_service and get_doctor_queue are
-- unchanged by this migration.
--
-- Backfill: every existing CHECKED_IN/COMPLETED row predates payment
-- tracking entirely -- no money was ever collected or waived under a
-- documented reason, so neither PAID nor UNPAID is accurate. PAID would
-- misrepresent history as "we collected this"; UNPAID would incorrectly
-- flag these patients as owing money that was never actually charged.
-- WAIVED is the only honest value: "this visit happened before payment
-- was tracked," not "no payment was needed" or "payment outstanding."
-- PENDING/CONFIRMED/CANCELLED/REJECTED/NO_SHOW rows never reached
-- check-in, so they keep the column's default (UNPAID) -- consistent
-- with "payment isn't relevant until arrival."

ALTER TABLE appointments
    ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'UNPAID'
        CHECK (payment_status IN ('UNPAID', 'PAID', 'FAILED', 'WAIVED', 'REFUNDED'));

UPDATE appointments
SET payment_status = 'WAIVED'
WHERE status IN ('CHECKED_IN', 'COMPLETED');

COMMENT ON COLUMN appointments.payment_status IS
    'One of: UNPAID, PAID, FAILED, WAIVED, REFUNDED. Historical CHECKED_IN/COMPLETED rows predating payment tracking are backfilled WAIVED, never PAID or UNPAID.';
