-- Closes the RBAC gap in issue #133. migrations/0048 gated the two
-- *creating* prescription endpoints behind prescription.create, but
-- three mutating prescription/pharmacy endpoints were left on bare
-- get_current_staff, which performs no authorization at all:
--
--   DELETE .../prescription/items/{item_id}   -- removing a drafted line
--   POST   .../prescription/cancel            -- cancelling a signed prescription
--   POST   /pharmacy/items/{item_id}/dispense -- handing the drug over
--
-- Verified before this migration: a LAB_TECH account (no prescription
-- or pharmacy permission of any kind) was refused 403 when adding a
-- prescription item, but succeeded with 200 on all three of the above
-- -- including cancelling an entire PRESCRIBED prescription and
-- dispensing against it.
--
-- Two new permissions, not three. Removing a line from a DRAFT
-- prescription is the same authority as adding one -- the prescription
-- is not signed off yet, so it reuses the existing prescription.create
-- rather than inventing a permission for half of one editing action.
-- The other two are genuinely distinct acts and get their own, in
-- keeping with how this schema already separates a reversing or
-- escalated action from the write it reverses (consultation.amend vs
-- consultation.write; bill.void/void_charge/void_payment vs
-- bill.add_charge; order.collect/result/verify/release vs
-- order.create):
--
--   prescription.cancel  -- cancels a PRESCRIBED prescription, after
--                           sign-off, which prescription.create's
--                           holders can do but which is not "drafting"
--   pharmacy.dispense    -- the physical act of dispensing, which is
--                           not the prescriber's job
INSERT INTO permissions (name) VALUES
    ('prescription.cancel'),
    ('pharmacy.dispense');

-- ADMIN gets every permission that exists; both are brand new, so both
-- need an explicit grant here.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'ADMIN'), id
FROM permissions
WHERE name IN ('prescription.cancel', 'pharmacy.dispense');

-- STAFF keeps its full-access-fallback role, exactly as migrations/0048
-- argued: an existing deployment running on plain STAFF accounts must
-- not lose the ability to cancel or dispense the moment these gates
-- land. The six specific roles stay additive, not a forced migration.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'STAFF'), id
FROM permissions
WHERE name IN ('prescription.cancel', 'pharmacy.dispense');

-- DOCTOR: cancels prescriptions they signed (prescription.create is
-- already theirs per 0048). Deliberately NOT pharmacy.dispense -- a
-- prescriber is not the dispenser, and collapsing the two would remove
-- the separation of duties the pharmacy queue exists to enforce.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'DOCTOR'), id
FROM permissions
WHERE name = 'prescription.cancel';

-- PHARMACIST: dispensing is the job. Already holds
-- pharmacy.manage_stock from migrations/0043; this adds the act that
-- stock management exists to serve. Deliberately NOT
-- prescription.cancel -- a pharmacist who cannot dispense an item
-- raises it with the prescriber rather than voiding their
-- prescription.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'PHARMACIST'), id
FROM permissions
WHERE name = 'pharmacy.dispense';

-- NURSE: dispenses too. A nurse handing medication to a patient is
-- routine in the 100+ bed multi-speciality hospitals this system
-- targets, and in smaller units there is often no separate pharmacist
-- on every shift at all -- the same "common in a small clinic without
-- a separate step" reasoning migrations/0048 used to give DOCTOR
-- vitals.record rather than reserving it for NURSE. Withholding this
-- would not enforce a separation of duties, it would just stop nurses
-- doing a job they already do. Deliberately NOT prescription.cancel:
-- voiding a doctor's signed prescription is a prescriber's decision,
-- and NOT pharmacy.manage_stock, which stays PHARMACIST/ADMIN --
-- dispensing against stock is not the same authority as adjusting it.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'NURSE'), id
FROM permissions
WHERE name = 'pharmacy.dispense';

-- RECEPTIONIST/LAB_TECH/BILLING get neither, per the same "no
-- permission row unless the role's real-world duties call for it"
-- discipline migrations/0043 and 0048 established. LAB_TECH holding
-- either of these is the specific defect #133 reports.
