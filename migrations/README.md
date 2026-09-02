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

## What this does *not* handle

- **Provisioning** the Postgres role/database themselves. That's a
  separate, one-time step (`docker compose up`, or
  `scripts/provision_local_db.sh` for a non-Docker setup) -- see
  `SETUP.md`. `scripts/migrate.py` assumes an empty, reachable database
  already exists and connects to it via `DATABASE_URL`/`DB_*` env vars.
- **Running automatically.** Nothing in the application calls this on
  startup, on purpose (see `app/main.py`). It's a deployment/setup step
  you run explicitly.
