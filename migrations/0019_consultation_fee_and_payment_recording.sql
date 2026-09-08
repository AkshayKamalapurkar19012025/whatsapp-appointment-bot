-- Phase 3 of the patient arrival workflow: consultation charges and
-- payment recording.
--
-- consultation_fee lives on doctor_appointment_types (not a new
-- pricing table) -- that table already maps (doctor_id,
-- appointment_type_id) -> duration_minutes at exactly the granularity
-- a fee also needs. Defaults to 0 for every existing row: an honest
-- "no price configured yet", not a fabricated placeholder fee (the
-- application must never hard-code a rupee amount -- see
-- get_consultation_charge_service). An admin sets real prices through
-- the existing assign/update endpoints for this table, now extended
-- to accept consultation_fee alongside duration_minutes.
--
-- The five new appointments columns record what Section 8 of the
-- workflow spec requires for a payment: amount, method, who recorded
-- it, and when. All nullable with no backfill (unlike
-- migrations/0018's payment_status backfill) -- NULL is simply true
-- for every historical row: no method or amount was ever recorded
-- under the old flow, so there is nothing dishonest about leaving them
-- unset, unlike payment_status where UNPAID/PAID would have actively
-- misrepresented history.

ALTER TABLE doctor_appointment_types
    ADD COLUMN consultation_fee NUMERIC(10, 2) NOT NULL DEFAULT 0
        CHECK (consultation_fee >= 0);

COMMENT ON COLUMN doctor_appointment_types.consultation_fee IS
    'The fee charged for this doctor/appointment-type combination. Defaults to 0 (not configured) -- never a fabricated default price.';

ALTER TABLE appointments
    ADD COLUMN payment_method TEXT
        CHECK (payment_method IN ('CASH', 'UPI', 'CARD', 'OTHER')),
    ADD COLUMN payment_amount NUMERIC(10, 2),
    ADD COLUMN payment_recorded_by BIGINT REFERENCES staff(id),
    ADD COLUMN payment_recorded_at TIMESTAMPTZ,
    ADD COLUMN waive_reason TEXT;

COMMENT ON COLUMN appointments.payment_method IS
    'One of CASH, UPI, CARD, OTHER. NULL until a payment attempt is recorded.';
COMMENT ON COLUMN appointments.payment_amount IS
    'Amount actually charged (frozen at the time payment_status was set to PAID/FAILED) or 0 for WAIVED. NULL until recorded.';
COMMENT ON COLUMN appointments.payment_recorded_by IS
    'Staff member who recorded the payment or waiver.';
COMMENT ON COLUMN appointments.payment_recorded_at IS
    'When the payment or waiver was recorded.';
COMMENT ON COLUMN appointments.waive_reason IS
    'Required staff-entered reason when payment_status = WAIVED (see waive_consultation_fee_service''s 7-day-revisit eligibility rule). NULL otherwise.';
