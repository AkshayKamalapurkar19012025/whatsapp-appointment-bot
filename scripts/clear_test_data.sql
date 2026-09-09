-- Wipes all doctor/patient/appointment/department test data from a
-- local database, leaving staff/staff_sessions (and the schema itself)
-- untouched so you can still log in afterward.
--
-- One multi-table TRUNCATE: every table below is either independent
-- (mock_sms_outbox, patient_otp_codes) or only references other tables
-- in this same list, so no CASCADE is needed -- Postgres just requires
-- that any FK-referencing table be truncated in the same statement,
-- which this is.
--
-- Usage: psql -U <your DB_USER> -d <your DB_NAME> -f scripts/clear_test_data.sql

TRUNCATE TABLE
    mock_sms_outbox,
    patient_otp_codes,
    patient_sessions,
    booking_sessions,
    appointments,
    doctor_blocks,
    doctor_schedule,
    doctor_appointment_types,
    doctor_departments,
    doctor_education,
    patients,
    doctors,
    appointment_types,
    departments
RESTART IDENTITY;
