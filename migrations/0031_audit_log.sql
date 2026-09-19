-- P1.b (HospitalOS build plan): audit log. A single, generic table
-- recording who did what, to which resource, and when -- wired into
-- every mutation P1.a's RBAC decomposition gated behind
-- require_permission(...), plus break-glass grant/review
-- (migrations/0030 already called for "someone auditing break-glass
-- usage" -- this is that mechanism, generalized to the rest of the
-- RBAC-gated surface rather than built as a break-glass-only table).
--
-- Deliberately NOT retrofitted with a DEFAULT 1 hospital_id the way
-- migrations/0024's nine existing tables were -- that default existed
-- purely so already-populated tables didn't need a backfill statement.
-- This table starts empty; every INSERT from here on supplies
-- hospital_id explicitly from the acting staff member's own session
-- (see app/services/staff_auth.py's get_current_staff, which already
-- resolves it), same as any other genuinely new table would.
--
-- staff_id is nullable, not because any current call site can omit it
-- (every write this logs requires an authenticated staff session), but
-- because a future system-initiated action (a cron job, a migration
-- script) would have no staff account to attribute -- ON DELETE is
-- deliberately left as the default (NO ACTION/RESTRICT) rather than SET
-- NULL: a staff account is deactivated, never deleted, in this schema,
-- so that path never triggers in practice.
--
-- resource_id is nullable for the same reason it's untyped beyond
-- BIGINT: every action logged today has exactly one numeric primary-key
-- resource, but this stays generic rather than assuming that holds
-- forever.
CREATE TABLE audit_log (
    id            BIGSERIAL PRIMARY KEY,
    hospital_id   BIGINT NOT NULL REFERENCES hospitals(id),
    staff_id      BIGINT REFERENCES staff(id),
    action        TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id   BIGINT,
    details       JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE audit_log IS
    'One row per RBAC-gated mutation (see app/services/audit_log.py''s record_audit_log, called from every require_permission(...)-gated write endpoint and from break-glass grant/review). Append-only -- nothing in this application ever updates or deletes a row here.';

CREATE INDEX audit_log_hospital_created_idx ON audit_log (hospital_id, created_at DESC);
CREATE INDEX audit_log_staff_idx ON audit_log (staff_id);
CREATE INDEX audit_log_resource_idx ON audit_log (resource_type, resource_id);
