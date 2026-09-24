-- Adds a permanent, human-facing appointment identifier, the same
-- GENERATED ALWAYS AS ... STORED pattern already used for
-- patients.uhid (migrations/0024), appointments.invoice_number
-- (migrations/0026), and invoices.invoice_number/payments.receipt_number
-- (migrations/0033) -- deterministic from appointments.id, so every
-- existing row is backfilled instantly and no application code can ever
-- generate one client-side (master spec "Printing" phase section 22:
-- document identifiers must be generated safely by the backend, never
-- the frontend).
--
-- Format: "APT-00000123" -- a fixed "APT-" prefix plus id zero-padded
-- to 8 digits, matching invoice_number's width. Distinct from the
-- existing invoice_number ("INV-...") on the same table -- two
-- different identifiers for two different documents (the appointment
-- itself vs. its bill), not a rename of one into the other.
ALTER TABLE appointments
    ADD COLUMN appointment_number TEXT GENERATED ALWAYS AS ('APT-' || LPAD(id::text, 8, '0')) STORED;

CREATE UNIQUE INDEX appointments_appointment_number_key ON appointments (appointment_number);

COMMENT ON COLUMN appointments.appointment_number IS
    'Permanent per-appointment identifier for the printable appointment slip, e.g. APT-00000123. Derived from id, never mutated, never reused. Distinct from invoice_number (the bill''s own identifier).';
