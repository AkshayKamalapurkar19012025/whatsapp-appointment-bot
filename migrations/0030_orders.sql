-- OPD/HIMS master spec Phase 6 (Order Spine): one generic `orders`
-- table for every investigation/service a doctor orders during a
-- consultation -- lab, radiology, procedure, a plain service, or an
-- external referral when the internal module isn't available. This is
-- deliberately the ONE table for all of them (master spec Principle 3:
-- "Do NOT create disconnected modules... Order / Patient / Encounter /
-- Ordering clinician / Order type / Priority / Status / Result"), not
-- separate lab_orders/radiology_orders/procedure_orders tables -- order_
-- type is what distinguishes them, the same way encounter_type
-- distinguishes OPD/IPD/Emergency on `encounters` rather than separate
-- tables per origin.
--
-- Scope discipline for this migration: `description` is free text, not
-- a foreign key into a test/service catalog -- no such catalog exists
-- yet, and the master spec's own section 59 warns against creating
-- entities blindly ("do not create all of these blindly... reuse
-- existing entities where they already exist"). Likewise, `result_text`
-- is a single free-text slot, not the structured lab_results/
-- radiology_reports tables the master spec describes for Phase 7 --
-- this migration only builds the spine (an order exists, has a type,
-- a status, and can eventually carry a result), not the collection/
-- processing/verification workflow that fills that result in, which is
-- real, separate future work (master spec section 32/33).
--
-- Status lifecycle this phase actually wires up: ORDERED (on create) and
-- CANCELLED (explicit staff action, with a required reason -- master
-- spec section 90's confirmation-UX rule for a dangerous action).
-- IN_PROGRESS/COMPLETED are included in the CHECK constraint so Phase 7's
-- real lab/radiology workflow can use them without another migration,
-- but nothing in this phase's application code ever sets them -- no
-- fake "mark in progress" button with no real workflow behind it.

CREATE TABLE orders (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    encounter_id            BIGINT NOT NULL REFERENCES encounters(id),
    order_type              TEXT NOT NULL
        CHECK (order_type IN ('LAB', 'RADIOLOGY', 'PROCEDURE', 'SERVICE', 'EXTERNAL_REFERRAL')),
    description              TEXT NOT NULL,
    clinical_indication      TEXT,
    priority                 TEXT NOT NULL DEFAULT 'ROUTINE'
        CHECK (priority IN ('ROUTINE', 'URGENT', 'STAT')),
    status                   TEXT NOT NULL DEFAULT 'ORDERED'
        CHECK (status IN ('ORDERED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    -- Only meaningful (and required) when order_type = EXTERNAL_REFERRAL
    -- -- e.g. "External Laboratory" (master spec section 31).
    external_destination     TEXT,
    -- Minimal free-text result slot -- see header note above on why
    -- this isn't a structured table yet.
    result_text              TEXT,
    -- Attributed ordering doctor, derived server-side from the
    -- encounter's appointment at creation time -- same non-client-
    -- supplied discipline as consultations.doctor_id
    -- (migrations/0029).
    ordering_doctor_id       BIGINT NOT NULL REFERENCES doctors(id),
    created_by               BIGINT NOT NULL REFERENCES staff(id),
    cancelled_by              BIGINT REFERENCES staff(id),
    cancel_reason             TEXT,
    ordered_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at              TIMESTAMPTZ,
    cancelled_at               TIMESTAMPTZ,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (order_type <> 'EXTERNAL_REFERRAL' OR external_destination IS NOT NULL),
    -- Each status keeps its own timestamp column consistent with it --
    -- boolean-equality form (rather than the OR-of-two-cases form
    -- migrations/0028/0029 used) because there are two independent
    -- terminal timestamps here, not one: this reads as "completed_at is
    -- set if and only if status is COMPLETED", and the same for
    -- cancelled_at/CANCELLED, which also correctly forces both NULL
    -- for ORDERED/IN_PROGRESS.
    CHECK ((status = 'COMPLETED') = (completed_at IS NOT NULL)),
    CHECK ((status = 'CANCELLED') = (cancelled_at IS NOT NULL))
);

CREATE INDEX orders_encounter_id_idx ON orders (encounter_id, ordered_at DESC);

-- A future worklist (Phase 7: "Laboratory work queue", "Radiology
-- worklist") will filter on exactly this -- live orders by type,
-- oldest first. Not used by any query this phase adds, but cheap and
-- obviously correct to add now rather than as a follow-up migration
-- once that worklist exists.
CREATE INDEX orders_open_by_type_idx ON orders (order_type, ordered_at)
    WHERE status IN ('ORDERED', 'IN_PROGRESS');

COMMENT ON TABLE orders IS
    'One row per investigation/service/referral ordered during a consultation, keyed to the encounter it belongs to -- see app/services/order_services.py. order_type distinguishes LAB/RADIOLOGY/PROCEDURE/SERVICE/EXTERNAL_REFERRAL; there is deliberately no separate table per type (master spec Principle 3).';
