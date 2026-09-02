-- Patient authentication (WEB P2): mobile number + OTP login/registration.
-- Purely additive -- three new tables, no changes to any existing table.
--
-- patient_otp_codes: the actual verification record. Stores code_hash
-- (SHA-256), never the plaintext code -- see app/services/patient_auth.py
-- for why a per-row salt wasn't judged necessary on top of that (short
-- TTL + attempt limit + request rate limit already bound the attack, and
-- anyone with DB read access to see a hash here could see the row's
-- other context regardless of salting).
--
-- patient_sessions: opaque bearer session tokens, hashed the same way,
-- for the same reason (a DB read alone must never hand out a usable
-- token).
--
-- mock_sms_outbox: NOT the OTP verification record above -- this is the
-- mock SMS/OTP provider's delivery log (what a real SMS would have
-- contained), including the plaintext code, so a non-production-only
-- lookup endpoint can retrieve it for testing without a real SMS
-- provider (global rules 15/16). This table is never written to via
-- app.logging_config's loggers -- it's an explicit mock-provider
-- artifact, not an application log, and the distinction matters for the
-- "no OTP logging" requirement. WEB P8 will generalize this into a
-- shared mock-notification abstraction also used for booking
-- confirmations; scoped to OTP delivery only here, deliberately not
-- built ahead of that phase.

CREATE TABLE patient_otp_codes (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    whatsapp_number TEXT NOT NULL,
    code_hash       TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    consumed_at     TIMESTAMPTZ,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Both the rate-limit count (recent rows for a number) and "fetch the
-- latest OTP for this number" queries filter/order on exactly this pair.
CREATE INDEX idx_patient_otp_codes_number_created
    ON patient_otp_codes (whatsapp_number, created_at DESC);

CREATE TABLE patient_sessions (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id  BIGINT NOT NULL REFERENCES patients(id),
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ
);

CREATE INDEX idx_patient_sessions_patient
    ON patient_sessions (patient_id);

CREATE TABLE mock_sms_outbox (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    whatsapp_number TEXT NOT NULL,
    otp_code        TEXT NOT NULL,
    message_body    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mock_sms_outbox_number_created
    ON mock_sms_outbox (whatsapp_number, created_at DESC);
