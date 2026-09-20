-- P1.a (HospitalOS build plan), step 5: break-glass -- an endpoint
-- granting temporary scope override with a mandatory reason, writing an
-- audit row flagged for review. Grants immediately: refusing an
-- emergency clinician is the more dangerous failure than a permission
-- used outside its normal grant for a few minutes.
--
-- No approval step before the grant takes effect -- reviewed_at/
-- reviewed_by_staff_id are for the *after the fact* review this work
-- order calls for, never a gate on the grant itself.
CREATE TABLE break_glass_grants (
    id                    BIGSERIAL PRIMARY KEY,
    staff_id              BIGINT NOT NULL REFERENCES staff(id),
    permission_name       TEXT NOT NULL,
    reason                TEXT NOT NULL,
    granted_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at            TIMESTAMPTZ NOT NULL,
    reviewed_at           TIMESTAMPTZ,
    reviewed_by_staff_id  BIGINT REFERENCES staff(id)
);

COMMENT ON TABLE break_glass_grants IS
    'An active (expires_at > NOW()), non-reviewed-yet row here grants permission_name to staff_id immediately, checked by app/api/staff_auth.py''s require_permission alongside the normal staff_roles path. reviewed_at/reviewed_by_staff_id are filled in later, by someone auditing break-glass usage -- never a precondition for the grant itself.';

CREATE INDEX break_glass_grants_active_idx
    ON break_glass_grants (staff_id, permission_name, expires_at);
