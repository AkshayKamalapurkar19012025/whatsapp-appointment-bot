-- Introduces `encounters` -- the clinical-episode entity the OPD/HIMS
-- expansion's data model (vitals, consultations, diagnoses, orders,
-- prescriptions, ...) will key off, per the Phase 0 audit
-- (docs/OPD_HIMS_P0_AUDIT.md section 3) and the resulting decision: add
-- encounters inside this database rather than treat IPD/future services
-- as fully isolated (the direction ipd-service/schema's standalone
-- sketch had taken).
--
-- Deliberately does NOT touch `appointments` at all -- no new column,
-- no changed constraint. The link is one-directional, from
-- encounters.appointment_id back to the appointment that opened it,
-- which is enough for every OPD lookup ("the encounter for this
-- appointment") without adding any risk to the appointments table's
-- existing, heavily-tested concurrency/booking/billing/queue logic.
--
-- Scope for this migration: only what OPD needs today (one encounter
-- per OPD appointment, opened alongside it, closed when the appointment
-- reaches a terminal status, carried forward -- not re-created -- across
-- a reschedule). encounter_type already distinguishes OPD from future
-- IPD/EMERGENCY per the master spec's Principle 2/3 ("the encounter
-- determines whether it originated from OPD/IPD/Emergency"; "orders,
-- consultation, billing, and queue activity must belong to the correct
-- encounter"), but this migration does not create IPD/EMERGENCY rows or
-- any application code path for them -- that is future work, not
-- implemented merely for schema completeness (master spec section 77,
-- "do not implement future modules merely for visual completeness").

CREATE TABLE encounters (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id      BIGINT NOT NULL REFERENCES patients(id),
    encounter_type  TEXT NOT NULL DEFAULT 'OPD'
        CHECK (encounter_type IN ('OPD', 'IPD', 'EMERGENCY')),
    -- OPEN = the care episode is still active (equivalently: the
    -- linked appointment hasn't reached a terminal status yet). CLOSED
    -- = the episode ended -- appointment cancelled/rejected/completed/
    -- no-showed. No third state on purpose: this table doesn't mirror
    -- appointments.status's full lifecycle (PENDING/CONFIRMED/
    -- CHECKED_IN/...), it only tracks whether the episode itself is
    -- still open, matching what a future encounter-scoped query
    -- ("show me this patient's open encounters") actually needs.
    status          TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'CLOSED')),
    -- The OPD appointment/walk-in/follow-up that opened this encounter.
    -- NULL is reserved for a future encounter that doesn't originate
    -- from an appointment (an IPD admission, an ED presentation) --
    -- every OPD encounter must have one, enforced by the CHECK below.
    -- Carried forward (UPDATEd, not re-inserted) across a reschedule --
    -- see app/services/appointment_services.py's
    -- reschedule_appointment_service -- since a reschedule is the same
    -- care episode moved in time, not a new one.
    appointment_id  BIGINT REFERENCES appointments(id),
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (encounter_type <> 'OPD' OR appointment_id IS NOT NULL),
    CHECK (
        (status = 'OPEN' AND closed_at IS NULL)
        OR (status = 'CLOSED' AND closed_at IS NOT NULL)
    )
);

-- One encounter per appointment -- partial (not a plain UNIQUE column
-- constraint) so a future non-OPD encounter with a NULL appointment_id
-- is never blocked by this.
CREATE UNIQUE INDEX encounters_appointment_id_key ON encounters (appointment_id)
    WHERE appointment_id IS NOT NULL;

-- Patient 360 / "this patient's encounters" is the other lookup shape
-- this table exists to serve.
CREATE INDEX encounters_patient_id_idx ON encounters (patient_id);

COMMENT ON TABLE encounters IS
    'One row per clinical care episode. For encounter_type = OPD (the only type any application code creates today), appointment_id is always set and is this encounter''s entire identity -- see create_appointment_service/reschedule_appointment_service/_transition_appointment_status in app/services/appointment_services.py for how rows here are created, carried forward across a reschedule, and closed.';

-- Backfill: every appointment that already exists gets exactly one
-- encounter, computed from its own current status/timestamps -- not a
-- placeholder to be fixed up later. status/closed_at use the same
-- terminal-status set (CANCELLED, REJECTED, COMPLETED, NO_SHOW) as
-- RELEASED_STATUSES/ACTIONABLE_STATUSES in app/services/
-- appointment_services.py; keep these in lockstep if that set ever
-- changes.
INSERT INTO encounters (patient_id, encounter_type, status, appointment_id, opened_at, closed_at, created_at, updated_at)
SELECT
    patient_id,
    'OPD',
    CASE WHEN status IN ('CANCELLED', 'REJECTED', 'COMPLETED', 'NO_SHOW')
         THEN 'CLOSED' ELSE 'OPEN' END,
    id,
    created_at,
    CASE WHEN status IN ('CANCELLED', 'REJECTED', 'COMPLETED', 'NO_SHOW')
         THEN updated_at ELSE NULL END,
    created_at,
    updated_at
FROM appointments;
