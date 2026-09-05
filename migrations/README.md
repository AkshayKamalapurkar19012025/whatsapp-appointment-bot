# Migrations

Plain numbered `.sql` files, applied in filename order by
`scripts/migrate.py`. Each file's name (once applied) is recorded in a
`schema_migrations` table so it's never re-applied. See that script's own
docstring for exactly how it runs (locking, transactions, exit codes).

## Adding a new migration

1. Create the next file: `000N_short_description.sql` (zero-padded to 4
   digits, so filenames sort correctly up to 9999 migrations).
2. Write plain SQL. Don't wrap it in `BEGIN`/`COMMIT` -- the runner
   already runs each file in its own transaction.
3. Run `python scripts/migrate.py --check` to confirm it's picked up as
   pending, then `python scripts/migrate.py` to apply it locally.
4. Never edit a migration file that has already been applied anywhere
   (including just your own machine) -- add a new one instead. Editing an
   already-applied file means different environments end up with
   different schemas while `schema_migrations` claims they're identical.

## What's here today

- **`0001_baseline_schema.sql`** -- the baseline schema, inferred from
  the SQL the application code actually executes (not redesigned).
  Deliberately scoped to only primary keys, foreign keys, `NOT NULL`, and
  `UNIQUE` constraints the code already depends on (catches
  `UniqueViolation` or uses `ON CONFLICT`). No indexes beyond what
  `UNIQUE` creates automatically, and no `CHECK` constraints -- those are
  tracked as follow-up work, not omitted by accident. See
  `docs/DATABASE_P1_NOTES.md` for what's planned and why it wasn't done
  here.

## A database that already has these tables, from before migration history existed

`scripts/migrate.py` assumes a database is either empty or was built
entirely by running these files in order -- it has no concept of "this
table already exists with equivalent structure, treat it as already
applied." Pointing it at a database whose tables predate this migration
chain fails: 0001's `CREATE TABLE` statements hit `duplicate_table`.

If you're in that situation (`schema_migrations` is empty, but the
tables already exist with real data in them), don't run
`scripts/migrate.py` directly. See `scripts/reconcile_pre_0001_baseline.
sql` -- a one-time, read-and-review-first script that closes the one
verified real gap between such a database and what 0001 guarantees, then
records 0001 as applied so `scripts/migrate.py` can take over normally
from 0002 onward. It does not drop, recreate, or modify any existing
row.

## A database whose appointments.status is a native ENUM, not TEXT

A database reconciled via `scripts/reconcile_pre_0001_baseline.sql` (see
above) keeps its pre-existing `appointment_status` ENUM type for
`appointments.status` rather than converting it to 0001's TEXT --
verified compatible with the app under the original two-value model,
`{BOOKED, CANCELLED}`.

`migrations/0011_appointment_lifecycle_statuses.sql` introduces four new
status literals (`PENDING`, `CONFIRMED`, `REJECTED`, `VISITED`,
`COMPLETED`) and assumes a TEXT column that accepts any string. Against
the enum-typed column above, its first statement fails:
`invalid input value for enum appointment_status: "CONFIRMED"`, since
the enum type was never taught those values.

If you hit that error, run `scripts/reconcile_enum_status_pre_0011.sql`
once (it only adds enum values -- no data, rows, or the column's type
are touched), then re-run `python scripts/migrate.py` to continue from
0011 onward as normal. If your `status` column is TEXT (checked via
`SELECT typname FROM pg_type WHERE typname = 'appointment_status';`
returning no row), you will never hit this and don't need that script.

## What this does *not* handle

- **Provisioning** the Postgres role/database themselves. That's a
  separate, one-time step (`docker compose up`, or
  `scripts/provision_local_db.sh` for a non-Docker setup) -- see
  `SETUP.md`. `scripts/migrate.py` assumes an empty, reachable database
  already exists and connects to it via `DATABASE_URL`/`DB_*` env vars.
- **Running automatically.** Nothing in the application calls this on
  startup, on purpose (see `app/main.py`). It's a deployment/setup step
  you run explicitly.
