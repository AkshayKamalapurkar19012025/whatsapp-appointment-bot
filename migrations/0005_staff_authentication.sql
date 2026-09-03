-- Staff/admin authentication + RBAC (WEB P5): username + password login for
-- staff and admin accounts, mirroring migrations/0004's patient session
-- design. Purely additive -- two new tables, no changes to any existing
-- table.
--
-- staff: one row per staff/admin account. password_hash is an argon2
-- hash (see app/services/staff_auth.py), never plaintext. role is
-- constrained to the two roles from the RBAC design
-- (docs/WEB_EXPANSION_ARCHITECTURE.md section 8): ADMIN and STAFF.
-- failed_login_count/locked_until implement the same brute-force
-- defense pattern as patient_otp_codes.attempt_count, adapted to a
-- password-login context (temporary lockout after repeated failures,
-- rather than a per-code attempt cap).
--
-- staff_sessions: opaque bearer session tokens, hashed the same way as
-- patient_sessions, for the same reason (a DB read alone must never
-- hand out something directly usable to log in). Deliberately a
-- separate table from patient_sessions, not a shared table with a
-- subject_type discriminator -- staff and patient are different subject
-- types with different login mechanisms (password vs OTP) and must
-- never be authenticatable through each other's session table.

CREATE TABLE staff (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username            TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    role                TEXT NOT NULL CHECK (role IN ('ADMIN', 'STAFF')),
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    failed_login_count  INTEGER NOT NULL DEFAULT 0,
    locked_until        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE staff_sessions (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    staff_id    BIGINT NOT NULL REFERENCES staff(id),
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ
);

CREATE INDEX idx_staff_sessions_staff
    ON staff_sessions (staff_id);
