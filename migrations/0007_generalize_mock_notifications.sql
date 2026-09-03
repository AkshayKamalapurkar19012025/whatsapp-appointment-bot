-- Generalize mock_sms_outbox into a shared mock-notification outbox
-- (WEB P8), exactly as flagged in migrations/0004's header comment when
-- the table was first created OTP-only: "WEB P8 will generalize this
-- into a shared mock-notification abstraction also used for booking
-- confirmations."
--
-- otp_code becomes nullable -- a booking-confirmation/cancellation/
-- reschedule notification has no OTP code, only a message body. A new
-- `kind` discriminator distinguishes what a row actually is; every
-- existing row (necessarily OTP, since that was the only thing this
-- table could hold before this migration) backfills to 'OTP' via the
-- column default, preserving its meaning exactly.

ALTER TABLE mock_sms_outbox
    ALTER COLUMN otp_code DROP NOT NULL,
    ADD COLUMN kind TEXT NOT NULL DEFAULT 'OTP';

ALTER TABLE mock_sms_outbox
    ADD CONSTRAINT mock_sms_outbox_kind_check
    CHECK (kind IN ('OTP', 'BOOKING_CONFIRMATION', 'CANCELLATION', 'RESCHEDULE'));
