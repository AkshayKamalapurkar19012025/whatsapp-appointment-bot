-- Performance indexes for the query patterns every double-booking check
-- and upcoming-appointments lookup already uses. Purely additive: no
-- columns, constraints, or existing data are touched, and none of these
-- indexes change what any query returns -- only how fast it's found.
--
-- Partial indexes (WHERE clauses matching the app's own filters) keep
-- them small and match the exact predicates already used in
-- app/api/appointments.py, app/api/availability.py, and
-- app/api/booking.py.

-- Overlap checks filter appointments by doctor_id + time range and
-- always exclude CANCELLED rows.
CREATE INDEX idx_appointments_doctor_time
    ON appointments (doctor_id, start_at, end_at)
    WHERE status <> 'CANCELLED';

-- get_upcoming_booked_appointments() (app/api/booking.py) filters by
-- patient_id, status = 'BOOKED', start_at > NOW().
CREATE INDEX idx_appointments_patient_upcoming
    ON appointments (patient_id, start_at)
    WHERE status = 'BOOKED';

-- Doctor block overlap checks filter by doctor_id + time range and
-- always exclude inactive blocks.
CREATE INDEX idx_doctor_blocks_doctor_time
    ON doctor_blocks (doctor_id, start_at, end_at)
    WHERE active = TRUE;
