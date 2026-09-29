-- Phase 10B (Billing Ledger Coexistence, ADR-009 Option B): dual-write
-- schema. Ledger A (appointments.payment_status and its siblings)
-- remains the untouched operational gate -- this migration adds ONLY
-- what's needed for Ledger B (invoices/charges/payments,
-- migrations/0033) to carry a same-transaction mirror of every Ledger A
-- consultation-fee event, so reporting can read Ledger B exclusively
-- while Ledger A keeps driving check-in/queue-token/waiver-eligibility
-- exactly as before. See docs/architecture/BILLING_LEDGER_COEXISTENCE.md
-- for the full design and docs/decisions/ADR-009 for why this option
-- was chosen over folding Ledger A into Ledger B outright.
--
-- Two nullable correlation columns, one per ledger-2 table, following
-- the exact same "where did this row come from" pattern
-- source_order_id/source_dispense_id/source_package_id already use on
-- charges -- these are plumbing cross-references, not financial-truth
-- columns (appointments itself gets NO new column; Ledger A's schema
-- is completely unchanged by this phase, per its own scope):
--
-- charges.legacy_appointment_id: which appointment's Ledger A payment
--   event this CONSULTATION charge mirrors. Lets the mirror function
--   find-or-create idempotently (the partial unique index below) and
--   lets the reconciliation invariant join the two ledgers directly.
--
-- payments.legacy_appointment_id: which appointment's Ledger A event
--   this specific payment row mirrors. Needed because payments has no
--   charge_id of its own (a payment only ever references its invoice),
--   so a REFUNDED mirror -- which must update the ONE payment row that
--   mirrors this appointment's consultation fee, not just any payment
--   on an invoice that might also carry a paid lab charge -- has no
--   other way to find the right row unambiguously.
ALTER TABLE charges ADD COLUMN legacy_appointment_id BIGINT REFERENCES appointments(id);
ALTER TABLE payments ADD COLUMN legacy_appointment_id BIGINT REFERENCES appointments(id);

-- At most one ACTIVE CONSULTATION charge per legacy appointment -- the
-- same "prevent double-billing the same source" pattern
-- charges_source_order_unique/charges_source_dispense_unique already
-- enforce for LAB/RADIOLOGY/PHARMACY, applied to the legacy-mirror
-- source instead of an order/dispense id.
CREATE UNIQUE INDEX charges_one_consultation_per_legacy_appointment
    ON charges (legacy_appointment_id)
    WHERE source_type = 'CONSULTATION' AND status = 'ACTIVE';

-- WAIVED mirror representation, resolved (not left ambiguous): a real,
-- nonzero waived consultation fee (waive_consultation_fee_service --
-- always a genuine, configured fee being forgiven, never zero, per its
-- own eligibility rule) mirrors as a real ACTIVE charge plus a
-- COMPLETED payment for the same amount, so the invoice balance
-- correctly zeroes to 0 (nothing outstanding) without pretending cash
-- changed hands under an existing method. 'WAIVED' did not previously
-- exist as a legal payments.method value -- added here the same way
-- migrations/0050 added 'DECLINED' to payments.status for the
-- structurally identical problem (an existing enum-shaped CHECK
-- constraint needing one more legitimate value, not a new mechanism).
-- settle_free_visit_service's case (consultation_fee always exactly 0
-- by that function's own precondition) is NOT covered by this --
-- charges.amount/payments.amount both CHECK (amount > 0), so a
-- genuinely zero-cost visit still cannot be mirrored as any ledger-2
-- row at all, waived or otherwise; see app/services/billing_services.py
-- for where this case is explicitly skipped, not silently mis-mapped.
ALTER TABLE payments DROP CONSTRAINT payments_method_check;
ALTER TABLE payments
    ADD CONSTRAINT payments_method_check
    CHECK (method IN ('CASH', 'UPI', 'CARD', 'BANK_TRANSFER', 'INSURANCE', 'OTHER', 'WAIVED'));

-- RBAC: closes the same gap identified during the earlier (shelved,
-- PR #121) Option A pass -- POST /appointments/{id}/payment and POST
-- /appointments/{id}/bill/payments are the only two billing-mutating
-- endpoints gated by bare authentication instead of require_permission
-- (bill.add_charge/void/void_payment/refund_payment all already have
-- one). Independent of which ADR-009 option is chosen, so it's
-- included here on its own merits, not because Option B requires it.
-- Granted to every existing role, not narrowed to ADMIN/BILLING the
-- way the correction/reversal actions are: today literally any
-- authenticated staff account can reach these two endpoints
-- (migrations/0031_rbac_decomposition.sql's own docstring groups
-- "routine payment collection" with vitals/orders/prescriptions as
-- deliberately bare-auth), and "preserve current behavior" means
-- exactly that grant, not a narrower one a later phase might choose
-- deliberately.
INSERT INTO permissions (name) VALUES ('bill.record_payment');

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permissions p
WHERE p.name = 'bill.record_payment';
