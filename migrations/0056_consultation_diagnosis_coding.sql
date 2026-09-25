-- OPD/HIMS interoperability master prompt Phase 6 (diagnosis domain
-- hardening): implements the Phase 3 design
-- (docs/OPD_HIMS_STANDARDS_READINESS.md S6's "Proposed minimal model")
-- for consultations.diagnosis -- optional terminology-code slots
-- alongside the existing free-text field, not replacing it.
--
-- consultations.diagnosis (migrations/0029_vitals_and_consultations.sql)
-- stays exactly as it is: required, human-readable, the field every
-- existing API caller/UI/report already reads. These three columns are
-- purely additive and nullable -- every existing row gets NULL in all
-- three, and reads/writes that never mention them are completely
-- unaffected.
--
-- Deliberately generic, not SNOMED-specific: diagnosis_code_system is
-- free text naming whatever terminology a code came from (e.g.
-- "SNOMED CT", "ICD-10"), not an enum/CHECK of known systems -- this
-- phase has no terminology service and isn't choosing one now (see
-- docs/OPD_HIMS_STANDARDS_READINESS.md S5/S16's own "explicitly not
-- proposed" reasoning for the identical decision on medications).
--
-- No codes are populated by this migration or by any application code
-- this phase adds -- every existing consultation's diagnosis_code stays
-- NULL until a human enters one through a future terminology-aware UI
-- that does not exist yet.
ALTER TABLE consultations
    ADD COLUMN diagnosis_code_system TEXT,
    ADD COLUMN diagnosis_code TEXT,
    ADD COLUMN diagnosis_code_display TEXT,
    -- Structural only: a code without knowing which system it's from
    -- is meaningless (docs/OPD_HIMS_STANDARDS_READINESS.md S6's own
    -- "code requires a system" instruction). No other relationship
    -- between these three columns is enforced -- e.g. system alone
    -- (no code yet) and display alone are both valid, since nothing in
    -- this phase's evidence requires otherwise.
    ADD CONSTRAINT consultations_diagnosis_code_requires_system
        CHECK (diagnosis_code IS NULL OR diagnosis_code_system IS NOT NULL);

COMMENT ON COLUMN consultations.diagnosis_code_system IS
    'Optional, free text naming the terminology diagnosis_code came from (e.g. "SNOMED CT", "ICD-10") -- not an enum, since no terminology system has been chosen yet. NULL on every consultation until a future phase adds real terminology-coded diagnosis entry.';
COMMENT ON COLUMN consultations.diagnosis_code IS
    'Optional structured code for the diagnosis in diagnosis_code_system''s terminology. Never populated automatically -- no code here has been validated against any real terminology service (none exists in this phase). NULL is the default and expected state for every existing and new consultation.';
COMMENT ON COLUMN consultations.diagnosis_code_display IS
    'Optional, the terminology''s own display string for diagnosis_code (which can differ from the free-text diagnosis a clinician typed) -- kept separate from diagnosis itself so neither ever silently overwrites the other.';

-- consultation_amendments (migrations/0041_consultation_amendments.sql)
-- archives the pre-amendment value of every amendable consultation
-- field, most recently extended for disposition/disposition_notes
-- (migrations/0047_consultation_disposition.sql) -- same treatment
-- here, so amending a consultation's coded diagnosis is captured in
-- the same before/after audit trail as everything else, not silently
-- dropped. No CHECK constraint mirrored onto this table, same as
-- migrations/0047 didn't mirror its disposition CHECK here either --
-- this table only ever receives values already validated by the
-- consultations row they were copied from.
ALTER TABLE consultation_amendments
    ADD COLUMN previous_diagnosis_code_system TEXT,
    ADD COLUMN previous_diagnosis_code TEXT,
    ADD COLUMN previous_diagnosis_code_display TEXT;
