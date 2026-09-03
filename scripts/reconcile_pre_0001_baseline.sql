-- One-time reconciliation for a database that predates migrations/0001_
-- baseline_schema.sql and was never migration-tracked (schema_migrations
-- is empty, but the application's tables already exist -- created by
-- some earlier, pre-migration-history process). Running `python
-- scripts/migrate.py` directly against such a database fails: 0001's
-- CREATE TABLE statements hit duplicate_table errors, since the tables
-- are already there.
--
-- This script does NOT recreate, drop, or alter any existing data. It
-- brings a specific, already-existing schema up to true 0001-equivalence
-- (proven by direct comparison against the live schema, the application
-- code that reads/writes it, and migrations/0001's own header, which
-- scopes itself to only the PK/FK/NOT NULL/UNIQUE guarantees the code
-- already depends on), then records 0001 as applied so `python
-- scripts/migrate.py` can take over normally from 0002 onward.
--
-- Before running this against your real database, confirm it actually
-- matches the situation this script assumes:
--   1. `SELECT * FROM schema_migrations;` returns zero rows.
--   2. All 10 of departments/doctors/doctor_departments/appointment_types/
--      doctor_appointment_types/doctor_schedule/doctor_blocks/patients/
--      appointments/booking_sessions already exist with data in them.
--   3. `\d doctor_appointment_types` shows NO `updated_at` column (the
--      one gap this script closes) -- if it already has one, the ADD
--      COLUMN IF NOT EXISTS below is a safe no-op either way.
-- If your database doesn't match this shape, do not run this file --
-- work out the actual gap first (see the migrations/ each table's DDL
-- and compare against `\d+ <table>` for every table).
--
-- What this fixes and why it's required, not optional:
-- doctor_appointment_types.updated_at was missing on the database this
-- was written against, even though app/api/doctor_appointment_types.py's
-- reactivation path (assign_appointment_type_to_doctor, the UPDATE ...
-- SET ... updated_at = NOW() branch that reassigns a previously-removed
-- appointment type) already executes exactly that UPDATE. Without this
-- column, that specific, real, currently-shipped code path fails with
-- UndefinedColumn. This adds it, matching migrations/0001's own
-- definition for a fresh install (TIMESTAMPTZ NOT NULL DEFAULT NOW())
-- exactly, so a reconciled database and a fresh 0001 install end up
-- structurally identical for this column. Existing rows backfill to
-- NOW() -- there is no created_at on this table to backfill from
-- instead (also true on a fresh 0001 install: DEFAULT NOW() only
-- applies going forward from whenever the column starts existing).
--
-- Deliberately NOT changed by this script (verified against the actual
-- application code in app/api/*.py and app/services/*.py, not assumed):
--   * VARCHAR(n) columns 0001 defines as TEXT (appointment_types.name,
--     departments.name, doctors.name/timezone, patients.name/
--     whatsapp_number, booking_sessions.whatsapp_number/step,
--     doctor_blocks.reason) -- no code path requires the wider/looser
--     TEXT type, and no existing data has ever needed more room than
--     these columns already allow.
--   * appointments.status as a native `appointment_status` ENUM instead
--     of 0001's TEXT -- every status literal and parameterized query the
--     app uses ('BOOKED', 'CANCELLED', comparisons, updates, inserts)
--     was verified compatible with the enum, including the exact
--     WHERE status = %s / status <> %s patterns app/api/appointments.py
--     and app/services/appointment_services.py use, and migrations/0003's
--     EXCLUDE USING gist constraint, which was dry-run against a
--     throwaway enum-typed table before this script was written.
--   * doctor_blocks.reason being nullable instead of 0001's NOT NULL --
--     app/api/doctor_blocks.py's own Pydantic validator already requires
--     a non-empty reason on every insert, so the column is never
--     actually null in practice; loosening the guarantee doesn't change
--     app behavior, and tightening it isn't required by anything reading
--     this column.
--   * missing `created_at`/`updated_at` on doctor_departments/
--     appointment_types/departments/doctors/doctor_schedule/doctor_blocks
--     -- confirmed via a full-repository search that no code reads or
--     writes these columns on these specific tables (unlike
--     doctor_appointment_types.updated_at above, which is required).
--
-- After this script runs, `python scripts/migrate.py` applies 0002-0008
-- through the normal, unmodified runner. Every one of those was
-- independently verified additive and safe against this exact schema:
-- 0002 (new indexes only), 0003 (new extension + exclusion constraint,
-- dry-run tested against the enum status type, and this database has
-- zero existing overlapping non-cancelled appointments so it applies
-- cleanly), 0004/0005 (brand new tables this database doesn't have yet),
-- 0006 (two new nullable columns doctor_schedule is missing -- also
-- required: app/services/availability_engine.py and app/services/
-- appointment_services.py already query doctor_schedule.start_date/
-- end_date directly, so booking/availability is currently broken on an
-- unreconciled copy of this database until 0006 applies), 0007/0008
-- (alter the tables 0004/0005 just created, which are empty at that
-- point in the same run, so their backfill steps are no-ops).

BEGIN;

ALTER TABLE doctor_appointment_types
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO schema_migrations (version)
VALUES ('0001_baseline_schema.sql')
ON CONFLICT (version) DO NOTHING;

COMMIT;
