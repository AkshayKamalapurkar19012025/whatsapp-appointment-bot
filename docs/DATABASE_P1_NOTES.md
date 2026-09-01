# Database: P1 scalability notes

Documentation only -- nothing here is implemented yet. These are the
database changes identified during the P0 review as needed before the
app can handle meaningfully more doctors/patients/appointments or run as
more than one instance. Each needs an explicit decision before
implementation, since several affect existing behaviour or are schema
changes.

## 1. Indexes for appointment lookup/overlap queries

Every double-booking check (`app/api/appointments.py`, `app/api/
availability.py`, `app/api/booking.py`) filters
`appointments` by `doctor_id, start_at, end_at, status`. Today that's a
sequential scan once the table has any real volume. Needed:

```sql
CREATE INDEX idx_appointments_doctor_time
    ON appointments (doctor_id, start_at, end_at)
    WHERE status <> 'CANCELLED';
```

A partial index (excluding cancelled rows) keeps it small and matches
every query's `status <> 'CANCELLED'` filter exactly.

## 2. Index for patient upcoming appointments

`get_upcoming_booked_appointments()` in `app/api/booking.py` filters
`patient_id, status = 'BOOKED', start_at > NOW()`. Needed:

```sql
CREATE INDEX idx_appointments_patient_upcoming
    ON appointments (patient_id, start_at)
    WHERE status = 'BOOKED';
```

## 3. Index for doctor_blocks

Both availability calculators filter `doctor_id, start_at, end_at,
active`. Needed:

```sql
CREATE INDEX idx_doctor_blocks_doctor_time
    ON doctor_blocks (doctor_id, start_at, end_at)
    WHERE active = TRUE;
```

## 4. Concurrency strategy consistency

Two different mechanisms currently protect against double-booking for
what's conceptually the same operation:

- `app/api/appointments.py` (plain REST `POST /api/appointments`): `SELECT
  ... FOR UPDATE` row locks on `doctors`, then on the conflicting
  `appointments` rows.
- `app/api/booking.py` (the WhatsApp flow) and its reschedule path:
  `pg_advisory_xact_lock(doctor_id)`.

These are different locking primitives. A `SELECT ... FOR UPDATE` in one
session does not block a concurrent `pg_advisory_xact_lock` in another --
they don't see each other. If a WhatsApp booking and a direct
`POST /api/appointments` call race for the same doctor/slot at the same
instant, neither lock protects against the other; only the final overlap
re-check (a plain `SELECT` before the `INSERT`) stands between them, which
is exactly the race condition Test 1 in the spec calls out. This has
**not been demonstrated to fail** in testing so far (both paths were
tested independently, not against each other simultaneously), but it is
not proven safe either, and should not be assumed safe just because each
path is safe in isolation. Needs a decision: standardize on one
mechanism (advisory lock is already proven in `booking.py`'s hot path)
or add the constraint in #5 below as a database-enforced backstop that
doesn't care which application-level lock (if any) was used.

## 5. Possible PostgreSQL exclusion constraint

A `tstzrange`-based `EXCLUDE` constraint would make double-booking
*impossible* at the database level, independent of which code path or
lock strategy inserted the row -- closing the gap in #4 without having to
choose between the two existing strategies:

```sql
ALTER TABLE appointments ADD COLUMN slot tstzrange
    GENERATED ALWAYS AS (tstzrange(start_at, end_at, '[)')) STORED;

CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE appointments ADD CONSTRAINT no_overlapping_appointments
    EXCLUDE USING gist (doctor_id WITH =, slot WITH &&)
    WHERE (status <> 'CANCELLED');
```

This is additive (a second, database-level line of defense underneath
the existing application-level locks, not a replacement for them) but is
still a schema change with real considerations: it requires the
`btree_gist` extension, changes what error class a would-be double-booking
insert raises (currently the app catches the race with its own overlap
check and returns a friendly message; with this constraint, a race that
slips past the app-level check would instead surface as a Postgres
`ExclusionViolation` that the app does not currently catch anywhere --
that catch would need to be added wherever `INSERT INTO appointments`
happens). Flagged for an explicit decision, not applied.

## 6. Connection pooling

Already implemented in P0 (`psycopg_pool.ConnectionPool`, opened via the
FastAPI lifespan). Noted here only because it's a scalability item this
document should not omit -- see the P0 report for what changed and why.
Follow-up for P1: the pool currently lives in a single module-level
global sized for one process (`DB_POOL_MIN_SIZE`/`MAX_SIZE`); running
multiple app instances means each gets its own pool, so the *total*
connections against Postgres is `instances x max_size` -- worth
capacity-planning against Postgres's own `max_connections` once instance
count is known, rather than assumed.

## 7. Migration strategy for multiple application instances

`scripts/migrate.py` now takes a Postgres advisory lock for the duration
of a run (added in this pass), so two instances/deploys running it at
the same moment won't race each other -- the second one exits cleanly
with a distinct exit code instead of erroring or double-applying. What's
still not solved, and should be decided before running several
instances in production:

- **Who runs it.** Today it's a manual, human-run step. In a multi-
  instance deployment this should be a single, explicit step in the
  deploy pipeline (run once, before the new instances start serving
  traffic) -- not run by each instance's own startup (deliberately not
  done, per the P0 instruction not to auto-migrate on boot) and not left
  to a human to remember for every deploy.
- **Zero-downtime schema changes.** Nothing here yet enforces the usual
  discipline of a growing app (additive-first migrations, expand/contract
  for renames or drops, backward-compatible with the *previous* app
  version during a rolling deploy). Fine at the current single-migration
  size; worth a written convention once migrations become frequent.
- **Alembic.** See the earlier review discussion -- recommended before
  migration count/complexity grows much further or an ORM is introduced,
  not urgent today.
