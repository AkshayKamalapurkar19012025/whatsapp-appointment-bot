-- Track which staff account added each doctor, alongside the existing
-- doctors.created_at (present since the 0001 baseline).
--
-- Nullable: existing doctor rows predate this column and there is no
-- record of who created them, so they stay NULL ("unknown") rather than
-- being backfilled to a guessed value. ON DELETE SET NULL rather than
-- CASCADE -- there is no staff-deletion endpoint today (staff accounts
-- are only ever deactivated, see app/services/staff_management.py), but
-- if one is ever added, removing a staff account must not silently
-- delete the doctors they created.

ALTER TABLE doctors
    ADD COLUMN created_by BIGINT REFERENCES staff(id) ON DELETE SET NULL;
