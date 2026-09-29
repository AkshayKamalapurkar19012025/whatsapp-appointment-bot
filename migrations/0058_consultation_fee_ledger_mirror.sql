-- ADR-009 (docs/OPD_HIMS_ARCHITECTURE_AUDIT.md), Option B, Phase 1:
-- mirror appointments.payment_status events (the older "Ledger A"
-- consultation-fee mechanism, migrations/0018-0026) into the
-- invoices/charges/payments model (migrations/0033, "Ledger B") so
-- every reporting surface that already reads Ledger B -- GET
-- /dashboard/billing, Billing History, Payment History, the Exception
-- Engine's PAYMENT_PENDING check -- sees the consultation fee too.
--
-- This does NOT touch appointments.payment_status's own role as the
-- queue-token gate (app/services/appointment_services.py's
-- record_payment_service/waive_consultation_fee_service/
-- settle_free_visit_service/record_refund_service, and
-- generate_queue_token_service, are unchanged) -- see the ADR for why
-- that stays exactly as it is.
--
-- legacy_appointment_id is the idempotency key the dual-write (this
-- phase) and any future one-time historical backfill (a separate,
-- later script, not part of this migration) both key off:
--
--   * charges.legacy_appointment_id is UNIQUE (partial index below) --
--     at most one mirrored CONSULTATION charge per appointment, ever,
--     matching how a real visit has exactly one consultation fee.
--   * payments.legacy_appointment_id is NOT unique -- a FAILED-then-
--     retried-PAID sequence legitimately mirrors to two payment rows
--     (one DECLINED, one COMPLETED) sharing one appointment id, the
--     same way payments.status already supports a declined-then-
--     retried real Ledger B payment (migrations/0050).

ALTER TABLE charges ADD COLUMN legacy_appointment_id BIGINT REFERENCES appointments(id);
ALTER TABLE payments ADD COLUMN legacy_appointment_id BIGINT REFERENCES appointments(id);

CREATE UNIQUE INDEX charges_legacy_appointment_unique
    ON charges (legacy_appointment_id) WHERE legacy_appointment_id IS NOT NULL;
CREATE INDEX payments_legacy_appointment_id_idx
    ON payments (legacy_appointment_id) WHERE legacy_appointment_id IS NOT NULL;

-- Same "at most one source" invariant migrations/0033/0038 already
-- established for source_order_id/source_dispense_id/source_package_id
-- -- a mirrored consultation-fee charge is never also an order/
-- dispense/package charge.
ALTER TABLE charges DROP CONSTRAINT charges_source_exclusive_check;
ALTER TABLE charges ADD CONSTRAINT charges_source_exclusive_check CHECK (
    (source_order_id IS NOT NULL)::int
    + (source_dispense_id IS NOT NULL)::int
    + (source_package_id IS NOT NULL)::int
    + (legacy_appointment_id IS NOT NULL)::int <= 1
);

COMMENT ON COLUMN charges.legacy_appointment_id IS
    'Set only for a charge mirroring a consultation fee recorded through the older appointments.payment_status mechanism -- NULL for every other charge. See docs/OPD_HIMS_ARCHITECTURE_AUDIT.md ADR-009.';
COMMENT ON COLUMN payments.legacy_appointment_id IS
    'Set only for a payment mirroring a consultation-fee event recorded through appointments.payment_status (record_payment_service/waive_consultation_fee_service/record_refund_service) -- NULL for every other payment. Not unique: a FAILED-then-retried-PAID sequence mirrors to two rows sharing this value. See docs/OPD_HIMS_ARCHITECTURE_AUDIT.md ADR-009.';

-- Scope note (not enforced by this migration, recorded here for the
-- next reader): this phase mirrors PAID/FAILED/WAIVED/REFUNDED events
-- only -- a consultation fee that is still UNPAID at check-in has no
-- Ledger B charge yet, so the Exception Engine's PAYMENT_PENDING check
-- (app/services/exception_engine.py) still cannot flag an unpaid
-- consultation fee as outstanding. This is a named, tracked gap, not a
-- hidden one -- see the ADR's "one scope nuance worth being explicit
-- about" -- and is deliberately left for a later, separately-scoped
-- phase (creating the charge eagerly, unpaid, at check-in) rather than
-- folded into this one.
