-- Phase 10B (Billing Ledger Coexistence, ADR-009 Option B) historical
-- backfill: mirrors every Ledger A consultation-fee event that already
-- existed BEFORE migrations/0058 (and therefore never ran through
-- app/services/appointment_services.py's _write_consultation_payment_
-- status chokepoint / billing_services.py's
-- mirror_legacy_consultation_payment_service) into Ledger B, so the
-- reconciliation invariant (tests/test_billing_ledger_reconciliation_
-- gap.py) and the now-unified GET /dashboard/billing collections cover
-- pre-coexistence history too, not just events recorded after this
-- phase went live.
--
-- Idempotent by construction: every INSERT is guarded by a NOT EXISTS
-- against the same legacy_appointment_id, not ON CONFLICT DO NOTHING
-- (silently swallowing a real duplicate-insert attempt would hide a bug
-- rather than surface it). Running this migration a second time (e.g.
-- migrate.py re-run against a database that already has it recorded in
-- schema_migrations) is a no-op regardless; the NOT EXISTS guards are
-- what make re-running the SQL body itself, specifically, safe too --
-- verified by executing this file's three INSERTs twice in a row
-- against the test database and confirming 0 rows on the second pass
-- (see the Phase 10B final report).
--
-- WAIVED and UNPAID are excluded entirely, same as Phase 10A's own
-- backfill did (migrations/0059 on the shelved, unmerged Option A
-- branch) -- Ledger A's WAIVED write has never recorded a real forgiven
-- amount (payment_amount is hardcoded 0 by _write_consultation_payment_
-- status, historically and still), so there is nothing to backfill a
-- WAIVED mirror FROM, and waiving is a one-shot terminal action (no
-- future event on an existing appointment will ever supply the missing
-- amount either) -- this is a permanent, not a "caught up later", gap.
--
-- Also permanently excluded, rather than backfilled with an invented
-- value, for the same "nothing real to put there" reason:
--   * appointments.encounter_id IS NULL -- migrations/0028 made this
--     column nullable and never enforced NOT NULL (reschedule_
--     appointment_service still doesn't populate it as of this
--     migration). invoices.encounter_id is NOT NULL UNIQUE, so a row
--     with no encounter has nowhere in Ledger B to attach to.
--   * payment_recorded_by IS NULL -- charges.created_by/payments.
--     recorded_by are both NOT NULL REFERENCES staff(id); no real staff
--     attribution exists to backfill from.
--   * payment_method IS NULL on a PAID/FAILED row -- payments.method is
--     NOT NULL; appointments.payment_method's own CHECK
--     (migrations/0019) is a strict subset of payments.method's, so any
--     non-null value here is always valid there, but a genuinely NULL
--     one has nothing to map.
--   * a REFUNDED row whose refund_amount exceeds its payment_amount --
--     would violate payments' own CHECK (refunded_amount <= amount);
--     excluded here rather than letting it abort the whole migration.
--     Not expected to match any real row (the application never allows
--     recording a refund larger than the payment), but excluded
--     defensively rather than assumed impossible.
--
-- Each of these exclusions is independently auditable after the fact:
--   SELECT a.id, a.payment_status, a.encounter_id, a.payment_method,
--          a.payment_recorded_by, a.payment_amount, a.refund_amount
--   FROM appointments a
--   WHERE a.payment_status IN ('PAID', 'FAILED', 'REFUNDED')
--     AND a.payment_amount IS NOT NULL AND a.payment_amount > 0
--     AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.legacy_appointment_id = a.id)
-- lists every row Ledger A considers a real collected/attempted/
-- refunded amount that this migration did NOT end up mirroring, for
-- whichever of the reasons above applies to it.

