-- OPD/HIMS interoperability master prompt, Phase 5: Medication Master
-- Data. Closes the specific Phase 3 finding
-- (docs/OPD_HIMS_STANDARDS_READINESS.md S5): prescription_items.
-- medicine_name and pharmacy_stock.medicine_name are two independent
-- free-text fields with no shared identity -- confirmed still true by
-- re-reading app/services/pharmacy_services.py before writing this
-- migration (record_dispense_service's own MedicineMismatch guard is a
-- fragile case-insensitive string-equality check, the only thing
-- linking them today).
--
-- Scope discipline: this migration adds one new table and two nullable,
-- additive FK columns. It does not touch medicine_name/generic_name on
-- either existing table, does not add SNOMED/RxNorm/any external
-- terminology code column (docs/OPD_HIMS_STANDARDS_READINESS.md S16 --
-- only add those "if the Phase 3 design explicitly requires them," and
-- it doesn't), and does not attempt fuzzy clinical-equivalence matching
-- in the backfill below -- see that backfill's own comment for exactly
-- what "confident match" means here.

CREATE TABLE medications (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- The one required field, same "one clearly required identity
    -- field, everything else optional" convention as patient_
    -- allergies.allergen (migrations/0042) and consultations.diagnosis
    -- (migrations/0029).
    generic_name    TEXT NOT NULL CHECK (generic_name = btrim(generic_name) AND generic_name <> ''),
    brand_name      TEXT,
    -- Free text on purpose (e.g. "500mg") -- no unit parsing/UCUM
    -- binding here, per docs/OPD_HIMS_STANDARDS_READINESS.md S5's own
    -- "strength: nullable, free text initially" design; a future
    -- terminology phase can add a coded strength/unit pair alongside
    -- this without touching it.
    strength        TEXT,
    -- e.g. "Tablet", "Syrup", "Injection" -- free text, same reasoning.
    dosage_form     TEXT,
    -- A sensible default for this medication, not a constraint on what
    -- a prescriber picks per prescription_items row -- that column
    -- (already real, migrations/0032) stays independent and always
    -- wins for what actually gets prescribed.
    default_route   TEXT,
    -- Prefer/deactivate over delete, matching every other master table
    -- in this schema (departments.active, appointment_types.active) --
    -- never destructively removable once referenced, see the FK
    -- comments below.
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    -- Nullable: rows created by this migration's own backfill (below)
    -- have no real member of staff who "created" them -- they're
    -- derived from pre-existing free text, not a deliberate admin
    -- action, and inventing an attribution would misrepresent the
    -- historical record.
    created_by      BIGINT REFERENCES staff(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Duplicate prevention "where practical" (this migration's own
-- instruction) -- an exact, case/whitespace-insensitive match across
-- every identifying field. Two rows that differ in strength or form are
-- correctly two different medications (Paracetamol 500mg Tablet vs.
-- Paracetamol 650mg Tablet are not the same clinical entity); this only
-- blocks a literal re-entry of the same one. COALESCE treats a NULL
-- brand/strength/form as equivalent to another NULL for this purpose
-- (plain UNIQUE would let NULLs multiply, since NULL <> NULL in SQL).
CREATE UNIQUE INDEX medications_identity_idx ON medications (
    lower(btrim(generic_name)),
    lower(btrim(COALESCE(brand_name, ''))),
    lower(btrim(COALESCE(strength, ''))),
    lower(btrim(COALESCE(dosage_form, '')))
);

-- Search (generic/brand name) -- same trigram approach already proven
-- for patient name search (migrations/0030_patient_merge_and_
-- duplicate_detection.sql), reused rather than inventing a second
-- search strategy. pg_trgm is already enabled by that migration.
CREATE INDEX medications_generic_name_trgm_idx ON medications USING gin (generic_name gin_trgm_ops);
CREATE INDEX medications_brand_name_trgm_idx ON medications USING gin (brand_name gin_trgm_ops) WHERE brand_name IS NOT NULL;
CREATE INDEX medications_active_idx ON medications (active);

COMMENT ON TABLE medications IS
    'Internal canonical medication identity, shared by prescription_items and pharmacy_stock via their own medication_id columns below. Deliberately not tied to any external terminology (RxNorm/SNOMED) -- see docs/OPD_HIMS_STANDARDS_READINESS.md S16 for why those columns are not added here.';

-- Additive, nullable -- every existing row keeps working unmodified.
-- No ON DELETE clause (defaults to the FK's normal reference-integrity
-- behavior, i.e. a medication referenced by any item/stock row cannot
-- be hard-deleted) -- deactivation (medications.active = FALSE) is the
-- only supported way to retire one, per this migration's own
-- instruction not to use cascading deletes that could erase historical
-- prescription meaning.
ALTER TABLE prescription_items ADD COLUMN medication_id BIGINT REFERENCES medications(id);
ALTER TABLE pharmacy_stock ADD COLUMN medication_id BIGINT REFERENCES medications(id);

CREATE INDEX prescription_items_medication_id_idx ON prescription_items (medication_id);
CREATE INDEX pharmacy_stock_medication_id_idx ON pharmacy_stock (medication_id);

COMMENT ON COLUMN prescription_items.medication_id IS
    'Nullable -- NULL for every prescription written before this migration (their medicine_name/generic_name text columns remain the full, unmodified historical record), and for any future line a clinician enters as free text without picking a Medication Master match. Never backfilled by guessing; see this migration''s own backfill block for the one safe, exact-match case it does link.';
COMMENT ON COLUMN pharmacy_stock.medication_id IS
    'Same as prescription_items.medication_id -- nullable, never guessed beyond an exact normalized-text match to an existing medicine_name.';

-- ---------------------------------------------------------------------
-- Backfill: exact-match only, never fuzzy/clinical equivalence.
--
-- "Confident match" here means exactly one thing: the existing
-- medicine_name text, lowercased and trimmed, is byte-identical to
-- another row's (in either table). This is NOT drug-name equivalence
-- reasoning -- it never merges "Paracetamol" with "Panadol", and it
-- never merges "Paracetamol 500mg" with "Paracetamol 650mg" (those
-- normalize to two different strings, so they correctly become two
-- different medications rows). It also, deliberately, does NOT treat
-- "Paracetamol 500mg" and "paracetamol 500 mg" as the same medication
-- -- the extra internal space makes them different strings under
-- lower+trim, and inventing a rule to collapse that (or any other
-- spelling/spacing variant) would be exactly the fuzzy equivalence this
-- migration is instructed not to invent. Any such variant is left as
-- its own, separately-linked medications row -- safe, if not perfectly
-- deduplicated; a human can merge them later with real judgment this
-- migration doesn't have.
--
-- Step 1: one medications row per distinct normalized name across BOTH
-- tables combined (so a name appearing in both prescription_items and
-- pharmacy_stock links to the SAME row, not two) -- generic_name is set
-- to whichever original-cased spelling sorts first, purely for
-- determinism; no other significance.
INSERT INTO medications (generic_name, created_by)
SELECT DISTINCT ON (lower(btrim(medicine_name)))
    btrim(medicine_name), NULL
FROM (
    SELECT medicine_name FROM prescription_items
    UNION ALL
    SELECT medicine_name FROM pharmacy_stock
) existing_names
ORDER BY lower(btrim(medicine_name)), medicine_name;

-- Step 2: link every existing row whose normalized medicine_name
-- exactly matches one of the rows just created.
UPDATE prescription_items pi
SET medication_id = m.id
FROM medications m
WHERE lower(btrim(pi.medicine_name)) = lower(btrim(m.generic_name))
  AND pi.medication_id IS NULL;

UPDATE pharmacy_stock ps
SET medication_id = m.id
FROM medications m
WHERE lower(btrim(ps.medicine_name)) = lower(btrim(m.generic_name))
  AND ps.medication_id IS NULL;
