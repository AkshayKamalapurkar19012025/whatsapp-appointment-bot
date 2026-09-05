-- Expands appointments.status from the two-value {BOOKED, CANCELLED}
-- model to a real lifecycle: PENDING, CONFIRMED, REJECTED, CANCELLED,
-- VISITED, COMPLETED.
--
-- New appointments (WhatsApp, patient web booking, and admin booking on
-- a patient's behalf) now start PENDING and require a staff member to
-- Confirm or Reject them (app/api/appointments.py's new
-- /confirm, /reject, /visit, /complete endpoints). Existing BOOKED rows
-- were already, in effect, confirmed appointments -- staff had already
-- implicitly accepted them under the old two-state model -- so they are
-- migrated to CONFIRMED, not PENDING, to avoid dumping every historical
-- appointment into a new confirmation queue.
--
-- "Counts as occupying the slot" (the predicate used by every overlap
-- check, the EXCLUDE constraint, and the two partial indexes below) was
-- `status <> 'CANCELLED'` under the old model. It becomes
-- `status NOT IN ('CANCELLED', 'REJECTED')`: a Rejected request never
-- happened, exactly like a Cancelled one, so it must release the slot
-- the same way. PENDING is deliberately still in the "occupying" set --
-- per product decision, a Pending request holds its slot while awaiting
-- confirmation so two patients can't be offered (and race for) the same
-- time.

UPDATE appointments SET status = 'CONFIRMED' WHERE status = 'BOOKED';

ALTER TABLE appointments ALTER COLUMN status SET DEFAULT 'PENDING';

COMMENT ON COLUMN appointments.status IS
    'One of: PENDING, CONFIRMED, REJECTED, CANCELLED, VISITED, COMPLETED.';

-- Exclusion constraint (migrations/0003): same shape, new predicate.
ALTER TABLE appointments DROP CONSTRAINT no_overlapping_booked_appointments;

ALTER TABLE appointments
    ADD CONSTRAINT no_overlapping_booked_appointments
    EXCLUDE USING gist (
        doctor_id WITH =,
        tstzrange(start_at, end_at, '[)') WITH &&
    )
    WHERE (status NOT IN ('CANCELLED', 'REJECTED'));

-- Partial indexes (migrations/0002): same shape, new predicates.
DROP INDEX idx_appointments_doctor_time;

CREATE INDEX idx_appointments_doctor_time
    ON appointments (doctor_id, start_at, end_at)
    WHERE status NOT IN ('CANCELLED', 'REJECTED');

DROP INDEX idx_appointments_patient_upcoming;

CREATE INDEX idx_appointments_patient_upcoming
    ON appointments (patient_id, start_at)
    WHERE status IN ('PENDING', 'CONFIRMED');