-- Step 1: ensure an invoice exists for every backfillable appointment's
-- encounter. DISTINCT ON handles the (not expected, but not DB-enforced
-- either) case of two appointments sharing one encounter_id -- picking
-- the earliest appointment's staff as that invoice's created_by is as
-- good a choice as any single-valued column requires; the charges/
-- payments this migration inserts are what actually matter, and those
-- stay correctly one-per-appointment regardless of which appointment
-- "owns" the shared invoice header.
INSERT INTO invoices (encounter_id, created_by)
SELECT DISTINCT ON (a.encounter_id) a.encounter_id, a.payment_recorded_by
FROM appointments a
WHERE a.payment_status IN ('PAID', 'FAILED', 'REFUNDED')
  AND a.payment_amount IS NOT NULL AND a.payment_amount > 0
  AND a.payment_method IS NOT NULL
  AND a.payment_recorded_by IS NOT NULL
  AND a.encounter_id IS NOT NULL
  AND (a.payment_status != 'REFUNDED' OR COALESCE(a.refund_amount, a.payment_amount) <= a.payment_amount)
  AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.encounter_id = a.encounter_id)
ORDER BY a.encounter_id, a.id;

-- Step 2: one CONSULTATION charge per backfillable appointment.
INSERT INTO charges (invoice_id, description, amount, source_type, legacy_appointment_id, created_by)
SELECT i.id, 'Consultation fee (Phase 10B historical backfill)', a.payment_amount, 'CONSULTATION', a.id, a.payment_recorded_by
FROM appointments a
JOIN invoices i ON i.encounter_id = a.encounter_id
WHERE a.payment_status IN ('PAID', 'FAILED', 'REFUNDED')
  AND a.payment_amount IS NOT NULL AND a.payment_amount > 0
  AND a.payment_method IS NOT NULL
  AND a.payment_recorded_by IS NOT NULL
  AND a.encounter_id IS NOT NULL
  AND (a.payment_status != 'REFUNDED' OR COALESCE(a.refund_amount, a.payment_amount) <= a.payment_amount)
  AND NOT EXISTS (
      SELECT 1 FROM charges c
      WHERE c.legacy_appointment_id = a.id AND c.source_type = 'CONSULTATION' AND c.status = 'ACTIVE'
  );

-- Step 3: one payment per backfillable appointment, mirroring Ledger
-- A's own event exactly (PAID/FAILED -> COMPLETED/DECLINED; REFUNDED
-- carries the original payment forward as COMPLETED with its
-- refunded_amount/reason/by/at filled in, the same "one COMPLETED row,
-- refund applied on top" shape mirror_legacy_consultation_payment_
-- service produces for a live REFUNDED event -- see its own docstring).
INSERT INTO payments (
    invoice_id, amount, method, status, refunded_amount, refund_reason,
    refunded_by, refunded_at, legacy_appointment_id, recorded_by, recorded_at
)
SELECT
    c.invoice_id,
    a.payment_amount,
    a.payment_method,
    CASE a.payment_status WHEN 'FAILED' THEN 'DECLINED' ELSE 'COMPLETED' END,
    CASE WHEN a.payment_status = 'REFUNDED' THEN COALESCE(a.refund_amount, a.payment_amount) ELSE 0 END,
    CASE WHEN a.payment_status = 'REFUNDED' THEN a.refund_reason END,
    CASE WHEN a.payment_status = 'REFUNDED' THEN a.refunded_by END,
    CASE WHEN a.payment_status = 'REFUNDED' THEN a.refunded_at END,
    a.id,
    a.payment_recorded_by,
    COALESCE(a.payment_recorded_at, a.updated_at)
FROM appointments a
JOIN charges c ON c.legacy_appointment_id = a.id AND c.source_type = 'CONSULTATION' AND c.status = 'ACTIVE'
WHERE a.payment_status IN ('PAID', 'FAILED', 'REFUNDED')
  AND a.payment_amount IS NOT NULL AND a.payment_amount > 0
  AND a.payment_method IS NOT NULL
  AND a.payment_recorded_by IS NOT NULL
  AND a.encounter_id IS NOT NULL
  AND (a.payment_status != 'REFUNDED' OR COALESCE(a.refund_amount, a.payment_amount) <= a.payment_amount)
  AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.legacy_appointment_id = a.id);
