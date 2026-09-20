-- M4-M5 (HospitalOS build plan): patient_identifiers -- the first step
-- of decoupling patient identity from unique phone ownership.
-- patients.whatsapp_number stays authoritative and UNIQUE throughout
-- this phase and the next (M6 UHID, M7 drops the uniqueness
-- constraint) -- this table is purely additive: a redundant, dual-written
-- index that new readers can migrate onto one at a time (see
-- app/services/patient_identifiers.py) before anything depends on it
-- being the only source of truth.
--
-- kind is constrained to 'PHONE' only -- the only identifier kind that
-- exists anywhere in this codebase today (M8 adds a government-ID kind
-- alongside its merge/dedup work). Widen the CHECK in a later migration
-- alongside whatever introduces the next kind.
--
-- Deliberately NOT unique on (kind, value): two patients sharing a
-- phone number (a family) is real, expected data this whole identifier
-- model exists to eventually support (see M8's disambiguation UI) --
-- enforcing uniqueness here would just reproduce the exact constraint
-- this phase is working around. is_primary is unique per (patient_id,
-- kind) instead -- at most one primary identifier of a given kind per
-- patient -- via the partial index below, which doubles as the ON
-- CONFLICT target for the idempotent dual-write in
-- write_phone_identifier().
CREATE TABLE patient_identifiers (
    id          BIGSERIAL PRIMARY KEY,
    hospital_id BIGINT NOT NULL REFERENCES hospitals(id),
    patient_id  BIGINT NOT NULL REFERENCES patients(id),
    kind        TEXT NOT NULL CHECK (kind IN ('PHONE')),
    value       TEXT NOT NULL,
    is_primary  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX patient_identifiers_one_primary_per_kind
    ON patient_identifiers (patient_id, kind)
    WHERE is_primary = TRUE;

-- The lookup resolve_patient_by_identifier() actually runs.
CREATE INDEX patient_identifiers_lookup
    ON patient_identifiers (hospital_id, kind, value);

COMMENT ON TABLE patient_identifiers IS
    'Dual-written alongside patients.whatsapp_number (see app/services/patient_identifiers.py) -- not yet the source of truth for anything. More than one patient can share a value; is_primary picks the one resolve_patient_by_identifier() returns.';

-- Backfill: every existing patient's whatsapp_number as a primary PHONE
-- identifier.
INSERT INTO patient_identifiers (hospital_id, patient_id, kind, value, is_primary)
SELECT hospital_id, id, 'PHONE', whatsapp_number, TRUE
FROM patients;
