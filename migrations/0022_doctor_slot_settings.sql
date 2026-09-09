-- Per-doctor slot-generation preferences (Doctor workspace Schedule tab's
-- "Slot settings" panel). Purely additive, both with safe defaults that
-- preserve existing behavior for every doctor created before this
-- migration:
--
-- default_duration_minutes: only used to pre-fill a new doctor/
-- appointment-type assignment's duration and to size the doctor-wide
-- "Generated slots preview" (Schedule tab), which isn't tied to any one
-- appointment type. It does NOT change how real booking slots are
-- computed -- that still comes from doctor_appointment_types.
-- duration_minutes per appointment type (see
-- app/services/availability_engine.get_appointment_type_for_doctor),
-- so existing bookings/availability are unaffected.
--
-- buffer_minutes: a gap inserted after each generated slot candidate in
-- get_available_slots (app/services/availability_engine.py), so
-- back-to-back bookings leave the configured breathing room. Defaults
-- to 0, i.e. exactly the pre-migration behavior (slots immediately
-- adjacent), for every existing doctor.
ALTER TABLE doctors
    ADD COLUMN default_duration_minutes INT NOT NULL DEFAULT 30,
    ADD COLUMN buffer_minutes INT NOT NULL DEFAULT 0;
