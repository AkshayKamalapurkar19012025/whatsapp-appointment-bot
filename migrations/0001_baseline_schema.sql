-- Baseline schema, inferred from the SQL queries in app/api/*.py as they
-- exist today. This migration does NOT change or add behaviour: it only
-- formalizes, as a reproducible migration, the schema the application code
-- already assumes.
--
-- Scope discipline for this migration specifically:
--   * Primary keys: yes (every table needs one; the app relies on `id`
--     being returned from RETURNING clauses).
--   * Foreign keys: yes (every *_id column the code joins/filters on is
--     declared as a real FK to the table it logically references).
--   * NOT NULL: yes, wherever the app never inserts/reads a NULL for that
--     column (verified against every INSERT/SELECT in app/api/*.py).
--   * UNIQUE: yes, but ONLY where the application code already depends on
--     it (catches psycopg.errors.UniqueViolation, or uses
--     `ON CONFLICT (...)`). Search: `departments.name`, `doctors.name`,
--     `appointment_types.name`, `patients.whatsapp_number`,
--     `booking_sessions.whatsapp_number`, `doctor_appointment_types
--     (doctor_id, appointment_type_id)`, `doctor_departments
--     (doctor_id, department_id)`.
--   * CHECK constraints, performance indexes beyond what UNIQUE already
--     creates, and any constraint not already implied by current code
--     behaviour are deliberately NOT included here — those are reviewed
--     and applied as separate, additive migrations so each change stays
--     independently understandable and revertible.
--
-- One deliberate omission worth flagging: doctor_schedule has no
-- CHECK(end_time > start_time). app/api/booking.py's slot calculation
-- (get_available_slots) contains explicit handling for an overnight
-- schedule (end_time <= start_time, e.g. 22:00-02:00), even though the
-- doctor_schedule.py API's Pydantic validator currently rejects creating
-- such a schedule through that endpoint. Adding a DB-level CHECK here
-- would foreclose a case the booking engine is already written to
-- support. This inconsistency is called out in the review report rather
-- than silently resolved by this migration.

-- Transaction boundaries are managed by scripts/migrate.py (one
-- transaction per migration file), not by this file.

CREATE TABLE departments (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE doctors (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    -- Read by app/api/booking.py's get_doctor_timezone(). The app already
    -- falls back to 'Asia/Kolkata' at runtime if this is missing/invalid,
    -- so defaulting new rows to the same value preserves that behaviour
    -- rather than introducing a new one.
    timezone    TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE doctor_departments (
    doctor_id       BIGINT NOT NULL REFERENCES doctors(id),
    department_id   BIGINT NOT NULL REFERENCES departments(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (doctor_id, department_id)
);

CREATE TABLE appointment_types (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE doctor_appointment_types (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doctor_id               BIGINT NOT NULL REFERENCES doctors(id),
    appointment_type_id     BIGINT NOT NULL REFERENCES appointment_types(id),
    duration_minutes        INTEGER NOT NULL,
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (doctor_id, appointment_type_id)
);

CREATE TABLE doctor_schedule (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doctor_id   BIGINT NOT NULL REFERENCES doctors(id),
    day_of_week SMALLINT NOT NULL,
    start_time  TIME NOT NULL,
    end_time    TIME NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE doctor_blocks (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doctor_id   BIGINT NOT NULL REFERENCES doctors(id),
    start_at    TIMESTAMPTZ NOT NULL,
    end_at      TIMESTAMPTZ NOT NULL,
    reason      TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE patients (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                TEXT NOT NULL,
    whatsapp_number     TEXT NOT NULL UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE appointments (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doctor_id               BIGINT NOT NULL REFERENCES doctors(id),
    patient_id              BIGINT NOT NULL REFERENCES patients(id),
    appointment_type_id     BIGINT NOT NULL REFERENCES appointment_types(id),
    start_at                TIMESTAMPTZ NOT NULL,
    end_at                  TIMESTAMPTZ NOT NULL,
    -- Values used by the app today: 'BOOKED', 'CANCELLED'.
    status                  TEXT NOT NULL DEFAULT 'BOOKED',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE booking_sessions (
    id                          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id                  BIGINT REFERENCES patients(id),
    whatsapp_number             TEXT NOT NULL UNIQUE,
    step                        TEXT NOT NULL,
    department_id               BIGINT REFERENCES departments(id),
    doctor_id                   BIGINT REFERENCES doctors(id),
    appointment_type_id         BIGINT REFERENCES appointment_types(id),
    selected_date                DATE,
    selected_start_at           TIMESTAMPTZ,
    selected_appointment_id     BIGINT REFERENCES appointments(id),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
