-- WEB P10 (security pass): add idle-activity tracking to both session
-- tables, on top of the absolute expires_at cap that has existed since
-- WEB P2/P5. A stolen-but-unused token currently stays valid for the
-- full 24-hour absolute TTL regardless of activity -- flagged as a P10
-- candidate in both the P2 and P3 reports, not a gap introduced here.
--
-- The application layer (app/services/patient_auth.py,
-- app/services/staff_auth.py) advances last_seen_at on every successful
-- token lookup, and separately checks it against a shorter idle-timeout
-- window before honoring a token -- both the absolute expires_at cap and
-- the idle check apply; whichever is stricter for a given session wins.
--
-- Added nullable first, backfilled explicitly to each row's own
-- created_at (not NOW()) so a session issued long before this migration
-- doesn't read as having just been used, then locked to NOT NULL with a
-- DEFAULT for every session created from here on.

ALTER TABLE patient_sessions ADD COLUMN last_seen_at TIMESTAMPTZ;
UPDATE patient_sessions SET last_seen_at = created_at WHERE last_seen_at IS NULL;
ALTER TABLE patient_sessions ALTER COLUMN last_seen_at SET NOT NULL;
ALTER TABLE patient_sessions ALTER COLUMN last_seen_at SET DEFAULT NOW();

ALTER TABLE staff_sessions ADD COLUMN last_seen_at TIMESTAMPTZ;
UPDATE staff_sessions SET last_seen_at = created_at WHERE last_seen_at IS NULL;
ALTER TABLE staff_sessions ALTER COLUMN last_seen_at SET NOT NULL;
ALTER TABLE staff_sessions ALTER COLUMN last_seen_at SET DEFAULT NOW();
