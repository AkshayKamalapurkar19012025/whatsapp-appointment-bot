-- Adds a permanent, human-facing hospital patient identifier (UHID),
-- distinct from patients.whatsapp_number -- see the OPD Patient Search
-- & Registration redesign report for why: whatsapp_number carries the
-- table's actual UNIQUE constraint today (the WhatsApp bot's patient
-- resolution, app/api/scheduling.py's get_patient, depends on it
-- staying exactly that), but a mobile number is a contact/search
-- attribute, not what a hospital should treat as a patient's permanent
-- identity. This migration does NOT touch that constraint or the
-- WhatsApp bot's resolution logic at all -- it only adds a new,
-- additive, always-present identity column.
--
-- GENERATED ALWAYS AS ... STORED (not a separate sequence, not
-- application-generated) -- deterministic from patients.id, which is
-- already a permanent, never-reused, monotonically increasing
-- identity column (BIGINT GENERATED ALWAYS AS IDENTITY). This makes
-- uhid trivially backfilled for every existing row (no data migration
-- script, no race with concurrent inserts) and physically incapable of
-- drifting out of sync with id.
--
-- Format: "HOS-0000123" -- a fixed "HOS-" prefix plus id zero-padded
-- to 7 digits (comfortably covers a hospital's expected patient volume
-- while staying a fixed width for as long as that holds; ids beyond
-- 7 digits still work, they just widen the number instead of
-- truncating -- LPAD never drops digits).
ALTER TABLE patients
    ADD COLUMN uhid TEXT GENERATED ALWAYS AS ('HOS-' || LPAD(id::text, 7, '0')) STORED;

CREATE UNIQUE INDEX patients_uhid_key ON patients (uhid);

COMMENT ON COLUMN patients.uhid IS
    'Permanent hospital patient identifier, e.g. HOS-0000123. Derived from id, never mutated, never reused. This -- not whatsapp_number -- is a patient''s real identity; whatsapp_number remains a separate, unique contact/search attribute.';
