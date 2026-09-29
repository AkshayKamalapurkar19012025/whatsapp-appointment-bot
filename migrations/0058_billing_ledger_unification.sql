-- Phase 10 (Billing Ledger Unification): makes the new-model invoice/
-- charge/payment system (migrations/0033) the single write path for
-- consultation-fee payments going forward, ending the two-ledger split
-- migrations/0033's own header named and deliberately deferred.
--
-- Schema only -- no data is touched here (see migrations/0059 for the
-- historical backfill). Three additive changes:
--
-- 1. appointments.consultation_payment_id: a plain cross-reference to
--    the payments row that satisfied THIS appointment's consultation
--    fee, once it's paid via the new ledger. This is NOT a financial-
--    truth column the way payment_status/payment_amount are -- it's
--    pure plumbing, needed because payments has no charge_id/order-
--    style link back to what it paid for (a payment is only ever tied
--    to its invoice as a whole, never to one specific charge). Without
--    this column, "is this appointment's consultation fee already
--    paid" and "which payment does a consultation-fee refund apply to"
--    would both have to be guessed at from amounts/ordering once an
--    invoice carries more than one charge type (a real case: billing
--    has no CHECKED_IN gate, so a lab charge can already exist on the
--    same invoice before the consultation fee is ever paid). Nullable,
--    and stays NULL forever for a waived visit (see point 3) or any
--    appointment that predates this migration and was never backfilled
--    a ledger-2 payment (migrations/0059 decides which historical rows
--    get one).
--
-- 2. charges_one_consultation_per_invoice: the same "prevent double-
--    billing the same source" pattern charges_source_order_unique/
--    charges_source_dispense_unique already use for LAB/RADIOLOGY/
--    PROCEDURE/PHARMACY charges (migrations/0033), applied to
--    CONSULTATION. A consultation charge has no order/dispense row to
--    hang a source_*_id FK off -- its real "source" is the encounter
--    itself, which invoices.encounter_id is already UNIQUE on, so "at
--    most one ACTIVE CONSULTATION charge per invoice" is the exact
--    equivalent constraint without inventing a new column.
--
-- 3. bill.record_payment: closes the one RBAC gap in the existing
--    bill.*/appointment.* permission set (migrations/0036/0043) --
--    POST /appointments/{id}/bill/payments and POST /appointments/{id}/
--    payment are today the only billing-mutating endpoints gated by
--    bare get_current_staff instead of require_permission(...). This
--    was a DELIBERATE choice, not an oversight -- migrations/0031's own
--    docstring groups "routine payment collection" with vitals/orders/
--    prescriptions as endpoints intentionally left bare-auth, and
--    migrations/0043 reaffirms it by name. This migration doesn't
--    reverse that judgment -- it makes the SAME access pattern
--    explicit and revocable instead of implicit: the permission is
--    granted to every existing role (not just ADMIN/BILLING the way
--    bill.add_charge/void/refund are), because today literally any
--    authenticated staff account can record a payment, and "preserve
--    current behavior" means exactly that grant, not a narrower one a
--    future phase might choose deliberately. Narrowing this is a real
--    product decision for a later phase, not something to guess here.
ALTER TABLE appointments ADD COLUMN consultation_payment_id BIGINT REFERENCES payments(id);

CREATE UNIQUE INDEX charges_one_consultation_per_invoice
    ON charges (invoice_id)
    WHERE source_type = 'CONSULTATION' AND status = 'ACTIVE';

INSERT INTO permissions (name) VALUES ('bill.record_payment');

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permissions p
WHERE p.name = 'bill.record_payment';
