-- OPD/HIMS master spec Phase 7 (Diagnostics), resumed per the source-
-- of-truth audit's own finding: `orders.status` CHECK already listed
-- IN_PROGRESS "so Phase 7's real lab/radiology workflow can use them"
-- (migrations/0030's header) but no code path ever set it. This
-- migration is that resumed phase: a real sample-collection ->
-- processing -> result-entry -> verification -> release lifecycle for
-- LAB/RADIOLOGY orders specifically, layered onto the existing generic
-- order spine rather than replacing it.
--
-- Scope discipline, matching this table's own existing precedent
-- (migrations/0030/0031's headers): PROCEDURE/SERVICE/EXTERNAL_REFERRAL
-- orders are completely unaffected -- they keep the exact one-step
-- "record a result -> COMPLETED" behavior that exists today. Only
-- LAB/RADIOLOGY gain the extra states, enforced by a CHECK tying the
-- new status values to those two order_types (mirrors orders_check's
-- own existing order_type-conditional CHECK for external_destination).
--
-- New status values reuse IN_PROGRESS as-is (per the audit's own
-- instruction to "implement the actual transition" for the value
-- that already exists) and add exactly three new ones:
--   COLLECTED       -- LAB: sample collected. RADIOLOGY: study
--                      performed/patient scanned (one shared value,
--                      not two type-specific names -- master spec
--                      Principle 3's "one generic model, discriminated
--                      by type" applied the same way orders.order_type
--                      and order_results.parameter already are).
--   RESULT_ENTERED  -- a result/report has been drafted, not yet
--                      countersigned.
--   VERIFIED        -- countersigned by someone other than the
--                      enterer (enforced in app/services/
--                      order_services.py, not here -- same "business
--                      rules live in the service layer" convention
--                      migrations/0035's priority_reason comment
--                      already established). Release (-> COMPLETED)
--                      is the last, separate step.
--
-- Deliberately no CHECK tying result_entered_at/verified_at/
-- released_at to a specific status value the way completed_at/
-- cancelled_at already are (orders_check1/orders_check2): a rejected
-- sample can send an order back to ORDERED for recollection while
-- result_entered_at from a *different*, earlier attempt could still be
-- set from history -- a strict boolean-equality CHECK across every
-- one of these columns and every backward transition would be more
-- fragile than useful. The three service-layer functions that set
-- them are the actual source of truth for when each is populated.

ALTER TABLE orders DROP CONSTRAINT orders_status_check;

ALTER TABLE orders ADD CONSTRAINT orders_status_check
    CHECK (status IN ('ORDERED', 'COLLECTED', 'IN_PROGRESS', 'RESULT_ENTERED', 'VERIFIED', 'COMPLETED', 'CANCELLED'));

ALTER TABLE orders ADD CONSTRAINT orders_diagnostic_status_requires_lab_radiology
    CHECK (status NOT IN ('COLLECTED', 'RESULT_ENTERED', 'VERIFIED') OR order_type IN ('LAB', 'RADIOLOGY'));

COMMENT ON COLUMN orders.status IS
    'One of: ORDERED, COLLECTED, IN_PROGRESS, RESULT_ENTERED, VERIFIED, COMPLETED, CANCELLED. COLLECTED/RESULT_ENTERED/VERIFIED only ever apply to LAB/RADIOLOGY (see orders_diagnostic_status_requires_lab_radiology) -- every other order_type still goes ORDERED -> COMPLETED/CANCELLED exactly as before this migration.';

-- The worklist's own "open work" partial index (migrations/0030) named
-- exactly the two values this migration now actually uses in
-- sequence -- widen it to the full pre-release set so the worklist's
-- default (pending) filter stays index-backed instead of falling back
-- to a sequential scan once RESULT_ENTERED/VERIFIED/COLLECTED rows
-- exist.
DROP INDEX orders_open_by_type_idx;

CREATE INDEX orders_open_by_type_idx ON orders (order_type, ordered_at)
    WHERE status IN ('ORDERED', 'COLLECTED', 'IN_PROGRESS', 'RESULT_ENTERED', 'VERIFIED');

-- Three new audit columns -- who drafted the result, who verified it,
-- who released it, and when. Mirrors the existing cancelled_by/
-- cancelled_at and created_by/created_at attribution pattern already
-- used throughout this schema (consultations, orders itself, charges).
ALTER TABLE orders
    ADD COLUMN result_entered_by BIGINT REFERENCES staff(id),
    ADD COLUMN result_entered_at TIMESTAMPTZ,
    ADD COLUMN verified_by       BIGINT REFERENCES staff(id),
    ADD COLUMN verified_at       TIMESTAMPTZ,
    ADD COLUMN released_by       BIGINT REFERENCES staff(id),
    ADD COLUMN released_at       TIMESTAMPTZ;

COMMENT ON COLUMN orders.verified_by IS
    'Set by verify_order_result_service (app/services/order_services.py). Enforced there to differ from result_entered_by unless the verifying staff account is ADMIN -- there is no distinct pathologist/senior-lab-tech role in this app (confirmed by the source-of-truth audit), so a same-person block on every non-ADMIN account is the real safeguard this phase can honestly enforce, not a role split this app has no basis for.';

-- Sample -- a distinct physical-specimen entity, not collapsed into
-- orders (master spec's own Order/Sample/Result/Report distinction).
-- One row per collection ATTEMPT: a rejected sample is never deleted
-- or overwritten, only marked REJECTED with a reason, and a fresh row
-- records the recollection -- same "never silently overwrite, append
-- a new row instead" ethos as patient_allergies (migrations/0042) and
-- consultation_amendments (migrations/0041).
--
-- Scope decisions, explicit per this phase's own instruction to
-- document rather than invent unsupported rules:
--   - One order can have more than one lab_samples row (recollection
--     history), but this phase does not support several *different*
--     tests sharing one physical sample, or one order requiring
--     several *simultaneous* samples -- both real laboratory
--     possibilities, neither built here. A future phase can add a
--     join table if real usage calls for it.
--   - Collection is not hard-required before result entry (see
--     record_order_result_service's own comment) -- same "optional,
--     not faked" stance pharmacy_stock already takes on dispensing
--     without a matching batch (migrations/0032).
CREATE TABLE lab_samples (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id        BIGINT NOT NULL REFERENCES orders(id),
    sample_code     TEXT GENERATED ALWAYS AS ('LAB-' || LPAD(id::text, 6, '0')) STORED,
    sample_type     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'COLLECTED' CHECK (status IN ('COLLECTED', 'REJECTED')),
    notes           TEXT,
    collected_by    BIGINT NOT NULL REFERENCES staff(id),
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rejected_by     BIGINT REFERENCES staff(id),
    rejected_reason TEXT,
    rejected_at     TIMESTAMPTZ,
    CHECK ((status = 'REJECTED') = (rejected_at IS NOT NULL))
);

CREATE INDEX lab_samples_order_id_idx ON lab_samples (order_id, collected_at DESC);

CREATE UNIQUE INDEX lab_samples_sample_code_key ON lab_samples (sample_code);

COMMENT ON TABLE lab_samples IS
    'One row per specimen-collection attempt for a LAB order (migrations/0030). A REJECTED row is never deleted -- see app/services/order_services.py''s reject_sample_service/record_sample_collection_service for the recollection flow.';

-- RBAC: three new permissions for the three new controlled actions
-- (collection/rejection/start-processing share one permission --
-- both are the technician's own pre-result-entry work, same "one
-- permission per real job duty, not per endpoint" granularity
-- migrations/0048/0051 already used). Grant set mirrors order.result's
-- own exactly (migrations/0051): there is no distinct
-- phlebotomist/senior-verifier role in this app to gate more narrowly
-- against, confirmed by the source-of-truth audit -- ADMIN/STAFF (full-
-- access fallback, same as every other clinical-RBAC migration since
-- 0048) /DOCTOR (a small clinic's doctor may do all of this
-- themselves, same reasoning migrations/0048 already gave for DOCTOR
-- holding vitals.record) /LAB_TECH (the Lab/Radiology Worklist's whole
-- reason to exist, migrations/0051).
INSERT INTO permissions (name) VALUES
    ('order.collect'),
    ('order.verify'),
    ('order.release');

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permissions p
WHERE r.name IN ('ADMIN', 'STAFF', 'DOCTOR', 'LAB_TECH')
  AND p.name IN ('order.collect', 'order.verify', 'order.release');
