-- Doctor profile enhancement: specialization, years of experience,
-- qualifications, a multi-entry education/training history, and an
-- optional profile photo reference.
--
-- Every new doctors column is nullable with no default beyond NULL --
-- existing doctors keep working and displaying exactly as before, with
-- blank profile fields until an admin fills them in (no backfill). The
-- "specialization is required for new doctors" product decision is
-- enforced at the API layer (app/api/doctors.py's DoctorCreate model),
-- deliberately not as a NOT NULL constraint here, since a NOT NULL
-- constraint would require every pre-existing row to already have one.

ALTER TABLE doctors
    ADD COLUMN specialization TEXT,
    ADD COLUMN sub_specialization TEXT,
    ADD COLUMN qualifications TEXT,
    ADD COLUMN years_of_experience INTEGER,
    ADD COLUMN photo_url TEXT;

COMMENT ON COLUMN doctors.specialization IS
    'Required for newly-created doctors (enforced in app/api/doctors.py, not here) -- nullable so existing doctors need no backfill.';
COMMENT ON COLUMN doctors.qualifications IS
    'Short headline summary (e.g. "MBBS, MD (Cardiology)") shown as the compact booking card''s "key qualification" -- distinct from the per-entry qualification recorded in doctor_education below.';
COMMENT ON COLUMN doctors.years_of_experience IS
    'Explicit admin-entered value. Never derived from doctor_education.completion_year.';
COMMENT ON COLUMN doctors.photo_url IS
    'Relative URL under the /media static mount (see app/config.py''s MEDIA_ROOT), e.g. "/media/doctors/<uuid>.jpg". The image file itself lives on disk, never in this column.';

-- Education & Training: multiple entries per doctor. All-or-nothing per
-- entry (every field NOT NULL) -- a partial entry (e.g. an institution
-- with no completion year) is not meaningful, so the API only ever
-- inserts a fully-populated row, never a partially-filled one to be
-- completed later.
CREATE TABLE doctor_education (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doctor_id       BIGINT NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    qualification   TEXT NOT NULL,
    institution     TEXT NOT NULL,
    city            TEXT NOT NULL,
    country         TEXT NOT NULL,
    completion_year INTEGER NOT NULL,
    -- The one entry (if any) shown as the compact booking card's
    -- "education/training location" line. Deliberately admin-chosen,
    -- not auto-derived from completion_year -- see the partial unique
    -- index below for the "at most one per doctor" rule.
    is_primary      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX doctor_education_doctor_id_idx ON doctor_education (doctor_id);

-- Enforces "only one education entry per doctor can be featured" at the
-- database level: a second UPDATE/INSERT setting is_primary = TRUE for
-- the same doctor while another row is already primary hits this
-- constraint, rather than relying solely on application-level care.
CREATE UNIQUE INDEX doctor_education_one_primary_per_doctor
    ON doctor_education (doctor_id)
    WHERE is_primary;
