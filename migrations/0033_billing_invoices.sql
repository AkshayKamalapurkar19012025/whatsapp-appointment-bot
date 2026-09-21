-- OPD/HIMS master spec Phase 9 (Billing + Payment).
--
-- Scope decision, made explicit here because it's the one that matters
-- most: this is a NEW, encounter-scoped invoice/charge/payment system,
-- deliberately separate from -- and non-invasive to -- the existing
-- appointments.consultation_fee/payment_status/payment_amount
-- mechanism (migrations/0018/0019/0025/0026) and its two callers
-- (record_payment_service, waive_consultation_fee_service in
-- app/services/appointment_services.py). That existing mechanism is
-- the single most concurrency-sensitive, heavily-tested path in this
-- codebase -- generate_queue_token_service, the actual "patient enters
-- the queue" trigger, fires directly off it. Rewriting it to absorb
-- orders/pharmacy charges (what Phases 6-8 left unbilled, per those
-- phases' own reports) was considered and rejected: it would mean
-- touching the one payment path whose regression blast radius is "the
-- queue silently stops issuing tokens," for a benefit (one unified
-- ledger instead of two) that a later, dedicated phase can deliver more
-- safely once this additive layer has proven itself. This migration
-- gets Phase 9's actual exit criterion -- "Encounter -> Charges -> Bill
-- -> Payment -> Receipt works reliably" -- for everything Phases 6-8
-- generated (lab/radiology/procedure orders, pharmacy dispenses) and
-- any ad-hoc charge, without touching the consultation-fee path at all.
--
-- invoice_number/receipt_number use a visually DISTINCT prefix ("BILL-"
-- /"RCPT2-") from the existing appointments.invoice_number ("INV-") on
-- purpose -- two different systems producing similar-looking numbers
-- for different things would be a real, avoidable confusion for staff
-- reading a screen, not just a cosmetic nit.
--
-- Tax is a per-invoice, staff-set rate (invoices.tax_rate), not a
-- hardcoded constant anywhere in application code -- master spec
-- section 38's explicit requirement. A real hospital-wide default rate
-- (vs. per-invoice override) is a configuration-system question this
-- migration doesn't answer; it only makes sure no code path invents a
-- number.
--
-- Packages (master spec section 39) and full insurance/TPA claim
-- modeling (section 40) are explicitly NOT built here -- the master
-- spec itself defers packages to "a later billing phase if not already
-- available", and asks only for insurance to be an *extension point*,
-- not a real claims system. charges.source_type reserves room for a
-- future PACKAGE type without a schema change; payments.method
-- including INSURANCE is that extension point for payer type -- policy
-- number, pre-auth, co-pay, and claim tracking are real future work,
-- not simulated with empty columns here.

CREATE TABLE invoices (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    encounter_id        BIGINT NOT NULL UNIQUE REFERENCES encounters(id),
    invoice_number      TEXT GENERATED ALWAYS AS ('BILL-' || LPAD(id::text, 8, '0')) STORED,
    discount_amount     NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    discount_reason     TEXT,
    -- Percentage, e.g. 18.00 for 18% GST -- applied to (gross -
    -- discount) at read time (get_invoice_summary_service), never
    -- stored as a computed amount, so changing the rate before payment
    -- is a one-column update, not a recomputation across rows.
    tax_rate            NUMERIC(5, 2) NOT NULL DEFAULT 0 CHECK (tax_rate >= 0 AND tax_rate <= 100),
    status              TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'VOID')),
    voided_by           BIGINT REFERENCES staff(id),
    void_reason         TEXT,
    voided_at           TIMESTAMPTZ,
    created_by          BIGINT NOT NULL REFERENCES staff(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((status = 'VOID') = (voided_at IS NOT NULL))
);

CREATE UNIQUE INDEX invoices_invoice_number_key ON invoices (invoice_number);

COMMENT ON TABLE invoices IS
    'One row per encounter -- the header (discount/tax terms) for that encounter''s new-model bill. See app/services/billing_services.py. Deliberately independent of appointments.payment_status; see this migration''s own header for why.';

