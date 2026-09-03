-- Recurring doctor schedule, date-ranged (WEB P7). Closes WEB P0 gap
-- analysis item #4: a doctor_schedule row previously had no way to say
-- "this weekly schedule applies only from date X to date Y" -- it was
-- either permanent or toggled off entirely via `active`. Purely
-- additive: two new nullable columns, no backfill needed -- NULL means
-- open-ended (unbounded) on that side, so every existing row (both
-- NULL) keeps its current "applies forever" meaning unchanged.
--
-- Resolves WEB P0 open item #7 (date-range semantics): a new row is
-- allowed to coexist with an existing one for the same doctor/day only
-- if their time ranges don't overlap OR their date ranges don't
-- overlap -- there is no separate "override" precedence concept. This
-- is a direct extension of the overlap check doctor_schedule.py's
-- schedule_overlaps() already enforced before this migration (which
-- only compared time ranges, implicitly treating every row as
-- permanent); see that function's updated version for the exact
-- interval-overlap logic.

ALTER TABLE doctor_schedule
    ADD COLUMN start_date DATE,
    ADD COLUMN end_date DATE;
