-- Three independent, additive, nullable changes for the OPD front-desk
-- workflow phase. None of them touch appointments.status, none of them
-- backfill (every historical row predates all three concepts -- see the
-- same "don't fabricate history" reasoning migrations/0018 already used
-- for payment_status's WAIVED backfill), and none of them change the
-- EXCLUDE constraint or partial indexes from migrations/0002/0003/0011.
--
-- 1. appointments.arrived_at -- physical arrival time, distinct from
--    visited_at (formal check-in, set only by mark_visited_service,
--    unchanged by this migration). A row can be CONFIRMED with
--    arrived_at set and visited_at still NULL: that's "arrived early,
--    not yet checked in." See app/services/appointment_services.py's
--    mark_arrived_service.
--
-- 2. appointments.booking_source -- which channel this appointment was
--    booked through (ONLINE/PHONE/WALK_IN/STAFF_ASSISTED), an
--    appointment-level property, never patient-level (the same patient
--    can have appointments with different sources). NULL means
--    "predates this column," not "unknown online/phone/etc" -- no
--    value is invented for existing rows.
--
-- 3. patients.date_of_birth / patients.gender -- optional patient
--    demographics. Both nullable, neither required by any existing
--    business rule; registration stays name+phone-only unless staff
--    choose to add these.

ALTER TABLE appointments
    ADD COLUMN arrived_at TIMESTAMPTZ,
    ADD COLUMN booking_source TEXT
        CHECK (booking_source IN ('ONLINE', 'PHONE', 'WALK_IN', 'STAFF_ASSISTED'));

COMMENT ON COLUMN appointments.arrived_at IS
    'Physical arrival time, set by mark_arrived_service. Distinct from visited_at (formal check-in). NULL until the patient is marked arrived.';
COMMENT ON COLUMN appointments.booking_source IS
    'One of: ONLINE, PHONE, WALK_IN, STAFF_ASSISTED. NULL for appointments created before this column existed -- never backfilled/guessed.';

ALTER TABLE patients
    ADD COLUMN date_of_birth DATE,
    ADD COLUMN gender TEXT
        CHECK (gender IN ('MALE', 'FEMALE', 'OTHER'));

COMMENT ON COLUMN patients.date_of_birth IS
    'Optional. Never required by registration -- name and whatsapp_number remain the only mandatory fields.';
COMMENT ON COLUMN patients.gender IS
    'Optional, one of: MALE, FEMALE, OTHER. Never required by registration.';