CREATE TABLE charges (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id          BIGINT NOT NULL REFERENCES invoices(id),
    description         TEXT NOT NULL,
    amount              NUMERIC(10, 2) NOT NULL CHECK (amount > 0),
    source_type         TEXT NOT NULL DEFAULT 'OTHER'
        CHECK (source_type IN ('CONSULTATION', 'LAB', 'RADIOLOGY', 'PROCEDURE', 'SERVICE', 'PHARMACY', 'OTHER')),
    -- At most one of these two, and at most one charge per source (the
    -- two partial unique indexes below) -- a given order or dispense
    -- gets billed once, never duplicated. Both NULL for a plain ad-hoc
    -- charge (e.g. a consumable, a walk-in procedure fee).
    source_order_id     BIGINT REFERENCES orders(id),
    source_dispense_id  BIGINT REFERENCES pharmacy_dispense_records(id),
    status              TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'VOIDED')),
    voided_by           BIGINT REFERENCES staff(id),
    void_reason         TEXT,
    voided_at           TIMESTAMPTZ,
    created_by          BIGINT NOT NULL REFERENCES staff(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (source_order_id IS NULL OR source_dispense_id IS NULL),
    CHECK ((status = 'VOIDED') = (voided_at IS NOT NULL))
);

CREATE INDEX charges_invoice_id_idx ON charges (invoice_id);
CREATE UNIQUE INDEX charges_source_order_unique ON charges (source_order_id) WHERE source_order_id IS NOT NULL;
CREATE UNIQUE INDEX charges_source_dispense_unique ON charges (source_dispense_id) WHERE source_dispense_id IS NOT NULL;

COMMENT ON TABLE charges IS
    'Line items on the new-model invoice. Never deleted -- a mistaken charge is VOIDED with a reason (master spec section 70: no casual deletion of financial records), which excludes it from the invoice total but keeps the row as an audit record.';

CREATE TABLE payments (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id          BIGINT NOT NULL REFERENCES invoices(id),
    receipt_number      TEXT GENERATED ALWAYS AS ('RCPT2-' || LPAD(id::text, 8, '0')) STORED,
    amount              NUMERIC(10, 2) NOT NULL CHECK (amount > 0),
    method              TEXT NOT NULL CHECK (method IN ('CASH', 'UPI', 'CARD', 'BANK_TRANSFER', 'INSURANCE', 'OTHER')),
    -- A UPI/card/bank reference -- NULL for cash. The partial unique
    -- index below is the real "prevent duplicate payments" mechanism
    -- master spec section 41 asks for, for every method that actually
    -- has an external reference to de-duplicate against; cash has none
    -- to check, same as in the real world.
    transaction_id      TEXT,
    status              TEXT NOT NULL DEFAULT 'COMPLETED' CHECK (status IN ('COMPLETED', 'VOIDED')),
    refunded_amount     NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK (refunded_amount >= 0),
    refund_reason       TEXT,
    refunded_by         BIGINT REFERENCES staff(id),
    refunded_at         TIMESTAMPTZ,
    voided_by           BIGINT REFERENCES staff(id),
    void_reason         TEXT,
    voided_at           TIMESTAMPTZ,
    recorded_by         BIGINT NOT NULL REFERENCES staff(id),
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (refunded_amount <= amount),
    CHECK ((status = 'VOIDED') = (voided_at IS NOT NULL))
);

CREATE INDEX payments_invoice_id_idx ON payments (invoice_id);
CREATE UNIQUE INDEX payments_transaction_id_unique ON payments (transaction_id) WHERE transaction_id IS NOT NULL;

COMMENT ON TABLE payments IS
    'One row per payment received against an invoice -- multiple rows support genuine partial payment over time (master spec section 41). status/refunded_amount/voided_* record correction after the fact without ever deleting the original row -- see app/services/billing_services.py for the balance computation these feed (gross - discount + tax - SUM(effective paid)).';
