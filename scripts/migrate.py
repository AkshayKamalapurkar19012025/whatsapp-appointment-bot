"""
Minimal, dependency-free migration runner.

Applies every *.sql file in migrations/ (sorted by filename) that has not
already been recorded in the schema_migrations table, each inside its own
transaction. Deliberately not a full framework (no down-migrations, no
branching) -- the project doesn't need that yet, and this stays easy to
read and reason about end to end.

Concurrency: before touching anything, this acquires a Postgres session
advisory lock (a fixed, app-specific key -- see MIGRATION_LOCK_KEY below).
If a second `migrate.py` is already running (e.g. two deploys firing at
once, or a developer running it while CI also runs it), the second one
exits immediately with a clear message instead of racing the first, which
could otherwise hit a duplicate_table/duplicate_object error or apply the
same migration file twice. The lock is released explicitly when the run
finishes, and released automatically if the connection is ever dropped.

Usage:
    python scripts/migrate.py            # apply pending migrations
    python scripts/migrate.py --check    # list pending migrations, apply nothing

Exit codes:
    0   success (nothing pending, or all pending migrations applied)
    1   --check found pending migrations (nothing was applied)
    2   another migration run holds the lock; this run did nothing
    3   could not connect to the database, or a migration failed
"""

import logging
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATABASE_URL  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Arbitrary fixed key identifying "a migration run for this application" in
# Postgres's session-advisory-lock namespace. Any distinct constant works;
# this one just needs to not collide with other locks this app takes
# elsewhere (it doesn't take any others today).
MIGRATION_LOCK_KEY = 782_331_009_411


def _ensure_migrations_table(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _applied_versions(cur) -> set[str]:
    cur.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}


def _run(conn, check_only: bool) -> int:
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    with conn.cursor() as cur:
        _ensure_migrations_table(cur)
        applied = _applied_versions(cur)
    conn.commit()

    pending = [f for f in migration_files if f.name not in applied]

    if not pending:
        logger.info("No pending migrations.")
        return 0

    if check_only:
        logger.info("Pending migrations:")
        for f in pending:
            logger.info("  - %s", f.name)
        return 1

    # One transaction per migration file: if a later file fails, the
    # earlier ones that already committed stay applied and recorded.
    for migration_file in pending:
        logger.info("Applying %s ...", migration_file.name)
        sql = migration_file.read_text()

        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (migration_file.name,),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.error("Failed applying %s -- rolled back.", migration_file.name)
            raise

        logger.info("Applied %s", migration_file.name)

    logger.info("All migrations applied.")
    return 0


def main() -> int:
    check_only = "--check" in sys.argv

    try:
        conn = psycopg.connect(DATABASE_URL)
    except psycopg.OperationalError as exc:
        logger.error("Could not connect to the database: %s", exc)
        logger.error(
            "Check DATABASE_URL / DB_HOST / DB_USER / DB_PASSWORD in your "
            "environment or .env file -- see SETUP.md."
        )
        return 3

    try:
        # pg_try_advisory_lock/pg_advisory_unlock are session-level, not
        # transaction-level: holding the lock is unaffected by the
        # explicit commit()/rollback() calls _run() does per migration.
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
            got_lock = cur.fetchone()[0]
        conn.commit()

        if not got_lock:
            logger.error(
                "Another migration run already holds the lock. "
                "Exiting without applying anything."
            )
            return 2

        try:
            return _run(conn, check_only)
        except Exception as exc:
            logger.error("Migration run failed: %s", exc)
            return 3
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
