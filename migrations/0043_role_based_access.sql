-- OPD/HIMS master spec audit gap #3: "Role-based work is schema-only,
-- not real" -- DOCTOR/NURSE/RECEPTIONIST/LAB_TECH/PHARMACIST/BILLING
-- were seeded into `roles` by migrations/0031_rbac_decomposition.sql
-- ("seeded now... but unused -- no staff account holds one of these
-- yet"), but two things actually blocked ever using them:
--
-- 1. staff.role's own CHECK constraint (migrations/0005) only allowed
--    'ADMIN'/'STAFF' -- creating an account with any other role string
--    failed at the database layer before staff_roles was ever reached.
-- 2. Every one of these roles held zero rows in role_permissions, so
--    even past that, an account holding one could do nothing beyond
--    what bare authentication already grants any STAFF account.
--
-- This migration fixes both, narrowly. For (1): a CHECK constraint
-- can't reference another table in Postgres, so this replaces it with
-- a foreign key to roles.name (already UNIQUE) instead of hand-listing
-- the eight role strings a second time -- a future ninth role only
-- needs an INSERT into roles, never another migration touching this
-- constraint. For (2): grants each new role the subset of
-- *already-existing* admin-tier permissions that matches its
-- real-world duties. Nothing here changes what ADMIN or STAFF can do,
-- and no currently-bare-auth endpoint (vitals, orders, prescriptions,
-- routine payment collection -- see 0031's own docstring for why those
-- were deliberately left ungated) becomes newly restricted:
-- NURSE/RECEPTIONIST/LAB_TECH get no extra permission rows because no
-- existing gate covers their domain yet, so an account holding one of
-- those roles today has exactly STAFF's own access, just under its
-- real job title -- consistent with everything else in this app never
-- restricting a bare-auth endpoint by role.
ALTER TABLE staff DROP CONSTRAINT staff_role_check;
ALTER TABLE staff ADD CONSTRAINT staff_role_fkey
    FOREIGN KEY (role) REFERENCES roles(name) NOT VALID;
-- NOT VALID + a manual VALIDATE keeps this migration from holding a
-- blocking lock while it re-checks every existing row; validating
-- immediately after, in the same migration, still catches any
-- pre-existing bad data synchronously rather than leaving it
-- unenforced indefinitely.
ALTER TABLE staff VALIDATE CONSTRAINT staff_role_fkey;

-- DOCTOR: corrects their own completed consultation notes (master spec
-- section 70's controlled amendment flow, migrations/0041).
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'DOCTOR'), id
FROM permissions
WHERE name = 'consultation.amend';

-- PHARMACIST: adjusts pharmacy stock levels (intake, correction).
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'PHARMACIST'), id
FROM permissions
WHERE name = 'pharmacy.manage_stock';

-- BILLING: every admin-tier billing/invoice action across both the
-- old invoice model (migrations/0034) and the current bill/charge/
-- payment model (migrations/0036) -- voiding, waiving, refunding, and
-- adjusting terms or line items are exactly this role's job.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'BILLING'), id
FROM permissions
WHERE name IN (
    'bill.update_terms',
    'bill.void',
    'bill.add_charge',
    'bill.void_charge',
    'bill.void_payment',
    'bill.refund_payment',
    'appointment.add_invoice_line_item',
    'appointment.waive_payment',
    'appointment.refund_payment'
);
