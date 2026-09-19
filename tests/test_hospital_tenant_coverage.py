"""
CI coverage test for M2 (HospitalOS build plan): every table holding its
own hospital-scoped business data must carry a hospital_id column, so a
new table can't silently be added without that decision being made. See
migrations/0024_hospital_tenant_context.sql for the nine tables
retrofitted so far and this file's own EXEMPT_TABLES for why every other
existing table doesn't need one.
"""

EXEMPT_TABLES = {
    "schema_migrations",  # migration bookkeeping, not application data.
    "hospitals",  # the tenant table itself.
    # Pure join tables between two already hospital-scoped parents --
    # tenant is derivable through either FK, no column of its own needed.
    "doctor_appointment_types",
    "doctor_departments",
    # Child of doctors (doctor_id FK) -- tenant derivable through it.
    "doctor_education",
    # Auth/notification bookkeeping, always reached through an
    # already-scoped patient_id/staff_id, never queried on its own by
    # tenant.
    "patient_sessions",
    "patient_otp_codes",
    "staff_sessions",
    "mock_sms_outbox",
    # P1.a RBAC (migrations/0029_rbac_decomposition.sql): roles/
    # permissions/role_permissions are a fixed, code-level catalogue --
    # one permission name per resource-area router, wired directly into
    # require_permission() call sites -- not hospital-configurable data.
    # A hospital can't define its own "doctor.manage"-equivalent without
    # a code change to go with it, so there's no per-hospital row to
    # scope these tables by.
    "roles",
    "permissions",
    "role_permissions",
    # Child of staff (staff_id FK, and staff itself is hospital-scoped
    # since migrations/0024) -- tenant derivable through it, same
    # category as doctor_education above.
    "staff_roles",
    "break_glass_grants",
}


def test_every_non_exempt_table_has_hospital_id(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
        all_tables = {row[0] for row in cur.fetchall()}

        cur.execute(
            """
            SELECT table_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'hospital_id'
            """
        )
        tables_with_hospital_id = {row[0] for row in cur.fetchall()}

    missing = (all_tables - EXEMPT_TABLES) - tables_with_hospital_id

    assert not missing, (
        f"{sorted(missing)} lack a hospital_id column and aren't in "
        "EXEMPT_TABLES (tests/test_hospital_tenant_coverage.py) -- add "
        "the column in a new migration, or add the table to "
        "EXEMPT_TABLES with a comment explaining why it doesn't need one."
    )
