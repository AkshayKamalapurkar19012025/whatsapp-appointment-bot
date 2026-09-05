-- Lets a doctor's recurring weekly schedule differ by department. Until
-- now doctor_schedule was keyed only by doctor_id/day_of_week -- a
-- doctor in two departments (via doctor_departments) had one combined
-- set of hours no matter which department a patient found them
-- through.
--
-- Nullable, additive: NULL means "applies regardless of department"
-- (both the meaning for every existing row, and the default for a new
-- row that doesn't need department-specific hours), so nothing already
-- stored changes behavior. A non-NULL value scopes that one row to a
-- single department -- app/api/doctor_schedule.py's create/update
-- handlers enforce it must be one the doctor is actually assigned to
-- via doctor_departments (a plain CHECK can't do that cross-table
-- lookup).
--
-- Overlap prevention (schedule_overlaps() in app/api/doctor_schedule.py)
-- is deliberately NOT relaxed by department: a doctor can only be in
-- one place at a time, so two rows for the same doctor/day with
-- overlapping time ranges are still rejected even if tagged to
-- different departments.
--
-- ON DELETE SET NULL rather than CASCADE or RESTRICT: deleting a
-- department a doctor's schedule row references should widen that
-- row back to "applies regardless of department" rather than silently
-- deleting the doctor's working hours or blocking the department
-- deletion.

ALTER TABLE doctor_schedule
    ADD COLUMN department_id BIGINT REFERENCES departments(id) ON DELETE SET NULL;

CREATE INDEX idx_doctor_schedule_department ON doctor_schedule (department_id);
