-- Phase 10 (Billing Ledger Unification): historical data backfill.
--
-- Scope decision, matching what record_payment_service/record_refund_
-- service actually do going forward (see app/services/appointment_
-- services.py and app/services/billing_services.py after this phase):
--
-- payment_status = PAID     -> invoice + CONSULTATION charge + a
--                               COMPLETED payment.
-- payment_status = FAILED   -> invoice + CONSULTATION charge + a
--                               DECLINED payment (recorded, doesn't
--                               count toward the invoice's paid total
--                               -- exactly mirrors what a fresh FAILED
--                               attempt does today).
-- payment_status = REFUNDED -> invoice + CONSULTATION charge + a
--                               COMPLETED payment carrying the ORIGINAL
--                               payment_amount, with refunded_amount/
--                               refund_reason/refunded_by/refunded_at
--                               copied across -- it WAS a real payment
--                               before being refunded, so it's recorded
--                               as one, the refund applied on top, the
--                               same shape refund_invoice_payment_
--                               service produces for a fresh refund.
-- payment_status = WAIVED   -> NOTHING. A waiver was never money
--                               changing hands, and charges.amount has
--                               a CHECK (amount > 0) that structurally
--                               cannot represent a zero-cost line item
--                               -- "waived" stays represented purely by
--                               the legacy appointments.waive_reason/
--                               payment_recorded_by/payment_recorded_at
--                               columns, which this migration leaves
--                               completely untouched and which stay the
--                               permanent, authoritative record for
--                               waivers going forward too (see
--                               app/services/appointment_services.py's
--                               waive_consultation_fee_service, which
--                               this phase does not change).
-- payment_status = UNPAID   -> NOTHING. Nothing was ever charged or
--                               collected in the old model either;
--                               creating an invoice for a visit that
--                               was simply never paid (often because it
--                               was long ago abandoned/no-show-adjacent)
--                               would add empty invoices with no
--                               financial event behind them.
--
-- Every INSERT below is guarded by a NOT EXISTS check against
-- appointments.consultation_payment_id and, for the invoice/charge
-- steps, against an existing row for that encounter/invoice -- so this
-- file is safe to reason about even though migrations only ever run
-- once (scripts/migrate.py never re-applies an applied file); it
-- mirrors the defensive style migrations/0052's own backfill INSERT
-- uses (ON CONFLICT DO NOTHING) rather than assuming a pristine table.
--
-- created_by/recorded_by on every new invoices/charges/payments row
-- uses appointments.payment_recorded_by, which record_payment_service
-- always sets (a required, non-optional parameter) for every PAID/
-- FAILED outcome -- REFUNDED rows were PAID first, so the same
-- guarantee applies. A row where that's somehow NULL is skipped (left
-- for the reconciliation report to surface as "unmatched" rather than
-- guessing a staff attribution) -- see the reconciliation script run
-- alongside this migration for the exact count, expected to be zero.

-- Step 1: one invoice per encounter that needs one and doesn't have one yet.
INSERT INTO invoices (encounter_id, created_by)
SELECT a.encounter_id, a.payment_recorded_by
FROM appointments a
WHERE a.payment_status IN ('PAID', 'FAILED', 'REFUNDED')
  AND a.consultation_payment_id IS NULL
  AND a.payment_recorded_by IS NOT NULL
  AND a.encounter_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.encounter_id = a.encounter_id)
ON CONFLICT (encounter_id) DO NOTHING;

-- Step 2: one ACTIVE CONSULTATION charge per invoice that needs one and
-- doesn't have one yet (an invoice can already exist from a pre-Phase-10
-- lab/pharmacy charge on the same encounter -- reused, not duplicated).
INSERT INTO charges (invoice_id, description, amount, source_type, created_by)
SELECT i.id, 'Consultation fee (migrated from legacy ledger)', a.payment_amount, 'CONSULTATION', a.payment_recorded_by
FROM appointments a
JOIN invoices i ON i.encounter_id = a.encounter_id
WHERE a.payment_status IN ('PAID', 'FAILED', 'REFUNDED')
  AND a.consultation_payment_id IS NULL
  AND a.payment_recorded_by IS NOT NULL
  AND a.payment_amount IS NOT NULL
  AND a.payment_amount > 0
  AND NOT EXISTS (
      SELECT 1 FROM charges c
      WHERE c.invoice_id = i.id AND c.source_type = 'CONSULTATION' AND c.status = 'ACTIVE'
  );

-- Step 3: the payment itself -- COMPLETED for PAID/REFUNDED, DECLINED
-- for FAILED, refund fields copied across for REFUNDED.
INSERT INTO payments (
    invoice_id, amount, method, status,
    refunded_amount, refund_reason, refunded_by, refunded_at,
    recorded_by, recorded_at
)
SELECT
    i.id,
    a.payment_amount,
    COALESCE(a.payment_method, 'OTHER'),
    CASE WHEN a.payment_status = 'FAILED' THEN 'DECLINED' ELSE 'COMPLETED' END,
    CASE WHEN a.payment_status = 'REFUNDED' THEN COALESCE(a.refund_amount, 0) ELSE 0 END,
    CASE WHEN a.payment_status = 'REFUNDED' THEN a.refund_reason ELSE NULL END,
    CASE WHEN a.payment_status = 'REFUNDED' THEN a.refunded_by ELSE NULL END,
    CASE WHEN a.payment_status = 'REFUNDED' THEN a.refunded_at ELSE NULL END,
    a.payment_recorded_by,
    a.payment_recorded_at
FROM appointments a
JOIN invoices i ON i.encounter_id = a.encounter_id
JOIN charges c ON c.invoice_id = i.id AND c.source_type = 'CONSULTATION' AND c.status = 'ACTIVE'
WHERE a.payment_status IN ('PAID', 'FAILED', 'REFUNDED')
  AND a.consultation_payment_id IS NULL
  AND a.payment_recorded_by IS NOT NULL
  AND a.payment_amount IS NOT NULL
  AND a.payment_amount > 0
  AND NOT EXISTS (
      -- one migrated payment per appointment: if this encounter's
      -- invoice already carries a payment recorded at exactly this
      -- appointment's payment_recorded_at for this amount, this row
      -- was already migrated (defensive re-run guard, see file header).
      SELECT 1 FROM payments p
      WHERE p.invoice_id = i.id
        AND p.recorded_at = a.payment_recorded_at
        AND p.amount = a.payment_amount
  );

-- Step 4: point each migrated appointment at the payment row that now
-- represents its consultation fee, so record_payment_service's
-- idempotency check and record_refund_service's ledger-2 refund path
-- both find it going forward.
UPDATE appointments a
SET consultation_payment_id = p.id
FROM invoices i
JOIN payments p ON p.invoice_id = i.id
WHERE i.encounter_id = a.encounter_id
  AND a.payment_status IN ('PAID', 'FAILED', 'REFUNDED')
  AND a.consultation_payment_id IS NULL
  AND p.recorded_at = a.payment_recorded_at
  AND p.amount = a.payment_amount;
