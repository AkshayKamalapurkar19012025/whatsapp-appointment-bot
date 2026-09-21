-- OPD/HIMS master spec Phase 5 (Triage + Clinical Consultation): the
-- first clinical documentation tables, keyed off encounters (migrations/
-- 0028_encounters.sql) per that migration's own stated purpose. Both
-- tables are new and additive -- no existing table touched.
--
-- Scope discipline for this migration specifically: diagnosis is a
-- single free-text field on `consultations`, not a separate
-- `diagnoses` table with structured codes (ICD or otherwise). The
-- master spec's own section 59 warns "do not create all of these
-- blindly"; a structured, codeable diagnosis list is real future work
-- once there's an actual code set/search UI to justify the extra
-- table, not before. Likewise, follow-up is recorded here as a plain
-- recommendation (date + reason) on the consultation, not wired to
-- actually creating a follow-up appointment -- that composition
-- (reusing create_appointment_service with a doctor/slot picker in the
-- consultation UI) is flagged as explicit follow-on work in this
-- phase's report, not built blind here.

-- One row per triage/vitals recording. Deliberately append-only (no
-- UNIQUE on encounter_id) -- a nurse can recheck vitals during the same
-- visit, and every check should stay in the record, not overwrite the
-- last one. "Current vitals" for a screen showing one is simply the
-- most recent row by recorded_at.
CREATE TABLE vitals (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    encounter_id        BIGINT NOT NULL REFERENCES encounters(id),
    recorded_by         BIGINT NOT NULL REFERENCES staff(id),
    bp_systolic         SMALLINT CHECK (bp_systolic IS NULL OR bp_systolic > 0),
    bp_diastolic        SMALLINT CHECK (bp_diastolic IS NULL OR bp_diastolic > 0),
    pulse               SMALLINT CHECK (pulse IS NULL OR pulse > 0),
    temperature_celsius NUMERIC(4, 1) CHECK (temperature_celsius IS NULL OR temperature_celsius > 0),
    spo2                SMALLINT CHECK (spo2 IS NULL OR (spo2 > 0 AND spo2 <= 100)),
    respiratory_rate    SMALLINT CHECK (respiratory_rate IS NULL OR respiratory_rate > 0),
    weight_kg           NUMERIC(5, 1) CHECK (weight_kg IS NULL OR weight_kg > 0),
    height_cm           NUMERIC(5, 1) CHECK (height_cm IS NULL OR height_cm > 0),
    -- Derived, not entered -- never trust a client-supplied BMI to
    -- match its own weight/height, same reasoning this codebase always
    -- uses for a derivable number (e.g. appointments.uhid/invoice_
    -- number). NULL whenever either input is missing.
    bmi                 NUMERIC(4, 1) GENERATED ALWAYS AS (
                            CASE WHEN weight_kg IS NOT NULL AND height_cm IS NOT NULL AND height_cm > 0
                                 THEN ROUND(weight_kg / POWER(height_cm / 100.0, 2), 1)
                            END
                        ) STORED,
    pain_score          SMALLINT CHECK (pain_score IS NULL OR (pain_score >= 0 AND pain_score <= 10)),
    chief_complaint     TEXT,
    priority            TEXT NOT NULL DEFAULT 'ROUTINE'
        CHECK (priority IN ('ROUTINE', 'URGENT', 'EMERGENCY')),
    nursing_notes       TEXT,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX vitals_encounter_id_idx ON vitals (encounter_id, recorded_at DESC);

COMMENT ON TABLE vitals IS
    'Append-only triage/vitals recordings against an encounter. The most recent row (ORDER BY recorded_at DESC) is "current" for that encounter -- see app/services/clinical_services.py''s get_latest_vitals_service.';

-- One row per encounter -- the doctor's single clinical documentation
-- record for that care episode. status mirrors encounters.status's
-- OPEN/CLOSED shape (a two-state "is this done" flag, not a copy of
-- appointments.status) for the same reason: a consultation is either
-- still being written (DRAFT) or finished (COMPLETED), independent of
-- whether the *visit* itself (billing, pharmacy, the appointment's own
-- status) has wrapped up yet -- see this phase's report for why those
-- are kept as two separate "done" signals rather than one.
CREATE TABLE consultations (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    encounter_id        BIGINT NOT NULL UNIQUE REFERENCES encounters(id),
    -- Attributed doctor, derived from the encounter's appointment at
    -- creation time (see get_or_create_consultation_service) -- not
    -- client-supplied, so this can never drift from who the visit was
    -- actually booked with.
    doctor_id           BIGINT NOT NULL REFERENCES doctors(id),
    status              TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'COMPLETED')),
    chief_complaint     TEXT,
    history_notes       TEXT,
    examination_notes   TEXT,
    diagnosis           TEXT,
    clinical_notes      TEXT,
    follow_up_date      DATE,
    follow_up_reason    TEXT,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    created_by          BIGINT NOT NULL REFERENCES staff(id),
    updated_by          BIGINT REFERENCES staff(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (status = 'DRAFT' AND completed_at IS NULL)
        OR (status = 'COMPLETED' AND completed_at IS NOT NULL)
    )
);

CREATE INDEX consultations_doctor_id_idx ON consultations (doctor_id);

COMMENT ON TABLE consultations IS
    'One row per encounter -- the doctor''s clinical documentation for that visit. See app/services/clinical_services.py for the create/save-draft/complete lifecycle. No amendment workflow yet: once COMPLETED, save_consultation_draft_service refuses further edits (master spec section 70, "controlled amendment/void processes" -- not built in this phase).';
