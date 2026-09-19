-- M8 (HospitalOS build plan): duplicate detection, merge, unmerge.
--
-- Two real gaps between this work order's text and what exists in this
-- codebase today, both handled explicitly rather than guessed past:
--
-- 1. Step 2 says merge rewrites patient_id on "appointments, encounters,
--    orders, observations, invoices and identifiers." orders/
--    observations (M9) and invoices (uncosted, later phase) don't exist
--    yet -- this repo hasn't reached M9 in the plan's own execution
--    order, which sequences M8 *before* M9. merge_patients() below
--    rewrites exactly the three patient-scoped tables that exist today
--    (appointments, encounters, patient_identifiers); it's a single
--    function with one UPDATE per table, so adding orders/observations/
--    invoices later is a small, additive change to that same function,
--    not a redesign.
--
-- 2. Step 1's four detection signals include "government ID," which no
--    prior migration ever added a column for. Added here
--    (patients.government_id, nullable, never required) since it's a
--    genuine, low-risk gap to close -- migrations/0026's own comment
--    already anticipated a GOVT_ID identifier kind for exactly this
--    work order, which this migration also adds.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE patients ADD COLUMN government_id TEXT;

COMMENT ON COLUMN patients.government_id IS
    'Optional (e.g. Aadhaar, passport) -- never required by registration. One of the four duplicate-detection signals (name similarity, date_of_birth, phone, government_id).';

-- Widen patient_identifiers.kind now that a second kind exists.
ALTER TABLE patient_identifiers DROP CONSTRAINT patient_identifiers_kind_check;
ALTER TABLE patient_identifiers ADD CONSTRAINT patient_identifiers_kind_check
    CHECK (kind IN ('PHONE', 'GOVT_ID'));

-- Trigram index for name-similarity search (pg_trgm's similarity()/%
-- operator) -- the "name similarity" leg of duplicate detection.
CREATE INDEX patients_name_trgm_idx ON patients USING gin (name gin_trgm_ops);

-- Retired patients still exist as ordinary rows (never deleted) --
-- merged_into_id is how a lookup on a retired identity (old UHID, old
-- phone) finds the surviving patient instead. NULL means "never merged
-- away," which is true for almost every patient, forever.
ALTER TABLE patients ADD COLUMN merged_into_id BIGINT REFERENCES patients(id);

COMMENT ON COLUMN patients.merged_into_id IS
    'NULL unless this patient record was retired by a merge, in which case it points at the surviving patient. A retired UHID/identifier lookup follows this pointer rather than returning a dead end -- see app/services/patient_merge.py.';

CREATE TABLE patient_merges (
    id                    BIGSERIAL PRIMARY KEY,
    hospital_id           BIGINT NOT NULL REFERENCES hospitals(id),
    surviving_patient_id  BIGINT NOT NULL REFERENCES patients(id),
    retired_patient_id    BIGINT NOT NULL REFERENCES patients(id),
    merged_by_staff_id    BIGINT REFERENCES staff(id),
    -- Exactly what this merge rewrote, keyed by table, detailed enough
    -- for unmerge to reverse it precisely (e.g. patient_identifiers
    -- entries record whether each was_primary, since merging can
    -- demote one from primary to make room for the survivor's own).
    -- {"appointments": [1,2], "encounters": [3],
    --  "patient_identifiers": [{"id": 10, "was_primary": true}]}
    affected              JSONB NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    unmerged_at           TIMESTAMPTZ
);

COMMENT ON TABLE patient_merges IS
    'One row per merge (never deleted, including after an unmerge -- unmerged_at records that instead). affected is the exact, replayable record unmerge_patients() reverses.';

CREATE TABLE patient_duplicate_reviews (
    id                    BIGSERIAL PRIMARY KEY,
    hospital_id           BIGINT NOT NULL REFERENCES hospitals(id),
    new_patient_id        BIGINT NOT NULL REFERENCES patients(id),
    candidate_patient_id  BIGINT NOT NULL REFERENCES patients(id),
    -- Which signals matched, e.g. {name, date_of_birth}. name_similarity
    -- is pg_trgm's score (0..1) when the name signal is one of them,
    -- NULL otherwise.
    matched_on            TEXT[] NOT NULL,
    name_similarity       REAL,
    reviewer_decision     TEXT NOT NULL DEFAULT 'PENDING'
                              CHECK (reviewer_decision IN ('PENDING', 'CONFIRMED_DUPLICATE', 'NOT_DUPLICATE')),
    reviewed_by_staff_id  BIGINT REFERENCES staff(id),
    decided_at            TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE patient_duplicate_reviews IS
    'Warns, never blocks (see the registration flow in app/api/patients.py) -- a row here never prevented new_patient_id from being created. reviewer_decision starts PENDING and is recorded either way (CONFIRMED_DUPLICATE or NOT_DUPLICATE), per this work order''s own note that those recorded decisions are the training data for tuning the match threshold later.';
