-- Corrects migrations/0019's column comment: the clinic's actual
-- waiver policy is a 3-day revisit window, not 7 (see
-- waive_consultation_fee_service and app/services/exceptions.py's
-- WaiverNotEligible, both updated to match). No schema/data change --
-- migrations/0019 is already applied, so its own comment text can't be
-- edited in place; this just brings the documentation in line with the
-- enforced rule.

COMMENT ON COLUMN appointments.waive_reason IS
    'Required staff-entered reason when payment_status = WAIVED (see waive_consultation_fee_service''s 3-day-revisit eligibility rule). NULL otherwise.';
