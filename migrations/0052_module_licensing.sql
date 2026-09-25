-- OPD/HIMS master spec section 67 (MODULE LICENSING / ENABLEMENT) and
-- section 68 (MODULE DEGRADATION): separates Licensed (a platform-level
-- entitlement -- what a hospital's contract actually covers) from
-- Enabled (the hospital admin's own on/off switch, but only within
-- what's licensed) from Available (the derived runtime state neither
-- column stores directly: licensed AND enabled). The CHECK constraint
-- below is the actual enforcement of the spec's "hospital admin must
-- NOT be able to self-grant paid modules" -- a DB-level guarantee, not
-- just an application-code check that a future call site could bypass.
--
-- Scoped to the three modules with a real, safe "turn it off" story
-- (see each module's own gating in app/services/order_services.py,
-- pharmacy_services.py, and billing_services.py): LAB_RADIOLOGY
-- (degrades to EXTERNAL -- External Referral, already a first-class
-- order type, was built for exactly this), PHARMACY (degrades to
-- BLOCKED), PACKAGES (degrades to HIDDEN). Billing/scheduling/
-- consultation aren't optional modules in this app -- there's no safe
-- "disable core visit documentation" story, so they're out of scope.

CREATE TABLE hospital_modules (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hospital_id  BIGINT NOT NULL REFERENCES hospitals(id),
    module_key   TEXT NOT NULL CHECK (module_key IN ('LAB_RADIOLOGY', 'PHARMACY', 'PACKAGES')),
    licensed     BOOLEAN NOT NULL DEFAULT FALSE,
    enabled      BOOLEAN NOT NULL DEFAULT FALSE,
    -- Deliberately no REFERENCES staff(id) here (unlike audit_log's own
    -- staff_id fkey): this table has to behave like hospitals/roles/
    -- permissions -- seeded once, read by every test in the run -- but
    -- tests/conftest.py's per-test TRUNCATE ... CASCADE on `staff`
    -- would cascade-truncate any table with a hard FK to it, wiping
    -- this one's seed data after the first test. A plain id (no FK)
    -- still records who made the change; it just can't be enforced at
    -- the DB layer the way hospitals-scoped rows normally would be.
    updated_by   BIGINT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hospital_id, module_key),
    CHECK (enabled = FALSE OR licensed = TRUE)
);

COMMENT ON TABLE hospital_modules IS
    'One row per (hospital, module) once either its license or its enabled state has ever been set -- a missing row means "never licensed", the same as an explicit licensed=FALSE row. licensed is set by the platform-licensing screen (ADMIN, app/api/module_licensing.py); enabled is set by the hospital-admin module-enablement screen, same role today since this app has no separate platform-owner role yet (master spec: "platform-level entitlement controls should exist later").';

-- Every existing hospital keeps today's actual behavior (all three
-- modules always on) as its *default* going forward -- this migration
-- must never silently take Lab/Pharmacy/Packages away from a hospital
-- that was already using them.
INSERT INTO hospital_modules (hospital_id, module_key, licensed, enabled)
SELECT h.id, m.key, TRUE, TRUE
FROM hospitals h
CROSS JOIN (VALUES ('LAB_RADIOLOGY'), ('PHARMACY'), ('PACKAGES')) AS m(key);
