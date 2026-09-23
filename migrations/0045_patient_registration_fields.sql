-- OPD/HIMS master spec audit "subsequent gaps" list (section 16-18):
-- registration form fields beyond name/mobile/DOB/gender -- email,
-- alternate mobile, address (city/state/PIN), emergency contact, and
-- blood group -- had no column at all. This was an explicit, documented
-- design choice (fast walk-in registration stays minimal), not an
-- oversight, and stays that way here: every column below is nullable,
-- nothing becomes newly required, and existing rows are untouched.
ALTER TABLE patients
    ADD COLUMN email TEXT,
    ADD COLUMN alternate_whatsapp_number TEXT,
    ADD COLUMN address_line TEXT,
    ADD COLUMN city TEXT,
    ADD COLUMN state TEXT,
    ADD COLUMN pincode TEXT,
    ADD COLUMN emergency_contact_name TEXT,
    ADD COLUMN emergency_contact_phone TEXT,
    ADD COLUMN blood_group TEXT
        CHECK (blood_group IN ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'));

COMMENT ON COLUMN patients.email IS 'Optional. Never required by registration.';
COMMENT ON COLUMN patients.alternate_whatsapp_number IS
    'Optional secondary contact number -- distinct from whatsapp_number, which stays the one identifier used for lookup/dedup/login. Free text, not validated or normalized the way whatsapp_number is (app/utils/phone.py) -- it is contact information only, never resolved through app/services/patient_identifiers.py.';
COMMENT ON COLUMN patients.pincode IS 'Optional, free text (not validated as a specific country''s postal code format).';
COMMENT ON COLUMN patients.blood_group IS 'Optional, one of the eight standard ABO/Rh groups.';
