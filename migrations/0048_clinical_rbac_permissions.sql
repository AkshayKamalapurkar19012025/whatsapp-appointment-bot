-- Deepens RBAC enforcement (master spec audit Principle 5) beyond the
-- three actions migrations/0043_role_based_access.sql already gated
-- (pharmacy.manage_stock, the bill.*/appointment.*_payment set,
-- consultation.amend). Those left every other clinical write --
-- vitals, the initial consultation save/complete, orders,
-- prescriptions -- on bare get_current_staff, deliberately, "since no
-- existing gate covers their domain yet."
--
-- Four new permissions, one per core clinical-documentation action:
--   vitals.record        -- POST /appointments/{id}/vitals
--   consultation.write   -- PUT .../consultation, POST .../consultation/complete
--   order.create         -- POST .../orders
--   prescription.create  -- POST .../prescription/items, POST .../prescription/prescribe
--
-- Unlike migrations/0043's own grants, this explicitly widens the
-- generalist STAFF role to hold all four alongside NURSE/DOCTOR, not
-- just ADMIN: STAFF stays a full-access fallback rather than a forced
-- migration off it the moment these gates land, so an existing
-- deployment with only plain STAFF accounts (the norm until the six
-- specific roles existed at all) keeps working unchanged today. The
-- six specific roles are additive, not a replacement requirement.
INSERT INTO permissions (name) VALUES
    ('vitals.record'),
    ('consultation.write'),
    ('order.create'),
    ('prescription.create');

-- ADMIN gets every permission that exists -- these are brand new, so
-- (unlike consultation.amend/pharmacy.manage_stock/bill.* above, each
-- already granted to ADMIN by whichever earlier migration first
-- defined it) ADMIN needs its own explicit grant here too.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'ADMIN'), id
FROM permissions
WHERE name IN ('vitals.record', 'consultation.write', 'order.create', 'prescription.create');

-- STAFF: all four, per this migration's own docstring above.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'STAFF'), id
FROM permissions
WHERE name IN ('vitals.record', 'consultation.write', 'order.create', 'prescription.create');

-- NURSE: triage is their job -- vitals only, not consultation/orders/
-- prescriptions (a doctor's clinical decisions).
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'NURSE'), id
FROM permissions
WHERE name = 'vitals.record';

-- DOCTOR: all four -- a doctor can record their own patient's vitals
-- (common in a small clinic without a separate triage step) in
-- addition to the consultation/orders/prescriptions that are
-- specifically theirs.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'DOCTOR'), id
FROM permissions
WHERE name IN ('vitals.record', 'consultation.write', 'order.create', 'prescription.create');

-- RECEPTIONIST/LAB_TECH/PHARMACIST/BILLING deliberately get none of
-- these -- none of the four is that role's job (same "no permission
-- row unless the role's real-world duties call for it" discipline
-- migrations/0043 already established for NURSE/RECEPTIONIST/LAB_TECH
-- there).
