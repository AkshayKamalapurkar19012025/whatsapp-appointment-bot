"""
Shared pytest fixtures.

All DB-backed tests run against a dedicated *_test database, never
against the developer's normal dev database -- see the DB_NAME override
immediately below, which must run before anything imports app.config
(it resolves DATABASE_URL from the environment at import time).

Requires that test database to already exist and be reachable via the
same DB_HOST/DB_USER/DB_PASSWORD/DB_PORT you use for local development
(only the database name is overridden). See SETUP.md, "Run the tests",
for how to provision it -- the same scripts/provision_local_db.sh used
for the main app, just with DB_NAME set to the *_test name.
"""

import os

_base_db_name = os.environ.get("DB_NAME", "appointment_bot")
if not _base_db_name.endswith("_test"):
    os.environ["DB_NAME"] = f"{_base_db_name}_test"

import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import psycopg  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import DATABASE_URL  # noqa: E402
from app.main import app  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every application table (schema_migrations excluded -- migration
# bookkeeping, not application data). TRUNCATE ... CASCADE means the
# order here doesn't need to respect foreign keys.
APP_TABLES = [
    "scheduling_sessions",
    "mock_sms_outbox",
    "patient_sessions",
    "patient_otp_codes",
    "staff_sessions",
    "staff",
    "invoice_line_items",
    "order_results",
    "orders",
    "vitals",
    "consultations",
    "encounters",
    "appointments",
    "doctor_appointment_types",
    "doctor_blocks",
    "doctor_schedule",
    "doctor_departments",
    "patients",
    "doctors",
    "appointment_types",
    "departments",
]


@pytest.fixture(scope="session", autouse=True)
def _migrated_test_database():
    """Apply migrations/*.sql to the test database once per test run.

    Deliberately shells out to the real scripts/migrate.py rather than
    reimplementing migration logic here -- tests should exercise the
    same migration path a developer/deploy actually uses, and this
    catches a broken migration file as a test failure, not just at
    deploy time.
    """
    try:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "migrate.py")],
            capture_output=True,
            text=True,
            env=os.environ,
        )
    except FileNotFoundError as exc:
        pytest.exit(f"Could not run scripts/migrate.py: {exc}")

    if result.returncode != 0:
        pytest.exit(
            "Failed to prepare the test database "
            f"(DATABASE_URL={DATABASE_URL!r}).\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n"
            "See SETUP.md 'Run the tests' for how to provision a "
            "dedicated *_test database first."
        )


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate every application table before each test.

    Uses its own direct connection rather than the app's pool, so it
    works independently of whether a TestClient (and therefore the
    pool) has been created yet for a given test.
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE " + ", ".join(APP_TABLES) + " RESTART IDENTITY CASCADE"
            )
    yield


@pytest.fixture(scope="session")
def client():
    """A single TestClient shared for the whole test session, with the
    app's lifespan (connection pool open/close) triggered via the `with`
    form.

    Session-scoped deliberately: psycopg_pool.ConnectionPool can only be
    opened and closed once per process -- opening it again after close()
    raises PoolClosed. That's a correct constraint for the real app
    (one lifespan per server process), but it means a fresh TestClient
    per test (which re-runs the lifespan each time) breaks after the
    first test closes the pool. One shared client for the session
    matches how the app actually runs in production.

    Per-test isolation still comes from the autouse _clean_tables
    fixture (function-scoped, runs before every test body regardless of
    this fixture's scope) -- not from recreating the client.
    """
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_connection(_clean_tables):
    """A direct connection to the test database, for tests that need to
    assert on rows the API doesn't expose (or to seed data ORM-free).
    Same explicit _clean_tables dependency as `client`, for the same
    reason."""
    with psycopg.connect(DATABASE_URL) as conn:
        yield conn
