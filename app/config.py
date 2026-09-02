"""
Application configuration, sourced from environment variables.

Nothing here changes default behaviour: with no environment variables set,
DATABASE_URL resolves to the exact same DSN that was previously hardcoded
in app/db/connection.py ("dbname=appointment_bot user=akshaykumar"), so
existing local setups keep working unchanged. Environment variables only
override that default when explicitly provided (e.g. in production).
"""

import os

from dotenv import load_dotenv

# Loads variables from a local .env file (if one exists) into os.environ.
# By default this never overrides a variable that is already set in the
# real environment (e.g. exported by the shell, or set by a container
# runtime/deploy platform) -- it only fills gaps. Safe no-op if no .env
# file is present, which is the case for anyone who already exports env
# vars directly and never had a .env before this change.
load_dotenv()


def _build_default_database_url() -> str:
    # Explicit full DSN/URL takes priority over the individual DB_* parts.
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return database_url

    db_name = os.environ.get("DB_NAME", "appointment_bot")
    db_user = os.environ.get("DB_USER", "akshaykumar")
    db_password = os.environ.get("DB_PASSWORD")
    db_host = os.environ.get("DB_HOST")
    db_port = os.environ.get("DB_PORT")

    parts = [f"dbname={db_name}", f"user={db_user}"]

    if db_password:
        parts.append(f"password={db_password}")
    if db_host:
        parts.append(f"host={db_host}")
    if db_port:
        parts.append(f"port={db_port}")

    return " ".join(parts)


DATABASE_URL = _build_default_database_url()

# Connection pool sizing. Defaults are conservative for a single-instance
# deployment; override via environment for larger-scale usage.
DB_POOL_MIN_SIZE = int(os.environ.get("DB_POOL_MIN_SIZE", "1"))
DB_POOL_MAX_SIZE = int(os.environ.get("DB_POOL_MAX_SIZE", "10"))

# Defaults to "development" so local setups and tests work unchanged with
# nothing set. A real deployment MUST set ENVIRONMENT=production -- this
# gates dev-only endpoints such as the mock-OTP lookup endpoint
# (app/api/patient_auth.py), which would otherwise let anyone read a
# patient's current OTP code.
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")
