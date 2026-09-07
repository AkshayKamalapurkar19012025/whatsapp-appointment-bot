-- One-time reconciliation for a database whose booking_sessions table's
-- constraints (primary key, the whatsapp_number UNIQUE, and/or its 5
-- foreign keys) aren't named the way a fresh migrations/0001-onward run
-- would name them -- e.g. because the database was bootstrapped via
-- scripts/reconcile_pre_0001_baseline.sql against a pre-existing schema
-- rather than migrations/0001 itself, which can leave constraints with
-- whatever names they already had.
--
-- migrations/0016_rename_booking_to_scheduling.sql renames
-- booking_sessions's constraints by their exact expected name (e.g.
-- ALTER TABLE ... RENAME CONSTRAINT booking_sessions_patient_id_fkey
-- TO ...) -- see that migration's own header comment for why it does
-- this explicitly rather than assuming Postgres renames constraints
-- along with the table. Against a table whose constraints are named
-- something else, that statement fails:
--   constraint "booking_sessions_patient_id_fkey" for table
--   "scheduling_sessions" does not exist
--
-- Unlike scripts/reconcile_enum_status_pre_0011.sql and
-- reconcile_enum_status_pre_0015.sql (which need pre-known target
-- values), this one doesn't need you to know what your constraints are
-- currently called: it looks each one up by what it actually is (the
-- primary key, the unique constraint, or a foreign key matched by which
-- table it references) rather than by name, and renames it to the exact
-- name migrations/0016 expects. Safe to run whether or not your names
-- already happen to match -- each rename is skipped if the constraint is
-- already correctly named. Doesn't touch any row or drop anything.
--
-- Confirm you actually need this before running migrations/0016:
--   SELECT conname FROM pg_constraint WHERE conrelid = 'booking_sessions'::regclass;
-- if every row already reads booking_sessions_pkey /
-- booking_sessions_whatsapp_number_key / booking_sessions_<col>_fkey,
-- you don't need this -- migrations/0016 will apply cleanly as-is.
--
-- Run this once, then re-run `python scripts/migrate.py` to continue
-- from 0016 onward as normal.

DO $$
DECLARE
    r RECORD;
    target_name TEXT;
BEGIN
    -- Primary key.
    SELECT conname INTO r FROM pg_constraint
    WHERE conrelid = 'booking_sessions'::regclass AND contype = 'p';
    IF FOUND AND r.conname <> 'booking_sessions_pkey' THEN
        EXECUTE format('ALTER TABLE booking_sessions RENAME CONSTRAINT %I TO %I', r.conname, 'booking_sessions_pkey');
    END IF;

    -- The one UNIQUE constraint (whatsapp_number).
    SELECT conname INTO r FROM pg_constraint
    WHERE conrelid = 'booking_sessions'::regclass AND contype = 'u';
    IF FOUND AND r.conname <> 'booking_sessions_whatsapp_number_key' THEN
        EXECUTE format('ALTER TABLE booking_sessions RENAME CONSTRAINT %I TO %I', r.conname, 'booking_sessions_whatsapp_number_key');
    END IF;

    -- The 5 foreign keys, matched by which table each one references
    -- (not by its current name).
    FOR r IN
        SELECT c.conname, target.relname AS target_table
        FROM pg_constraint c
        JOIN pg_class target ON target.oid = c.confrelid
        WHERE c.conrelid = 'booking_sessions'::regclass AND c.contype = 'f'
    LOOP
        target_name := CASE r.target_table
            WHEN 'patients' THEN 'booking_sessions_patient_id_fkey'
            WHEN 'departments' THEN 'booking_sessions_department_id_fkey'
            WHEN 'doctors' THEN 'booking_sessions_doctor_id_fkey'
            WHEN 'appointment_types' THEN 'booking_sessions_appointment_type_id_fkey'
            WHEN 'appointments' THEN 'booking_sessions_selected_appointment_id_fkey'
        END;
        IF target_name IS NOT NULL AND r.conname <> target_name THEN
            EXECUTE format('ALTER TABLE booking_sessions RENAME CONSTRAINT %I TO %I', r.conname, target_name);
        END IF;
    END LOOP;
END $$;
