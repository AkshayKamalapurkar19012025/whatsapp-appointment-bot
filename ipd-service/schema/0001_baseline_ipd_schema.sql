-- IPD service -- baseline schema sketch.
--
-- SUPERSEDED (2026-09-21): the OPD/HIMS master spec's Phase 0 audit
-- (docs/OPD_HIMS_P0_AUDIT.md section 3) flagged that this file's
-- separate-database service boundary conflicts with the master spec's
-- Principle 3/4 (shared orders/results/billing/timeline across
-- OPD/IPD/Emergency) and section 76 ("IPD Compatibility"). The decision
-- was Option A: encounters -- and, when IPD is actually built, IPD data
-- -- live in the OPD app's own database (see migrations/
-- 0028_encounters.sql's `encounters.encounter_type` column, which
-- already reserves 'IPD'/'EMERGENCY' for this). This sketch is left in
-- place as a record of the alternative that was considered and not
-- taken, not as a schema to build against -- IPD implementation itself
-- remains future work (master spec section 77, Phase 3 only covers
-- OPD), and when it starts it should extend the OPD database's
-- `encounters`/patient model, not this file.
--
-- Service boundary: this is its OWN database, not a new set of tables
-- bolted onto the OPD app's Postgres instance. The two domains share
-- almost no access patterns (OPD is slot/appointment-shaped and
-- stateless between visits; IPD is occupancy-shaped and stateful for
-- days/weeks per admission), and IPD's failure modes (a stuck bed
-- transfer, a bad discharge write) must never be able to take down
-- appointment booking or the WhatsApp bot.
--
-- Patient identity: this service does NOT own patient demographics.
-- It references the OPD app's patients.uhid (see
-- migrations/0024_patient_uhid.sql in the main repo) as a plain TEXT
-- value -- there is no live database FK across services, so uhid is
-- stored here and validated at the API layer (the IPD service calls
-- the OPD app's patient-lookup endpoint, or a shared read replica,
-- before admitting). This is the same reasoning the OPD schema itself
-- uses for doctors/departments: reference the identity, not a
-- cross-database constraint that can't actually be enforced.
--
-- Scope discipline, matching the main repo's baseline migration: this
-- sketch covers what's needed to represent "a patient is admitted to a
-- bed, clinical/nursing orders are placed against that admission, and
-- the admission ends in a discharge" -- the IPD box in the requested
-- flow diagram. It deliberately does NOT include lab/radiology/
-- pharmacy/billing integration tables (those are their own services;
-- an admission here only needs to reference orders placed against it,
-- not fulfill them) and does not include vitals/nursing-note detail
-- beyond what's needed to prove the shape out.

