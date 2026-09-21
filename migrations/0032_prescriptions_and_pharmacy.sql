-- OPD/HIMS master spec Phase 8 (Prescription + Pharmacy). Four new
-- tables, all additive.
--
-- prescriptions/prescription_items follow the same shape as
-- consultations (migrations/0029): one prescription per encounter,
-- DRAFT while the doctor is writing it, PRESCRIBED once signed and
-- sent to pharmacy, CANCELLED as the one other exit -- no amendment
-- workflow once PRESCRIBED, same stance as every other clinical record
-- in this schema.
--
-- pharmacy_stock/pharmacy_dispense_records are the "stock-aware
-- dispensing where available" half (master spec section 37): stock is
-- OPTIONAL (a prescription item can be dispensed with no matching
-- batch at all -- module degradation, same principle master spec
-- section 68 applies to a disabled module generally), but when a
-- dispense *does* reference a stock batch, it must be the same
-- medicine (enforced in app/services/pharmacy_services.py, not a DB
-- constraint -- there's no shared medicine catalog/FK to check against,
-- only two free-text names to compare) -- this is what "do not allow
-- unauthorized substitution" (section 36) actually means in a schema
-- with no substitution feature to begin with: dispensing against the
-- wrong stock row is prevented, not just "not offered as a button".
--
-- pharmacy_stock.quantity_on_hand is denormalized (current balance),
-- kept correct by pharmacy_dispense_records (the movement/transaction
-- log) -- the same denormalized-current-value-plus-ledger pattern this
-- repo already uses (ipd-service's beds.status, appointments.
-- token_number's own append-only-then-summarized shape). "Do not
-- simply mark medication dispensed without a transaction" (master spec
-- section 37) is exactly this: every dispense is a row here, not just
-- a status flip.
--
-- Deliberately NOT wired into the existing appointments.payment_status/
-- invoice_line_items billing flow: add_invoice_line_item_service only
-- accepts new charges while payment_status is UNPAID/FAILED (migrations/
-- 0026's own stated invariant -- the bill freezes once paid/waived), but
-- dispensing routinely happens after the consultation fee is already
-- settled (payment happens at check-in, before the doctor is even
-- seen; pharmacy is the last stop). Forcing pharmacy charges through
-- that mechanism would 409 in the common case, not the exception.
-- pharmacy_dispense_records.amount is a real, persisted financial fact
-- either way -- reconciling it into one unified invoice is Phase 9's
-- job (a real multi-charge invoice model that isn't frozen on first
-- payment), not forced prematurely onto today's single-fee model.

CREATE TABLE prescriptions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    encounter_id    BIGINT NOT NULL UNIQUE REFERENCES encounters(id),
    doctor_id       BIGINT NOT NULL REFERENCES doctors(id),
    status          TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'PRESCRIBED', 'CANCELLED')),
    notes           TEXT,
    prescribed_at   TIMESTAMPTZ,
    cancelled_by    BIGINT REFERENCES staff(id),
    cancel_reason   TEXT,
    cancelled_at    TIMESTAMPTZ,
    created_by      BIGINT NOT NULL REFERENCES staff(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- prescribed_at is set exactly when status is no longer DRAFT --
    -- CANCELLED only ever follows PRESCRIBED (cancel_prescription_
    -- service refuses to cancel a DRAFT), so prescribed_at stays set
    -- (and non-NULL) as the historical record of when it happened,
    -- even after cancellation. cancelled_at is its own independent
    -- flag, set iff CANCELLED.
    CHECK ((status = 'DRAFT') = (prescribed_at IS NULL)),
    CHECK ((status = 'CANCELLED') = (cancelled_at IS NOT NULL))
);

COMMENT ON TABLE prescriptions IS
    'One row per encounter -- see app/services/pharmacy_services.py. DRAFT/PRESCRIBED/CANCELLED only; "Partially Dispensed"/"Dispensed" (master spec section 35) are computed from prescription_items, never stored here, so they can never drift out of sync with the actual dispense records.';

CREATE TABLE prescription_items (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prescription_id       BIGINT NOT NULL REFERENCES prescriptions(id),
    medicine_name         TEXT NOT NULL,
    generic_name          TEXT,
    dosage                TEXT,
    route                 TEXT,
    frequency             TEXT,
    duration              TEXT,
    quantity              INTEGER NOT NULL CHECK (quantity > 0),
    -- Denormalized running total -- see header note on the
    -- denormalized-current-value-plus-ledger pattern. Kept in lockstep
    -- with pharmacy_dispense_records by record_dispense_service, in the
    -- same transaction as the INSERT there.
    quantity_dispensed    INTEGER NOT NULL DEFAULT 0 CHECK (quantity_dispensed >= 0),
    food_instructions     TEXT,
    special_instructions  TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (quantity_dispensed <= quantity)
);

CREATE INDEX prescription_items_prescription_id_idx ON prescription_items (prescription_id);

CREATE TABLE pharmacy_stock (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    medicine_name     TEXT NOT NULL,
    batch_number      TEXT NOT NULL,
    expiry_date       DATE NOT NULL,
    quantity_on_hand  INTEGER NOT NULL CHECK (quantity_on_hand >= 0),
    unit_price        NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    created_by        BIGINT NOT NULL REFERENCES staff(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (medicine_name, batch_number)
);

-- The lookup a dispense screen actually needs: "what batches of this
-- medicine still have stock". A new batch is a new row (restocking),
-- never an edit of an old one -- matches this repo's append-heavy
-- convention for anything with a real-world paper trail.
CREATE INDEX pharmacy_stock_available_idx ON pharmacy_stock (medicine_name)
    WHERE active AND quantity_on_hand > 0;

CREATE TABLE pharmacy_dispense_records (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prescription_item_id  BIGINT NOT NULL REFERENCES prescription_items(id),
    -- NULL when dispensed with no matching stock batch on hand --
    -- "stock-aware... where available" (master spec section 37); the
    -- dispense still happens and is still a real transaction, just
    -- without an inventory decrement behind it.
    pharmacy_stock_id     BIGINT REFERENCES pharmacy_stock(id),
    quantity              INTEGER NOT NULL CHECK (quantity > 0),
    unit_price            NUMERIC(10, 2) NOT NULL DEFAULT 0,
    amount                NUMERIC(10, 2) NOT NULL DEFAULT 0,
    dispensed_by          BIGINT NOT NULL REFERENCES staff(id),
    dispensed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX pharmacy_dispense_records_item_idx ON pharmacy_dispense_records (prescription_item_id);

COMMENT ON TABLE pharmacy_dispense_records IS
    'One row per dispense action -- the transaction/movement log record_dispense_service (app/services/pharmacy_services.py) writes alongside incrementing prescription_items.quantity_dispensed and (when pharmacy_stock_id is set) decrementing pharmacy_stock.quantity_on_hand, all in the same DB transaction.';
