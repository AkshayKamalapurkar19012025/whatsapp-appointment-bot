-- OPD/HIMS interoperability master prompt Phase 7 (laboratory/
-- observation/unit domain hardening): implements the Phase 3 design
-- (docs/OPD_HIMS_STANDARDS_READINESS.md S9's "order_results.unit is
-- different... needs the code-slot treatment") for order_results.unit
-- -- optional UCUM-readiness slots alongside the existing free-text
-- unit, not replacing it.
--
-- order_results.unit (migrations/0031_order_results.sql) stays exactly
-- as it is: free text, unvalidated, the field every existing API
-- caller/UI/Patient-360-timeline already reads. These two columns are
-- purely additive and nullable -- every existing row gets NULL in
-- both, and reads/writes that never mention them are completely
-- unaffected.
--
-- Two columns, not three: unlike consultations.diagnosis_code_display
-- (migrations/0056), there is no separate unit_code_display column
-- here -- the existing free-text `unit` a lab/tech already types (e.g.
-- "mg/dL") already serves as its own display string in virtually every
-- real case; a UCUM code's canonical display would essentially
-- duplicate it, unlike a diagnosis's free text and a terminology's
-- canonical display, which can genuinely differ in wording.
--
-- No code is ever assigned by this migration or by any application
-- code this phase adds -- every existing and new order_results row's
-- unit_code stays NULL until a human enters one through a future
-- terminology-aware UI that does not exist yet (see docs/workflows/
-- LABORATORY.md's "Target State"/"Gap" for why: no lab-test catalog,
-- no UCUM source, and this phase explicitly does not invent one).
ALTER TABLE order_results
    ADD COLUMN unit_system TEXT,
    ADD COLUMN unit_code TEXT,
    -- Structural only, mirroring consultations_diagnosis_code_requires_
    -- system (migrations/0056): a code without knowing which system
    -- it's from is meaningless. unit_system alone (no code yet) is
    -- still valid, since nothing in this phase's evidence requires a
    -- code to be present.
    ADD CONSTRAINT order_results_unit_code_requires_system
        CHECK (unit_code IS NULL OR unit_system IS NOT NULL);

COMMENT ON COLUMN order_results.unit_system IS
    'Optional, free text naming the terminology unit_code came from (e.g. "UCUM") -- not an enum, since no terminology system has been chosen yet. NULL on every result until a future phase adds real terminology-coded unit entry.';
COMMENT ON COLUMN order_results.unit_code IS
    'Optional structured code for the existing free-text `unit` column, in unit_system''s terminology. Never populated automatically -- no code here has been validated against any real terminology service (none exists in this phase). NULL is the default and expected state for every existing and new result.';
