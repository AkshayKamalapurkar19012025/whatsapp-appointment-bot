-- M3 (HospitalOS build plan): encounters -- the container a visit's
-- downstream clinical/administrative records (vitals, orders, diagnoses
-- in later phases) will attach to, instead of directly to an
-- appointment.
--
-- Schema note: this work order says "create encounters per the
-- foundation schema," a separate document not available here. The
-- columns below are inferred from what this migration's own backfill
-- and app/services/appointment_services.py's create_appointment_service
-- actually need -- kept intentionally minimal. Extend with a later,
-- additive migration if the real foundation schema calls for more.
--
-- encounter_type is constrained to 'OPD' only -- the only type that
-- exists anywhere in this codebase today. Widen the CHECK in a later
-- migration alongside whatever introduces the next type.
--
-- status OPEN/CLOSED mirrors the appointment's own lifecycle at the
-- moment of writing: CLOSED for a terminal appointment status
-- (COMPLETED/CANCELLED/REJECTED/NO_SHOW, see
-- migrations/0011_appointment_lifecycle_statuses.sql and
-- migrations/0015_appointment_checkin_and_no_show.sql for the full
-- status set), OPEN otherwise (PENDING/CONFIRMED/CHECKED_IN). Nothing
-- keeps an encounter's status in sync with its appointment's status
-- after this migration runs -- that's separate, later work (the order
-- spine, M9, is where encounter/appointment state starts being driven
-- by something other than direct SQL).
CREATE TABLE encounters (
    id              BIGSERIAL PRIMARY KEY,
    hospital_id     BIGINT NOT NULL REFERENCES hospitals(id),
    patient_id      BIGINT NOT NULL REFERENCES patients(id),
    doctor_id       BIGINT NOT NULL REFERENCES doctors(id),
    encounter_type  TEXT NOT NULL DEFAULT 'OPD'
                        CHECK (encounter_type IN ('OPD')),
    status          TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN', 'CLOSED')),
    started_at      TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE encounters IS
    'One row per clinical visit. OPD is the only encounter_type until a later phase introduces others.';
COMMENT ON COLUMN encounters.closed_at IS
    'NULL while OPEN. Also NULL for encounters this migration backfills as already CLOSED -- the real closing time predates this column and is not fabricated, same convention as appointments.arrived_at (migrations/0023).';

-- Nullable on purpose: every appointment gets one going forward
-- (create_appointment_service creates it inline, in the same
-- transaction as the appointment itself) and this migration backfills
-- one for every existing row below, but NOT NULL is deferred to a
-- later, separate migration once every write path -- including
-- reschedule_appointment_service, which this phase does NOT touch, see
-- its own report -- is confirmed to populate it.
ALTER TABLE appointments ADD COLUMN encounter_id BIGINT REFERENCES encounters(id);

COMMENT ON COLUMN appointments.encounter_id IS
    'NULL for an appointment created by reschedule_appointment_service (not yet wired up to create one -- separate, later work) or one that predates this column with no matching backfill logic path. Never assume NOT NULL.';

-- Backfill: one OPD encounter per existing appointment. started_at from
-- arrived_at where present (the patient's actual physical arrival),
-- else start_at (the scheduled time -- the best remaining signal for a
-- row that predates the arrived_at column, migrations/0023, or was
-- never checked in).
--
-- Uses a temp table to reserve one encounters.id per appointment up
-- front, rather than joining the INSERT's RETURNING back to
-- appointments by (patient_id, doctor_id, started_at) -- that natural
-- key isn't guaranteed unique (nothing stops two appointments for the
-- same patient/doctor from sharing an identical arrived_at or start_at,
-- e.g. bulk-imported historical data), so a join back on it could
-- silently mislink a row. Correlating by appointments.id instead makes
-- that class of bug impossible regardless of what the data looks like.
CREATE TEMP TABLE _m3_appointment_encounter_map AS
SELECT
    a.id AS appointment_id,
    nextval(pg_get_serial_sequence('encounters', 'id')) AS encounter_id
FROM appointments a;

INSERT INTO encounters (id, hospital_id, patient_id, doctor_id, encounter_type, status, started_at)
SELECT
    m.encounter_id,
    a.hospital_id,
    a.patient_id,
    a.doctor_id,
    'OPD',
    CASE
        WHEN a.status IN ('COMPLETED', 'CANCELLED', 'REJECTED', 'NO_SHOW') THEN 'CLOSED'
        ELSE 'OPEN'
    END,
    COALESCE(a.arrived_at, a.start_at)
FROM appointments a
JOIN _m3_appointment_encounter_map m ON m.appointment_id = a.id;

UPDATE appointments a
SET encounter_id = m.encounter_id
FROM _m3_appointment_encounter_map m
WHERE m.appointment_id = a.id;

DROP TABLE _m3_appointment_encounter_map;
