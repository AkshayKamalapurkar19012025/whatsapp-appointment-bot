-- OPD/HIMS master spec Phase 12: packages (section 39).
--
-- migrations/0033_billing_invoices.sql's own header already flagged
-- this: "Packages (master spec section 39)... explicitly NOT built
-- here -- the master spec itself defers packages to 'a later billing
-- phase if not already available'" and reserved room for it
-- ("charges.source_type reserves room for a future PACKAGE type
-- without a schema change"). This is that later phase.
--
-- A package is a hospital's own priced catalog entry -- "Health
-- Checkup Basic", "Antenatal Package", whatever a hospital bundles and
-- sells as one line item -- billed as a single charge rather than
-- retyped every time. Deliberately NOT a bundle of individually-
-- tracked component charges: section 39's own text is "Package
-- pricing should not duplicate individual services incorrectly",
-- i.e. don't ALSO bill the package's component services separately,
-- not "track which orders a package's price was supposed to cover".
-- A package charge and an order/dispense charge already can't both
-- exist for the same clinical event (there's no link between a
-- package charge and an order at all), so double-billing simply isn't
-- representable -- no reconciliation logic needed to prevent it.
--
-- hospital_id is real (not exempted) -- same as appointment_types and
-- departments, packages is a hospital-owned catalog table with no
-- parent row to derive tenancy from.
CREATE TABLE packages (
    id          BIGSERIAL PRIMARY KEY,
    hospital_id BIGINT NOT NULL REFERENCES hospitals(id),
    name        TEXT NOT NULL,
    description TEXT,
    price       NUMERIC(10, 2) NOT NULL CHECK (price > 0),
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_by  BIGINT NOT NULL REFERENCES staff(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hospital_id, name)
);

COMMENT ON TABLE packages IS
    'A hospital''s priced package catalog (master spec section 39) -- billed as a single charges.source_type = PACKAGE line item, see app/services/package_services.py. Soft-deleted via active, same convention as appointment_types/departments -- never hard-deleted once a charge may reference it.';

ALTER TABLE charges ADD COLUMN source_package_id BIGINT REFERENCES packages(id);

-- Same "at most one source" invariant migrations/0033 established for
-- source_order_id/source_dispense_id, extended to the new third
-- option -- a package charge, an order charge, and a dispense charge
-- are three different things and a charge is at most one of them.
-- Unlike those two, source_package_id gets no uniqueness index: a
-- package is a reusable catalog entry, not a single clinical event --
-- the same package can legitimately be billed to many different
-- invoices (or, in principle, more than once on the same invoice).
ALTER TABLE charges DROP CONSTRAINT charges_check;
ALTER TABLE charges ADD CONSTRAINT charges_source_exclusive_check CHECK (
    (source_order_id IS NOT NULL)::int
    + (source_dispense_id IS NOT NULL)::int
    + (source_package_id IS NOT NULL)::int <= 1
);

ALTER TABLE charges DROP CONSTRAINT charges_source_type_check;
ALTER TABLE charges ADD CONSTRAINT charges_source_type_check CHECK (
    source_type IN ('CONSULTATION', 'LAB', 'RADIOLOGY', 'PROCEDURE', 'SERVICE', 'PHARMACY', 'PACKAGE', 'OTHER')
);

INSERT INTO permissions (name) VALUES ('package.manage');

INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'ADMIN'), id
FROM permissions
WHERE name = 'package.manage';