CREATE TABLE wards (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    -- e.g. GENERAL, ICU, MATERNITY, PEDIATRIC -- drives default nursing
    -- ratios and monitoring cadence in the application layer; not
    -- enforced here as an enum since a hospital's ward taxonomy is
    -- site-specific configuration, not a fixed domain.
    ward_type   TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE beds (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ward_id         BIGINT NOT NULL REFERENCES wards(id),
    bed_number      TEXT NOT NULL,
    -- AVAILABLE / OCCUPIED / CLEANING / OUT_OF_SERVICE. Denormalized
    -- onto the bed (rather than derived from admissions each read)
    -- because bed-board views are the IPD service's highest-frequency
    -- read and occupancy also needs to reflect non-admission states
    -- (cleaning, maintenance) that have no admission row at all.
    -- Kept consistent with admissions.status by the application layer:
    -- every admit/transfer/discharge writes both in one transaction.
    status          TEXT NOT NULL DEFAULT 'AVAILABLE'
        CHECK (status IN ('AVAILABLE', 'OCCUPIED', 'CLEANING', 'OUT_OF_SERVICE')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ward_id, bed_number)
);

-- One row per admission episode. patient_uhid is a plain string (see
-- boundary note above) -- not FK-able, so validated at the API layer
-- and indexed for "show me this patient's admission history" lookups.
CREATE TABLE admissions (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_uhid            TEXT NOT NULL,
    -- Free text at admission time (deliberately not FK'd to a doctors
    -- table this service doesn't own); the OPD doctor directory is a
    -- separate system of record, so this stays a display/attribution
    -- field only, same posture as patient_uhid.
    admitting_doctor_name   TEXT NOT NULL,
    admission_reason        TEXT NOT NULL,
    -- PLANNED / ADMITTED / DISCHARGED / TRANSFERRED_OUT (a hard
    -- transfer to another facility, distinct from an internal bed
    -- transfer, which is bed_transfers below and doesn't end the
    -- admission).
    status                  TEXT NOT NULL DEFAULT 'ADMITTED'
        CHECK (status IN ('PLANNED', 'ADMITTED', 'DISCHARGED', 'TRANSFERRED_OUT')),
    current_bed_id          BIGINT REFERENCES beds(id),
    admitted_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    discharged_at           TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- A discharged/transferred-out admission is closed: no bed, and a
    -- close timestamp. An open one must hold a bed. Catches the same
    -- class of "status says one thing, the rest of the row says
    -- another" bug the OPD schema guards against with payment_status.
    CHECK (
        (status IN ('ADMITTED') AND current_bed_id IS NOT NULL AND discharged_at IS NULL)
        OR (status IN ('PLANNED') AND current_bed_id IS NULL AND discharged_at IS NULL)
        OR (status IN ('DISCHARGED', 'TRANSFERRED_OUT') AND current_bed_id IS NULL AND discharged_at IS NOT NULL)
    )
);

CREATE INDEX admissions_patient_uhid_idx ON admissions (patient_uhid);
-- Only one open (ADMITTED) admission per bed at a time -- the actual
-- occupancy invariant. Partial unique index rather than a UNIQUE
-- constraint since DISCHARGED/TRANSFERRED_OUT rows for the same bed
-- must be allowed to accumulate.
CREATE UNIQUE INDEX admissions_one_open_per_bed_idx ON admissions (current_bed_id)
    WHERE status = 'ADMITTED';

-- Every bed change during an admission -- initial assignment is a
-- transfer too (from NULL), so bed history is always a plain scan of
-- this table for a given admission, no special-casing "where did they
-- start".
CREATE TABLE bed_transfers (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    admission_id    BIGINT NOT NULL REFERENCES admissions(id),
    from_bed_id     BIGINT REFERENCES beds(id),
    to_bed_id       BIGINT NOT NULL REFERENCES beds(id),
    reason          TEXT,
    transferred_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    transferred_by  TEXT NOT NULL
);

-- Clinical / nursing orders placed against an admission. order_type
-- distinguishes a nursing order (e.g. "vitals every 4h") from a
-- clinical order that fans out to another service (lab, radiology,
-- pharmacy) -- this table only records that the order was placed and
-- its own lifecycle (ORDERED -> IN_PROGRESS -> COMPLETED/CANCELLED);
-- it does not perform the lab test or fill the prescription. Those
-- services own their own fulfillment state and would reference
-- orders.id (or a shared order-reference string) the same loose way
-- this schema references patient_uhid.
CREATE TABLE clinical_orders (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    admission_id    BIGINT NOT NULL REFERENCES admissions(id),
    order_type      TEXT NOT NULL
        CHECK (order_type IN ('NURSING', 'LAB', 'RADIOLOGY', 'PHARMACY', 'DIET', 'OTHER')),
    description     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ORDERED'
        CHECK (status IN ('ORDERED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    ordered_by      TEXT NOT NULL,
    ordered_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX clinical_orders_admission_id_idx ON clinical_orders (admission_id);

-- Discharge is modeled as a detail row (1:1 with a DISCHARGED
-- admission) rather than extra columns on admissions itself, the same
-- way this repo keeps queue-token detail off appointments' core
-- columns -- discharge has its own substantial, admission-closing-time
-- payload (summary, follow-up plan) that a bed-board or active-
-- admissions query never needs to select.
CREATE TABLE discharge_summaries (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    admission_id            BIGINT NOT NULL UNIQUE REFERENCES admissions(id),
    diagnosis                TEXT NOT NULL,
    treatment_summary       TEXT NOT NULL,
    follow_up_instructions  TEXT,
    discharged_by           TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE admissions IS
    'One row per inpatient episode. patient_uhid references the OPD service''s patients.uhid by value only -- no cross-database FK. status/current_bed_id/discharged_at are kept mutually consistent by the CHECK constraint above; the application writes admit/transfer/discharge as a single transaction against both this table and beds.status.';
