"""
Real SMS delivery via Twilio, used only for patient OTP codes.

This sits alongside, not instead of, the mock notification system
(app/services/notifications.py): every OTP still gets written to
mock_sms_outbox as before (so *_dev_lookup and existing tests keep
working unchanged), and additionally, if TWILIO_ACCOUNT_SID/
TWILIO_AUTH_TOKEN/TWILIO_FROM_NUMBER are all configured, a real SMS is
sent to the patient's phone.

With no Twilio env vars set (the default), send_otp_sms() is a no-op --
local dev and CI never need a Twilio account.

Security/PHI notes (same posture as app/services/notifications.py):
  - The OTP code and phone number are never logged. On a Twilio API
    failure, only the exception type/message (which Twilio does not put
    the message body or destination number into) is logged.
  - A delivery failure here must never surface to the caller as a
    request failure -- the OTP is already valid and stored; a patient
    who has the mock outbox available (dev/test) or a working phone can
    still complete login even if this specific send fails.
  - Only the exception *type* is logged on failure, never str(exc) --
    Twilio's own error messages sometimes echo the destination number
    back verbatim (e.g. "The 'To' number +91... is not valid"), which
    would leak it into logs otherwise.
"""

import logging

from app import config

logger = logging.getLogger(__name__)


def _twilio_configured() -> bool:
    return bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_FROM_NUMBER)


def send_otp_sms(whatsapp_number: str, message_body: str) -> None:
    """Best-effort real SMS delivery. Swallows all errors -- OTP issuance
    (app.services.patient_auth.request_otp) must succeed regardless of
    whether this succeeds, since the mock outbox is always written too."""
    if not _twilio_configured():
        return

    try:
        from twilio.rest import Client

        client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        client.messages.create(
            to=whatsapp_number,
            from_=config.TWILIO_FROM_NUMBER,
            body=message_body,
        )
    except Exception as exc:
        logger.warning("Twilio OTP SMS delivery failed: %s", type(exc).__name__)
