#!/usr/bin/env bash
#
# Fallback provisioning for developers NOT using docker-compose.yml.
#
# Creates the application's Postgres role and database if (and only if)
# they don't already exist. Safe to run more than once. Never drops,
# alters, or overwrites an existing role/database/data -- if the role or
# database already exists, this script leaves it untouched and just says
# so.
#
# Requires a Postgres superuser connection to bootstrap the new role and
# database (this is inherent to Postgres, not specific to this script --
# creating a role/database always requires an existing privileged role).
# Defaults assume a local Postgres reachable via the standard "postgres"
# superuser over the unix socket (peer auth), which is the common default
# for a freshly-installed local Postgres. Override via env vars for a
# different setup.
#
# Usage:
#   DB_PASSWORD=yourpassword ./scripts/provision_local_db.sh
#
# Env vars:
#   DB_NAME              database to create (default: appointment_bot)
#   DB_USER               role to create     (default: akshaykumar)
#   DB_PASSWORD            password for that role (required)
#   PG_SUPERUSER            bootstrap role to connect as (default: postgres)
#   PG_SUPERUSER_DATABASE  database to connect to for bootstrapping (default: postgres)

set -euo pipefail

DB_NAME="${DB_NAME:-appointment_bot}"
DB_USER="${DB_USER:-akshaykumar}"
PG_SUPERUSER="${PG_SUPERUSER:-postgres}"
PG_SUPERUSER_DATABASE="${PG_SUPERUSER_DATABASE:-postgres}"

if [ -z "${DB_PASSWORD:-}" ]; then
    echo "ERROR: DB_PASSWORD must be set." >&2
    echo "Usage: DB_PASSWORD=yourpassword ./scripts/provision_local_db.sh" >&2
    exit 1
fi

run_as_superuser() {
    psql -v ON_ERROR_STOP=1 -U "$PG_SUPERUSER" -d "$PG_SUPERUSER_DATABASE" "$@"
}

echo "Checking role \"$DB_USER\" ..."
role_exists=$(run_as_superuser -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'")

if [ "$role_exists" = "1" ]; then
    echo "Role \"$DB_USER\" already exists -- leaving it untouched."
else
    echo "Creating role \"$DB_USER\" ..."
    run_as_superuser -c "CREATE ROLE \"$DB_USER\" LOGIN PASSWORD '$DB_PASSWORD'"
fi

echo "Checking database \"$DB_NAME\" ..."
db_exists=$(run_as_superuser -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")

if [ "$db_exists" = "1" ]; then
    echo "Database \"$DB_NAME\" already exists -- leaving it (and its data) untouched."
else
    echo "Creating database \"$DB_NAME\" owned by \"$DB_USER\" ..."
    run_as_superuser -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\""
fi

echo
echo "Done. Nothing existing was modified or dropped."
echo "Next: set DB_USER=$DB_USER, DB_PASSWORD=<...>, and (if not connecting"
echo "over the local unix socket) DB_HOST/DB_PORT in your .env, then run:"
echo "  python scripts/migrate.py"
