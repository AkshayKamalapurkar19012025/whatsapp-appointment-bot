-- Itemized OPD billing: lets front desk/admin add ad-hoc charges on top
-- of the consultation fee (e.g. a dressing charge, a minor procedure
-- done during the same visit) -- the one gap left after migrations
-- 0018/0019/0025 gave a visit exactly one fixed charge
-- (doctor_appointment_types.consultation_fee).
--
-- Deliberately additive, not a replacement for that single-fee model:
-- appointments.payment_amount/payment_status/the whole check-in ->
-- payment -> queue-token flow is untouched and keeps working exactly as
-- it does today for the common case (no extra line items). record_
-- payment_service now charges consultation_fee PLUS the sum of this
-- appointment's invoice_line_items -- see that function's updated
-- docstring. This avoids introducing a second, parallel "amount owed"
-- concept (an invoice header row with its own status that would have
-- to be kept in lockstep with payment_status) for what is still, at
-- settlement time, one single payment against one appointment.
--
-- invoice_number is a permanent, always-present identifier for every
-- appointment's bill/receipt -- same derivation pattern as migrations/
-- 0024_patient_uhid.sql's uhid: GENERATED ALWAYS AS ... STORED from
-- appointments.id, not a separate sequence or application-generated
-- value, so it's trivially backfilled and can never drift out of sync.

ALTER TABLE appointments
    ADD COLUMN invoice_number TEXT GENERATED ALWAYS AS ('INV-' || LPAD(id::text, 8, '0')) STORED;

CREATE UNIQUE INDEX appointments_invoice_number_key ON appointments (invoice_number);

COMMENT ON COLUMN appointments.invoice_number IS
    'Permanent per-appointment invoice/receipt identifier, e.g. INV-00000123. Derived from id, never mutated, never reused.';

CREATE TABLE invoice_line_items (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    appointment_id  BIGINT NOT NULL REFERENCES appointments(id),
    description     TEXT NOT NULL,
    amount          NUMERIC(10, 2) NOT NULL CHECK (amount > 0),
    added_by        BIGINT NOT NULL REFERENCES staff(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX invoice_line_items_appointment_id_idx ON invoice_line_items (appointment_id);

COMMENT ON TABLE invoice_line_items IS
    'Ad-hoc charges added on top of an appointment''s consultation_fee. Only addable while the appointment''s payment_status is UNPAID/FAILED (see add_invoice_line_item_service) -- once PAID/WAIVED/REFUNDED, the bill is frozen, same invariant payment_amount already relies on.';
