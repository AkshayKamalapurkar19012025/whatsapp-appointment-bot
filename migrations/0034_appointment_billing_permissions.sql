-- Reconciling this branch's P1.a RBAC decomposition
-- (migrations/0031_rbac_decomposition.sql) with main's independently
-- shipped OPD billing/invoicing work: main added two more ADMIN-only
-- appointment actions -- add an ad-hoc invoice line item, record a
-- refund -- via require_role("ADMIN") directly, since P1.a's
-- require_permission() didn't exist yet on that line of development.
-- Neither is a new business rule (both were already ADMIN-only); this
-- just gives them their own permission names instead of leaving one
-- lone require_role("ADMIN") sitting next to nine require_permission(...)
-- calls in the same router.
INSERT INTO permissions (name) VALUES
    ('appointment.add_invoice_line_item'),
    ('appointment.refund_payment');

INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'ADMIN'), id
FROM permissions
WHERE name IN ('appointment.add_invoice_line_item', 'appointment.refund_payment');
