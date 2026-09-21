-- P1.a (HospitalOS build plan): RBAC decomposition. Every existing
-- require_role("ADMIN") call site becomes require_permission(<name>),
-- resolved through staff_roles -> role_permissions -> permissions
-- instead of a direct staff.role string comparison.
--
-- staff.role itself is untouched -- still what account creation/
-- listing/activation read and write (app/services/staff_management.py
-- has no role-awareness of its own; changing that is out of scope
-- here). staff_roles is a parallel structure, dual-written at account
-- creation time from here on and backfilled below for every existing
-- account -- the same dual-write-then-migrate-readers shape M4-M5 used
-- for patients.whatsapp_number/patient_identifiers.
--
-- Permission names are resource-scoped ("patient.create"-shaped, per
-- this work order's own example), derived by reading every current
-- require_role call site -- one permission per resource-area router,
-- not per individual endpoint:
--   department.manage              app/api/departments.py
--   doctor.manage                  app/api/doctors.py, doctor_photo.py
--   doctor_schedule.manage         app/api/doctor_schedule.py
--   doctor_appointment_type.manage app/api/doctor_appointment_types.py
--   appointment_type.manage        app/api/appointment_types.py
--   staff.manage                   app/api/staff_auth.py's account endpoints
--   appointment.waive_payment      app/api/appointments.py's one ADMIN site
--
-- Every endpoint gated only by bare get_current_staff today (no role
-- check at all -- patients.py, dashboard.py, doctor_blocks.py,
-- appointments.py's other endpoints) is deliberately untouched: that's
-- authentication, not a role comparison, so it's outside what "replace
-- role comparisons" asks for here.
--
-- Scope columns on staff_roles: department_id is real (departments
-- already exists). "Unit" from this work order's own text has no table
-- anywhere in this codebase yet, so no unit_id column is added here --
-- one with no FK target would just point at nothing. Add it alongside
-- whatever eventually introduces a unit concept.
CREATE TABLE roles (
    id   BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE permissions (
    id   BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE role_permissions (
    role_id       BIGINT NOT NULL REFERENCES roles(id),
    permission_id BIGINT NOT NULL REFERENCES permissions(id),
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE staff_roles (
    id            BIGSERIAL PRIMARY KEY,
    staff_id      BIGINT NOT NULL REFERENCES staff(id),
    role_id       BIGINT NOT NULL REFERENCES roles(id),
    department_id BIGINT REFERENCES departments(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (staff_id, role_id, department_id)
);

COMMENT ON COLUMN staff_roles.department_id IS
    'NULL means hospital-wide for this role, not "no department" -- e.g. ADMIN today has no department scope at all. A future per-department role (e.g. a department-scoped NURSE) sets this.';

INSERT INTO roles (name) VALUES
    ('ADMIN'), ('STAFF'), ('DOCTOR'), ('NURSE'), ('RECEPTIONIST'), ('LAB_TECH'), ('PHARMACIST'), ('BILLING');

COMMENT ON TABLE roles IS
    'DOCTOR/NURSE/RECEPTIONIST/LAB_TECH/PHARMACIST/BILLING are seeded now, per this work order''s own step 4, but unused -- no staff account holds one of these yet, and no permission is granted to any of them, until their own modules exist.';

INSERT INTO permissions (name) VALUES
    ('department.manage'),
    ('doctor.manage'),
    ('doctor_schedule.manage'),
    ('doctor_appointment_type.manage'),
    ('appointment_type.manage'),
    ('staff.manage'),
    ('appointment.waive_payment');

-- ADMIN gets every permission that exists today -- exactly reproducing
-- every require_role("ADMIN") gate this migration's callers replace.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'ADMIN'), id FROM permissions;

-- STAFF (and the six unused roles) get none of these -- exactly
-- reproducing today's 403 on every one of them for a STAFF session
-- (see tests/test_admin_rbac.py::test_admin_only_writes_reject_staff_role,
-- unchanged by this migration).

-- Backfill: one staff_roles row per existing staff account, mapping
-- their existing staff.role string to the matching role.
INSERT INTO staff_roles (staff_id, role_id)
SELECT s.id, r.id
FROM staff s
JOIN roles r ON r.name = s.role;
