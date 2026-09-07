-- Renames the last "booking"-named surfaces in the schema to
-- "scheduling", matching the same patient-facing terminology change
-- already made to the frontend/backend copy (no payment is collected;
-- this is a scheduling flow, not a commercial booking).
--
-- booking_sessions holds live WhatsApp conversation state (which step a
-- patient is on mid-chat) -- ALTER TABLE ... RENAME preserves the data,
-- all existing rows, and the identity sequence; it just changes the
-- name. Verified directly against a running instance (BEGIN; ALTER
-- TABLE ... RENAME TO; \d; ROLLBACK;) that Postgres does NOT auto-rename
-- a table's constraints/indexes along with it -- every constraint below
-- is renamed explicitly, or it would keep saying "booking_sessions_..."
-- forever despite the table itself no longer being named that.

ALTER TABLE booking_sessions RENAME TO scheduling_sessions;
ALTER TABLE scheduling_sessions RENAME COLUMN booking_mode TO scheduling_mode;

ALTER TABLE scheduling_sessions RENAME CONSTRAINT booking_sessions_pkey TO scheduling_sessions_pkey;
ALTER TABLE scheduling_sessions RENAME CONSTRAINT booking_sessions_whatsapp_number_key TO scheduling_sessions_whatsapp_number_key;
ALTER TABLE scheduling_sessions RENAME CONSTRAINT booking_sessions_patient_id_fkey TO scheduling_sessions_patient_id_fkey;
ALTER TABLE scheduling_sessions RENAME CONSTRAINT booking_sessions_department_id_fkey TO scheduling_sessions_department_id_fkey;
ALTER TABLE scheduling_sessions RENAME CONSTRAINT booking_sessions_doctor_id_fkey TO scheduling_sessions_doctor_id_fkey;
ALTER TABLE scheduling_sessions RENAME CONSTRAINT booking_sessions_appointment_type_id_fkey TO scheduling_sessions_appointment_type_id_fkey;
ALTER TABLE scheduling_sessions RENAME CONSTRAINT booking_sessions_selected_appointment_id_fkey TO scheduling_sessions_selected_appointment_id_fkey;

COMMENT ON COLUMN scheduling_sessions.scheduling_mode IS
    'Which entry mode (Doctor-First vs Date-First) this in-progress WhatsApp scheduling conversation is using -- NULL until a mode is chosen, reset to NULL like every other scheduling_sessions column whenever the conversation restarts.';
