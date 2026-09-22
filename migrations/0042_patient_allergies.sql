-- OPD/HIMS master spec: allergy data model. Named explicitly in the
-- patient-header mock (section 5/27: "Allergies: Penicillin ⚠") and as
-- a required clinical-safety UX item (section 91: "Allergy warning")
-- -- there was previously no allergy field or table anywhere in this
-- codebase (confirmed by the coverage audit, docs/OPD_HIMS_MASTER_
-- SPEC_AUDIT.md).
--
-- One row per allergy, not a single free-text column on patients --
-- same "one row per fact, not a blob" pattern vitals/consultations
-- already use, and it's the only shape that supports the real
-- clinical need: allergies are individually added over time (often at
-- triage, per section 26), can each have their own severity/reaction,
-- and are resolved/retracted (mistaken entry, or the patient no longer
-- reacts), never silently edited away -- same controlled-void ethos as
-- charges/payments (migrations/0033) and consultation amendments
-- (migrations/0041).
CREATE TABLE patient_allergies (
    id              BIGSERIAL PRIMARY KEY,
    patient_id      BIGINT NOT NULL REFERENCES patients(id),
    allergen        TEXT NOT NULL,
    reaction        TEXT,
    severity        TEXT CHECK (severity IN ('MILD', 'MODERATE', 'SEVERE')),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    recorded_by     BIGINT NOT NULL REFERENCES staff(id),
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_by     BIGINT REFERENCES staff(id),
    resolved_reason TEXT,
    resolved_at     TIMESTAMPTZ,
    CHECK ((active = FALSE) = (resolved_at IS NOT NULL))
);

CREATE INDEX patient_allergies_patient_id_idx ON patient_allergies (patient_id) WHERE active = TRUE;

COMMENT ON TABLE patient_allergies IS
    'Patient-level allergy list -- shown as a clinical-safety warning wherever the patient header appears. Deactivated (active=FALSE, with a reason) rather than deleted when retracted -- the row stays as a record of what was once documented and why it was withdrawn.';
