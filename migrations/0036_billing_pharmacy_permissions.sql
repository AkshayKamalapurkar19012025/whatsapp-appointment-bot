-- Reconciling this branch's Phase 9 billing (app/api/billing.py) and
-- Phase 8 pharmacy stock intake (app/api/pharmacy.py) with main's
-- independently shipped RBAC decomposition
-- (migrations/0031_rbac_decomposition.sql) -- same situation, and same
-- resolution, as migrations/0034_appointment_billing_permissions.sql:
-- both routers were built against require_role("ADMIN") directly, since
-- require_permission() didn't exist yet on this line of development.
-- Neither is a new business rule (every one of these was already
-- ADMIN-only); this just gives each its own permission name instead of
-- a bare role string comparison, one per distinct admin action (the
-- same fine-grained precedent 0034 set for appointment.
-- add_invoice_line_item/refund_payment), not one blanket permission for
-- the whole router.
--
-- "bill" is the resource name here, not "invoice" -- the new invoice/
-- charge/payment model is deliberately named /bill... in the API to
-- avoid colliding with the OLD invoice endpoints 0034's permissions
-- already cover (see app/api/billing.py's own module docstring).
INSERT INTO permissions (name) VALUES
    ('bill.update_terms'),
    ('bill.void'),
    ('bill.add_charge'),
    ('bill.void_charge'),
    ('bill.void_payment'),
    ('bill.refund_payment'),
    ('pharmacy.manage_stock');

INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'ADMIN'), id
FROM permissions
WHERE name IN (
    'bill.update_terms',
    'bill.void',
    'bill.add_charge',
    'bill.void_charge',
    'bill.void_payment',
    'bill.refund_payment',
    'pharmacy.manage_stock'
);
