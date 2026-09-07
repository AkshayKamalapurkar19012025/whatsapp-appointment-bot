-- Two additions to the appointment status lifecycle, layered on top of
-- the existing PENDING/CONFIRMED/REJECTED approval stage from
-- migrations/0011 -- that stage is untouched by this migration.
--
-- 1. VISITED -> CHECKED_IN: a pure naming rename, no semantic change.
--    Confirmed by reading every place VISITED was compared
--    (mark_visited_service, the doctor walk-in queue endpoint, the
--    dashboard stats query) -- it always meant exactly "patient
--    physically checked in, in the queue". Internal identifiers
--    (mark_visited_service, the /visit endpoint, the visited_at
--    column, visitAdminAppointment() in the frontend) are deliberately
--    left unchanged -- only the stored status value and user-facing
--    labels change.
--
-- 2. NO_SHOW: a new terminal status, set only by a manual front-desk
--    action (no cron/scheduler -- see mark_no_show_service) from
--    CONFIRMED only. Deliberately not reachable from CHECKED_IN (a
--    checked-in patient is physically present, so "no-show" is a
--    contradiction) or PENDING (an unapproved request that expired is
--    a different, out-of-scope problem, not a no-show).
--
-- Neither change touches the EXCLUDE constraint or the two partial
-- indexes from migrations/0002/0003/0011: their predicates only ever
-- reference CANCELLED/REJECTED (release set) and PENDING/CONFIRMED
-- (actionable set) -- VISITED was never in either set, and NO_SHOW
-- doesn't need to be added to either (a no-show is always a past
-- appointment, so it can never affect a future overlap/availability
-- check).

UPDATE appointments SET status = 'CHECKED_IN' WHERE status = 'VISITED';

COMMENT ON COLUMN appointments.status IS
    'One of: PENDING, CONFIRMED, REJECTED, CANCELLED, CHECKED_IN, COMPLETED, NO_SHOW.';
