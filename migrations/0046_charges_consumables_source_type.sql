-- OPD/HIMS master spec audit "subsequent gaps" list (section 38-42):
-- "Consumables specifically has no distinct source type (falls under
-- OTHER) -- minor gap against the spec's exact list." Same
-- DROP/ADD CONSTRAINT precedent migrations/0038_packages.sql already
-- used to add PACKAGE to this same list.
ALTER TABLE charges DROP CONSTRAINT charges_source_type_check;
ALTER TABLE charges ADD CONSTRAINT charges_source_type_check CHECK (
    source_type IN ('CONSULTATION', 'LAB', 'RADIOLOGY', 'PROCEDURE', 'SERVICE', 'PHARMACY', 'PACKAGE', 'CONSUMABLES', 'OTHER')
);
