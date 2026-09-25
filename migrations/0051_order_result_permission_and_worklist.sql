-- Building the Lab/Radiology Worklist screen (one of the large,
-- previously-deferred items) surfaces a real actor for order-result
-- entry -- LAB_TECH -- for the first time (migrations/0031/0043/0048
-- all seeded that role but never granted it a single permission row).
-- Result entry (POST .../orders/{id}/result) has stayed on bare
-- get_current_staff since migrations/0048 deliberately left it out of
-- scope ("every other endpoint here... stays on bare get_current_staff,
-- out of scope for that migration" -- app/api/orders.py's own
-- docstring). Now that a role exists whose actual job this is, gating
-- it properly is the same "close the gap once a real actor needs it"
-- discipline 0048 itself established for vitals/consultation/orders/
-- prescriptions.
--
-- Cancellation (POST .../orders/{id}/cancel) and listing stay on bare
-- get_current_staff -- out of scope here, same as before; this
-- migration only closes the gate this session's new worklist actually
-- needs.
INSERT INTO permissions (name) VALUES ('order.result');

-- ADMIN: brand new permission, needs its own explicit grant.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'ADMIN'), id
FROM permissions WHERE name = 'order.result';

-- STAFF: full-access fallback, same as every other clinical-
-- documentation gate this session has added (migrations/0048).
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'STAFF'), id
FROM permissions WHERE name = 'order.result';

-- DOCTOR: already sees and orders these from ConsultationWorkspace;
-- keeps being able to record a result themselves there too.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'DOCTOR'), id
FROM permissions WHERE name = 'order.result';

-- LAB_TECH: the actual point -- the role this worklist screen is for.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'LAB_TECH'), id
FROM permissions WHERE name = 'order.result';

-- NURSE/RECEPTIONIST/PHARMACIST/BILLING deliberately get none of this
-- -- not their job, same discipline as every prior RBAC migration.
