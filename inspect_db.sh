#!/usr/bin/env bash
# Read-only inspection of the local appointment_bot database.
# Runs nothing but \d+, SELECT, and catalog queries -- no writes, no locks
# beyond what a normal read takes. Safe to run any time.
#
# Usage: ./inspect_db.sh > db_dump.txt
# Then paste db_dump.txt's contents back to Claude.

set -euo pipefail

DB="${1:-appointment_bot}"
PSQL="psql dbname=$DB"

echo "############################################"
echo "# schema_migrations"
echo "############################################"
$PSQL -c "SELECT version, applied_at FROM schema_migrations ORDER BY applied_at;" 2>&1 || echo "(table does not exist)"

echo
echo "############################################"
echo "# Extensions"
echo "############################################"
$PSQL -c "SELECT extname, extversion FROM pg_extension ORDER BY extname;"

echo
echo "############################################"
echo "# Table list"
echo "############################################"
$PSQL -c "\dt"

echo
echo "############################################"
echo "# Full schema detail for every table (columns, types, defaults,"
echo "# nullability, PK/FK/UNIQUE/CHECK constraints, indexes)"
echo "############################################"
TABLES=$($PSQL -tAc "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;")
for t in $TABLES; do
  echo
  echo "---- $t ----"
  $PSQL -c "\d+ $t"
done

echo
echo "############################################"
echo "# Row counts (existing business/auth data)"
echo "############################################"
for t in $TABLES; do
  count=$($PSQL -tAc "SELECT COUNT(*) FROM \"$t\";" 2>&1)
  printf "%-30s %s\n" "$t" "$count"
done

echo
echo "############################################"
echo "# Any overlapping non-cancelled appointments per doctor"
echo "# (sanity check before any exclusion-constraint-related change)"
echo "############################################"
$PSQL -c "
SELECT a1.doctor_id, a1.id, a1.start_at, a1.end_at, a2.id, a2.start_at, a2.end_at
FROM appointments a1
JOIN appointments a2
  ON a1.doctor_id = a2.doctor_id AND a1.id < a2.id
  AND a1.status <> 'CANCELLED' AND a2.status <> 'CANCELLED'
  AND a1.start_at < a2.end_at AND a1.end_at > a2.start_at;
" 2>&1 || echo "(appointments table not queryable as expected -- see above)"

echo
echo "############################################"
echo "# appointment_status enum values, if it exists as a type"
echo "############################################"
$PSQL -c "
SELECT t.typname, e.enumlabel, e.enumsortorder
FROM pg_type t
JOIN pg_enum e ON t.oid = e.enumtypid
WHERE t.typname = 'appointment_status'
ORDER BY e.enumsortorder;
" 2>&1 || echo "(no such enum)"

echo
echo "############################################"
echo "# PostgreSQL version"
echo "############################################"
$PSQL -c "SELECT version();"
