-- OPD/HIMS master spec Phase 7 (Diagnostics): attaches results to an
-- order (migrations/0030_orders.sql). One table, not one per order
-- type -- same reasoning as orders.order_type itself: `parameter`
-- distinguishes a lab panel's individual values (e.g. "Hemoglobin",
-- "WBC") from a radiology report's narrative sections (e.g.
-- "Findings", "Impression") from a plain procedure/service/external-
-- referral note, rather than a lab_results table plus a separate
-- radiology_reports table (master spec Principle 3 again, one level
-- further down the same idea Phase 6 already applied to orders
-- themselves). unit/reference_range/is_abnormal/is_critical are simply
-- NULL/FALSE for the narrative-shaped rows (radiology, external
-- referral) where they aren't meaningful -- an unused nullable column
-- is cheaper than a second table.
--
-- Deliberately many rows per order, recorded together in one batch
-- (see record_order_result_service): a lab panel comes back as several
-- parameters at once, not one at a time. No amendment workflow --
-- once an order has results and is COMPLETED, no more can be added
-- (same "no amendment workflow yet" stance as migrations/0029's
-- consultations -- master spec section 70).
--
-- Scope discipline: no separate technician/verifier two-step sign-off.
-- The master spec's result-entry field list (section 32) includes both
-- a Technician and a Verifier -- a real two-person compliance workflow
-- this app can't meaningfully enforce yet (there is no LAB_TECH/
-- pathologist staff role, same gap already flagged for NURSE/DOCTOR in
-- migrations/0029's phase report). `recorded_by` captures who actually
-- entered the result, same single-actor attribution already used
-- throughout this schema (consultations.created_by, invoice_line_
-- items.added_by); a real verification step is real future work, not
-- simulated here with no role model behind it.

CREATE TABLE order_results (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id        BIGINT NOT NULL REFERENCES orders(id),
    parameter       TEXT NOT NULL,
    result_value    TEXT NOT NULL,
    unit            TEXT,
    reference_range TEXT,
    is_abnormal     BOOLEAN NOT NULL DEFAULT FALSE,
    is_critical     BOOLEAN NOT NULL DEFAULT FALSE,
    -- Display order within the same order's result set (e.g. a CBC
    -- panel's parameters in a stable, clinically-conventional order) --
    -- not a timestamp-derived ordering, since every row in one batch
    -- shares the same recorded_at for all practical purposes.
    sequence        SMALLINT NOT NULL DEFAULT 0,
    recorded_by     BIGINT NOT NULL REFERENCES staff(id),
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX order_results_order_id_idx ON order_results (order_id, sequence);

COMMENT ON TABLE order_results IS
    'Structured result entries attached to an order (migrations/0030), recorded as one batch by record_order_result_service (app/services/order_services.py), which also transitions the parent order to COMPLETED in the same transaction. See that migration''s header for why this is one table across every order_type rather than one per type.';
