-- M6 (HospitalOS build plan): UHID -- a stable, human-readable patient
-- identifier independent of any contact detail, the next step after
-- M4-M5's identifier work toward decoupling identity from phone
-- ownership entirely (M7 drops the phone UNIQUE constraint next;
-- patients get looked up/printed by UHID once that happens).
--
-- Format: <hospital.code>-<6-digit zero-padded sequence>, e.g.
-- MAIN-000001. Generated from hospital_uhid_counters, a plain
-- per-hospital counter table rather than a native Postgres SEQUENCE
-- object per hospital -- there is no runtime "create a new hospital"
-- flow yet (hospitals are only ever seeded by migrations/0024), so a
-- dynamically-created native sequence per hospital has nothing to hook
-- into. A single-row UPDATE ... RETURNING against this table is already
-- atomic under Postgres's own row-level locking -- two concurrent
-- callers for the same hospital_id simply serialize, never both
-- observing the same next_seq, no advisory lock needed (unlike the
-- scheduling paths' resource-allocation problem, which is about
-- coordinating checks across multiple statements, not a single atomic
-- write). A hospital added after this migration needs its own counter
-- row added alongside it -- nothing does that automatically yet.
--
-- uhid is nullable at the column level, same reasoning as
-- appointments.encounter_id (migrations/0025): NOT NULL is deferred to
-- a later, separate migration once every write path is confirmed to
-- populate it. Uniqueness is enforced per hospital, not globally, via a
-- partial index -- ready for more than one hospital's UHIDs to coexist
-- once a second hospital actually exists.
CREATE TABLE hospital_uhid_counters (
    hospital_id BIGINT PRIMARY KEY REFERENCES hospitals(id),
    next_seq    BIGINT NOT NULL DEFAULT 1
);

COMMENT ON TABLE hospital_uhid_counters IS
    'One row per hospital. next_seq is the next number app/services/uhid.py''s generate_uhid() will hand out -- never decremented, never reused, including after a future merge retires a UHID (M8).';

ALTER TABLE patients ADD COLUMN uhid TEXT;

CREATE UNIQUE INDEX patients_uhid_per_hospital
    ON patients (hospital_id, uhid)
    WHERE uhid IS NOT NULL;

COMMENT ON COLUMN patients.uhid IS
    'NULL until generated -- never assume NOT NULL. Format: <hospital code>-<6-digit sequence>, e.g. MAIN-000001. See app/services/uhid.py.';

-- Seed this deployment's one counter row.
INSERT INTO hospital_uhid_counters (hospital_id, next_seq) VALUES (1, 1);

-- Backfill existing patients in id order, per hospital.
WITH numbered AS (
    SELECT p.id AS patient_id, h.code,
           ROW_NUMBER() OVER (PARTITION BY p.hospital_id ORDER BY p.id) AS rn
    FROM patients p
    JOIN hospitals h ON h.id = p.hospital_id
)
UPDATE patients p
SET uhid = numbered.code || '-' || LPAD(numbered.rn::text, 6, '0')
FROM numbered
WHERE p.id = numbered.patient_id;

-- Advance each hospital's counter past what the backfill just assigned,
-- so the first runtime-generated UHID continues the sequence instead of
-- colliding with it.
UPDATE hospital_uhid_counters c
SET next_seq = (SELECT COUNT(*) FROM patients p WHERE p.hospital_id = c.hospital_id) + 1;
