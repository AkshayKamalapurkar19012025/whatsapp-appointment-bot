-- Two additive, nullable changes to the walk-in queue (migrations/0012),
-- both scoped to a ticketed (token_number IS NOT NULL) CHECKED_IN
-- appointment -- see app/services/appointment_services.py's
-- hold_queue_entry_service/recall_queue_entry_service/set_priority_service.
--
-- 1. queue_held_at -- front desk can skip a ticketed patient who's
--    stepped away without losing their place in line. GET /doctors/{id}/
--    queue (app/api/doctors.py) excludes a held entry from now_serving/
--    waiting while it's set, without ever renumbering token_number --
--    recalling (clearing this column) resumes the patient at their
--    original spot, not the back of the queue.
--
-- 2. is_priority/priority_reason/priority_set_by/priority_set_at -- staff
--    can flag a ticketed patient's entry as priority (medical emergency,
--    senior citizen, doctor's request, ...); get_doctor_queue calls
--    priority entries to the front ahead of earlier token numbers,
--    again without renumbering anyone. Turning priority on always
--    requires a reason (enforced in set_priority_service, not here --
--    matches this codebase's existing convention of business rules
--    living in the service layer, not DB CHECK constraints). Turning it
--    off leaves the last reason/who/when in place as history rather
--    than clearing it, so the audit trail survives the flag itself.

ALTER TABLE appointments
    ADD COLUMN queue_held_at TIMESTAMPTZ,
    ADD COLUMN is_priority BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN priority_reason TEXT,
    ADD COLUMN priority_set_by BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    ADD COLUMN priority_set_at TIMESTAMPTZ;

COMMENT ON COLUMN appointments.queue_held_at IS
    'Set by hold_queue_entry_service when front desk skips a ticketed patient who stepped away. NULL means not held (the normal case). Cleared by recall_queue_entry_service, which does not change token_number.';
COMMENT ON COLUMN appointments.is_priority IS
    'Set by set_priority_service. TRUE moves this ticketed entry ahead of earlier token numbers in get_doctor_queue''s serving order -- token_number itself is never changed.';
COMMENT ON COLUMN appointments.priority_reason IS
    'Required when is_priority is set TRUE (enforced in set_priority_service). Left in place after priority is turned back off, as the audit record of why it was ever set.';
COMMENT ON COLUMN appointments.priority_set_by IS
    'Staff account that last set is_priority TRUE. Left in place after priority is turned back off, same as priority_reason.';
COMMENT ON COLUMN appointments.priority_set_at IS
    'When priority_set_by last set is_priority TRUE. Left in place after priority is turned back off, same as priority_reason.';
