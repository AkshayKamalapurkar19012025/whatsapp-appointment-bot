# Developer Setup

This gets a fresh clone of the repository running locally: PostgreSQL,
the schema, and the FastAPI app. No prior context beyond this file
should be needed.

## Prerequisites

- Python 3.11+
- Either [Docker](https://docs.docker.com/get-docker/) (recommended), or
  a local PostgreSQL 16 install
- `psql` (the Postgres client) available on your `PATH` either way --
  Docker Desktop/Engine doesn't include it on the host, so install the
  `postgresql-client` package for your OS if `psql` isn't already there

## 1. Start PostgreSQL

Pick one:

### Option A -- Docker Compose (recommended)

```bash
cp .env.example .env
```

Open `.env` and set `DB_PASSWORD` to any password of your choosing (it
is not set by default -- `docker compose up` will refuse to start
without it, on purpose, so no password ends up hardcoded anywhere).

```bash
docker compose up -d
```

This starts a Postgres 16 container named
`whatsapp_appointment_bot_postgres`, creates the `appointment_bot`
database and role from your `.env` values, and persists data in a named
Docker volume (`postgres_data`) that survives `docker compose down` --
only `docker compose down -v` deletes it.

Because the app reaches this container over TCP (not the local unix
socket), also set in `.env`:

```
DB_HOST=localhost
```

Check it's healthy:

```bash
docker compose ps
```

### Option B -- a local PostgreSQL 16 install, no Docker

Install and start Postgres 16 yourself, then provision the role and
database with the provided script (idempotent -- safe to re-run, never
drops or alters anything that already exists):

```bash
cp .env.example .env
# edit .env: set DB_PASSWORD to a password of your choosing

sudo -u postgres DB_PASSWORD=<same password as .env> ./scripts/provision_local_db.sh
```

(`sudo -u postgres` runs it as the `postgres` OS user, which on a
standard Debian/Ubuntu Postgres install has passwordless admin access to
Postgres over the local socket. If your setup differs, see the script's
header comment for the `PG_SUPERUSER`/`PG_SUPERUSER_DATABASE` overrides.)

If you're on a machine where a Postgres role matching your OS username
already has passwordless local access (peer auth) to a role/database
literally named `akshaykumar`/`appointment_bot`, you don't need this
script at all -- that's the zero-config default the app has always
assumed (see `app/config.py`), and both `DB_USER`/`DB_NAME` in `.env`
can be left unset.

## 2. Configure environment variables

Copy the example file if you haven't already:

```bash
cp .env.example .env
```

`.env` is loaded automatically (via `python-dotenv`) by both the app and
`scripts/migrate.py` -- you don't need to `export` anything by hand. See
`.env.example` for every available variable; the important ones:

| Variable | Default if unset | Notes |
|---|---|---|
| `DATABASE_URL` | (unset) | If set, overrides everything below with a full DSN/URL. |
| `DB_NAME` | `appointment_bot` | |
| `DB_USER` | `akshaykumar` | Inherited from the app's original hardcoded default. |
| `DB_PASSWORD` | (unset) | Required for Docker; optional for local peer-auth Postgres. |
| `DB_HOST` | (unset = local unix socket) | Set to `localhost` for Docker or any TCP-only Postgres. |
| `DB_PORT` | (unset = Postgres default 5432) | |
| `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | `1` / `10` | Connection pool sizing. |

A real environment variable set outside of `.env` (e.g. exported by your
shell, or set by a container platform) always takes priority over the
same variable in `.env`.

## 3. Create/access the `appointment_bot` database

Already done by step 1 (Docker creates it automatically; the provisioning
script creates it for a local install). Nothing further needed here.

## 4. Run migrations

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

python scripts/migrate.py
```

This creates every application table from
`migrations/0001_baseline_schema.sql` (see `migrations/` for details on
what it does and does not include). It is safe to run more than once --
it only applies migrations it hasn't already recorded, and takes an
advisory lock so two runs can't race each other.

To see what would be applied without applying it:

```bash
python scripts/migrate.py --check
```

**Migrations are never run automatically by the application.** This is
deliberate -- see `app/main.py`'s comment on this. You must run
`scripts/migrate.py` yourself before first starting the app, and again
after pulling in any new file under `migrations/`.

## 5. Start the FastAPI application

```bash
uvicorn app.main:app --reload
```

The app reads the same `.env` file via `app/config.py`. On startup it
opens a Postgres connection pool (`app/db/connection.py`); on shutdown it
closes it cleanly.

## 6. Verify the database health endpoint

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/health/db
```

Both should return `{"status":"ok", ...}`. The second one confirms the
app can actually reach and query the database, not just that the process
started.

## 7. Run the tests

### Automated suite (pytest)

The suite runs against a dedicated test database -- never your dev
database -- so provision one first, the same way you provisioned the
dev one in step 1, just with a `_test`-suffixed name:

```bash
# Docker path: create a second database in the same container
docker compose exec postgres createdb -U "$DB_USER" "${DB_NAME}_test"

# Non-Docker path: same provisioning script, different DB_NAME
DB_NAME="${DB_NAME}_test" DB_PASSWORD=<same password as .env> \
    sudo -u postgres ./scripts/provision_local_db.sh
```

Then run the suite (it applies migrations to that test database itself
on first run -- see `tests/conftest.py` -- and truncates all tables
between tests, so it never needs the app to already be running):

```bash
pytest
```

What's covered:

- **`tests/test_timezone_utils.py`** -- unit tests for
  `app/utils/timezone.py` (validation, aware/naive conversion, overlap
  math, per-doctor timezone lookup).
- **`tests/test_booking_flow.py`** -- integration tests for the
  registration, booking, cancellation, rescheduling, back-navigation,
  restart, and invalid-input-handling flows, plus per-doctor timezone
  behavior, all driven exactly like `app/api/booking.py`'s real callers
  (`POST /api/booking` with a `whatsapp_number`/`message` pair).
- **`tests/test_concurrency.py`** -- two double-booking protection
  tests:
  - `test_simultaneous_whatsapp_bookings_same_slot`: two patients race,
    via the WhatsApp flow, to book the identical slot. Passes
    consistently -- exactly one booking succeeds.
  - `test_cross_path_concurrent_booking`: a WhatsApp booking
    confirmation racing a direct `POST /api/appointments` for the
    identical doctor/slot. **Marked `xfail(strict=True)`** -- this is a
    confirmed, reproducible gap (double-booking occurs in the large
    majority of attempts), not a flaky or hypothetical test. See
    `docs/DATABASE_P1_NOTES.md` item 4 for the full finding and the
    fix options awaiting a decision. `strict=True` means this test will
    loudly fail (as an unexpected pass) the moment a fix actually
    closes the gap -- that's the signal to remove the `xfail` marker.

### Manual/standalone scripts

- **`scripts/concurrency_test.py`** -- the original standalone version
  of the same-path concurrency check, driving a *running* app over real
  HTTP rather than an in-process TestClient. Requires the app already
  running (step 5) with some seeded reference data.

  ```bash
  python scripts/concurrency_test.py
  ```

- **`app/db/test_connection.py`** -- a minimal manual script confirming
  the app can open a connection and run a query. Not a pytest test
  despite the filename.

  ```bash
  python app/db/test_connection.py
  ```
