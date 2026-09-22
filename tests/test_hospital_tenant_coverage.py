"""
CI coverage test for M2 (HospitalOS build plan): every table holding its
own hospital-scoped business data must carry a hospital_id column, so a
new table can't silently be added without that decision being made. See
migrations/0027_hospital_tenant_context.sql for the nine tables
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
    # P1.a RBAC (migrations/0031_rbac_decomposition.sql): roles/
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
    # since migrations/0027) -- tenant derivable through it, same
    # category as doctor_education above.
    "staff_roles",
    "break_glass_grants",
    # Child of appointments (appointment_id FK, NOT NULL) -- tenant
    # derivable through it, same category as doctor_education. From
    # main's independently-shipped billing/invoicing work, which
    # predates this coverage test existing on that line of development.
    "invoice_line_items",
    # OPD/HIMS master spec Phases 3/5-9 (migrations/0028-0033, this
    # branch's independently-shipped clinical/billing work, same
    # situation as invoice_line_items above): every one of these has a
    # NOT NULL FK to encounters, or to another table that itself
    # ultimately FKs to encounters -- and encounters carries hospital_id
    # directly (migrations/0028_encounters.sql) -- so tenant is
    # derivable through the chain, same category as doctor_education.
    # Direct child of encounters:
    "vitals",
    "consultations",
    "orders",
    "prescriptions",
    "invoices",
    # Child of one of the above:
    "order_results",         # -> orders
    "prescription_items",    # -> prescriptions
    "pharmacy_dispense_records",  # -> prescription_items -> prescriptions
    "charges",                # -> invoices
    "payments",                # -> invoices
    # Child of consultations (consultation_id FK, NOT NULL) -- tenant
    # derivable through it, same category as the direct-children-of-
    # encounters group above. OPD/HIMS master spec Phase 14 (section
    # 70's controlled amendment/void processes).
    "consultation_amendments",
    # pharmacy_stock is NOT in this list -- it has no FK to any
    # hospital-scoped entity at all (physical inventory, not a child of
    # a visit), so it got its own hospital_id column instead
    # (migrations/0037_pharmacy_stock_hospital_id.sql), same category as
    # the nine tables 0027 itself retrofitted directly.
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
