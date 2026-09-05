-- One-time reconciliation for a database whose appointments.status
-- column is a native `appointment_status` ENUM rather than 0001's TEXT
-- (see scripts/reconcile_pre_0001_baseline.sql's note on this exact
-- divergence -- it was verified compatible with the app under the
-- original two-value model, {BOOKED, CANCELLED}).
--
-- migrations/0011_appointment_lifecycle_statuses.sql assumes a TEXT
-- column that accepts any string literal. Against an ENUM-typed column
-- whose type has never been taught the new lifecycle values, its very
-- first statement fails:
--   invalid input value for enum appointment_status: "CONFIRMED"
--
-- This script only ADDS enum values -- it does not touch any row, drop
-- anything, or change the column's type. Each is a separate statement
-- (required: Postgres does not let a value added by ALTER TYPE ... ADD
-- VALUE be *used* in the same transaction that added it, so this must
-- not be wrapped in one shared BEGIN/COMMIT the way ordinary migrations
-- are -- run it with psql's default autocommit-per-statement behavior).
--
-- Confirm you actually need this before running it:
--   SELECT typname FROM pg_type WHERE typname = 'appointment_status';
-- returns a row -- if it returns nothing, your status column is TEXT
-- (0001's default) and you should never run this file; migrations/0011
-- already applies cleanly against TEXT with no help needed.
--
-- Run this once, then re-run `python scripts/migrate.py` to continue
-- from 0011 onward as normal.

ALTER TYPE appointment_status ADD VALUE IF NOT EXISTS 'PENDING';
ALTER TYPE appointment_status ADD VALUE IF NOT EXISTS 'CONFIRMED';
ALTER TYPE appointment_status ADD VALUE IF NOT EXISTS 'REJECTED';
ALTER TYPE appointment_status ADD VALUE IF NOT EXISTS 'VISITED';
ALTER TYPE appointment_status ADD VALUE IF NOT EXISTS 'COMPLETED';
