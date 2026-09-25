-- Two new permissions for the module licensing/enablement screens
-- (app/api/module_licensing.py, migrations/0052's own schema): kept as
-- two distinct permissions, not one, even though both are ADMIN-only
-- today, because the spec itself separates these into two different
-- levels of authority -- module.manage_license is the platform-level
-- action, module.manage_enablement is the hospital-admin action (see
-- migrations/0052's header and "platform-level entitlement controls
-- should exist later"). When a real platform-owner role eventually
-- exists, only module.manage_license needs to move to it; keeping them
-- separate now means that's a role_permissions change, not a new
-- migration touching the permission set itself.
--
-- Neither is granted to STAFF -- module licensing/enablement is at
-- least as sensitive as pharmacy.manage_stock/bill.*/consultation.
-- amend, all of which already exclude STAFF (migrations/0043's own
-- "STAFF loses the admin-tier actions" pattern), unlike the four
-- clinical-documentation gates (migrations/0048) STAFF deliberately
-- kept as a full-access fallback.
INSERT INTO permissions (name) VALUES
    ('module.manage_license'),
    ('module.manage_enablement');

INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'ADMIN'), id
FROM permissions
WHERE name IN ('module.manage_license', 'module.manage_enablement');
