-- M2 (HospitalOS build plan): tenant context. Purely structural -- no
-- query anywhere is filtered by hospital_id yet (that's a separate,
-- later change once every read path is confirmed to pass it). This
-- just gives every business table a place to record which hospital it
-- belongs to, and seeds the single hospital this deployment currently
-- serves.
--
-- Deviation from the plan worth flagging: the plan calls for seeding
-- this row "from env" (HOSPITAL_CODE, HOSPITAL_NAME, DEFAULT_TIMEZONE).
-- Not possible here -- scripts/migrate.py applies each file's SQL text
-- verbatim with no variable substitution (see migrations/README.md), so
-- a .sql file has no way to read the application's environment. Seeded
-- with fixed defaults below instead; a deployment that needs a
-- different code/name/timezone updates this row afterward (a plain
-- UPDATE), same as any other seed data.
--
-- hospital_id defaults to 1 (this seeded row's id) so every pre-existing
-- row in every table below stays valid with no backfill statement
-- needed. That default is a migration convenience only, not a runtime
-- one -- see app/services/staff_auth.py and app/services/patient_auth.py
-- for where the request-scoped hospital_id added alongside this
-- migration is resolved from the authenticated staff/patient. No
-- application write path is changed by this migration to pass
-- hospital_id explicitly yet (there is exactly one hospital and every
-- existing test must keep passing unchanged) -- that's later work, once
-- query filtering itself is added.

CREATE TABLE hospitals (
    id          BIGSERIAL PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    timezone    TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE hospitals IS
    'One row per tenant. Exactly one row exists today (id=1) -- this application is still single-tenant in every behavior except the schema itself from this migration onward.';

INSERT INTO hospitals (code, name, timezone) VALUES ('MAIN', 'Main Hospital', 'Asia/Kolkata');

-- The nine business tables named by the plan. Deliberately not every
-- table in the schema: doctor_appointment_types/doctor_departments
-- (pure join tables between two already-scoped parents),
-- doctor_education (child of doctors), and
-- patient_sessions/patient_otp_codes/staff_sessions/mock_sms_outbox
-- (auth/notification bookkeeping, always reached through an
-- already-scoped patient_id/staff_id) are all tenant-derivable through
-- their parent FK and don't need their own column -- see
-- tests/test_hospital_tenant_coverage.py's EXEMPT_TABLES for the same
-- list, kept in one place so a genuinely new table can't skip this
-- decision silently.
ALTER TABLE departments         ADD COLUMN hospital_id BIGINT NOT NULL DEFAULT 1 REFERENCES hospitals(id);
ALTER TABLE doctors             ADD COLUMN hospital_id BIGINT NOT NULL DEFAULT 1 REFERENCES hospitals(id);
ALTER TABLE appointment_types   ADD COLUMN hospital_id BIGINT NOT NULL DEFAULT 1 REFERENCES hospitals(id);
ALTER TABLE doctor_schedule     ADD COLUMN hospital_id BIGINT NOT NULL DEFAULT 1 REFERENCES hospitals(id);
ALTER TABLE doctor_blocks       ADD COLUMN hospital_id BIGINT NOT NULL DEFAULT 1 REFERENCES hospitals(id);
ALTER TABLE patients            ADD COLUMN hospital_id BIGINT NOT NULL DEFAULT 1 REFERENCES hospitals(id);
ALTER TABLE appointments        ADD COLUMN hospital_id BIGINT NOT NULL DEFAULT 1 REFERENCES hospitals(id);
ALTER TABLE staff               ADD COLUMN hospital_id BIGINT NOT NULL DEFAULT 1 REFERENCES hospitals(id);
ALTER TABLE scheduling_sessions ADD COLUMN hospital_id BIGINT NOT NULL DEFAULT 1 REFERENCES hospitals(id);
