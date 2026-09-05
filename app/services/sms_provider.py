"""
Real SMS delivery via Authkey.io, used only for patient OTP codes.

This sits alongside, not instead of, the mock notification system
(app/services/notifications.py): every OTP still gets written to
mock_sms_outbox as before (so *_dev_lookup and existing tests keep
working unchanged), and additionally, if AUTHKEY_API_KEY and
AUTHKEY_TEMPLATE_ID are both configured, a real SMS is sent to the
patient's phone via Authkey's OTP API.

With no Authkey env vars set (the default), send_otp_sms() is a no-op --
local dev and CI never need an Authkey account.

Why Authkey.io instead of Twilio: this app's phone numbers default to
+91 (India, see app/utils/phone.py), and Authkey is an India-focused SMS/
OTP provider with DLT-compliant routing and a much larger free-credit
tier for testing than Twilio's -- see the product conversation that led
here.

Authkey's OTP send is a template-based API: AUTHKEY_TEMPLATE_ID (their
`sid` parameter) must be a DLT-approved OTP template already created in
your Authkey dashboard, using `{#otp#}` as its one variable -- Indian
telecom regulation (TRAI/DLT) requires the exact message text to be
pre-registered before it can be delivered, so the *content* of
message_body (built in app/services/patient_auth.py) is NOT sent here;
only the OTP code itself is, filled into whatever wording the registered
template already has. message_body is accepted for API-shape parity with
the mock path and to keep patient_auth.py provider-agnostic, but is
otherwise unused by this provider.

NOTE: this was implemented without direct access to Authkey's live API
docs (this environment's network policy blocks fetching authkey.io) --
the endpoint/parameter shape below is built from their publicly
documented request pattern. Verify the exact parameter names against
your own Authkey dashboard/docs once you have an account, before relying
on this in anything other than a quick test.

Security/PHI notes (same posture as app/services/notifications.py):
  - The OTP code is sent to Authkey (it has to be, to be delivered) but
    is never logged locally.
  - A delivery failure here must never surface to the caller as a
    request failure -- the OTP is already valid and stored; a patient
    who has the mock outbox available (dev/test) or a working phone can
    still complete login even if this specific send fails.
  - Only the exception type / HTTP status is logged on failure, never
    the response body or destination number, since some SMS gateways
    echo the destination number back in error text.
"""

import logging
import urllib.error
import urllib.parse
import urllib.request

from app import config

logger = logging.getLogger(__name__)

AUTHKEY_ENDPOINT = "https://api.authkey.io/request"


def _authkey_configured() -> bool:
    return bool(config.AUTHKEY_API_KEY and config.AUTHKEY_TEMPLATE_ID)


def _strip_country_code(whatsapp_number: str) -> str:
    """Authkey's API takes the bare mobile number and country code as
    separate fields; app.utils.phone normalizes numbers to a single
    E.164 string (e.g. "+919876543210"). Best-effort split on the
    configured country code -- falls back to stripping just the '+' if
    the number doesn't start with it (e.g. a non-Indian number)."""
    digits = whatsapp_number.lstrip("+")
    if digits.startswith(config.AUTHKEY_COUNTRY_CODE):
        return digits[len(config.AUTHKEY_COUNTRY_CODE):]
    return digits


def send_otp_sms(whatsapp_number: str, otp_code: str) -> None:
    """Best-effort real SMS delivery. Swallows all errors -- OTP issuance
    (app.services.patient_auth.request_otp) must succeed regardless of
    whether this succeeds, since the mock outbox is always written too."""
    if not _authkey_configured():
        return

    params = {
        "authkey": config.AUTHKEY_API_KEY,
        "mobile": _strip_country_code(whatsapp_number),
        "country_code": config.AUTHKEY_COUNTRY_CODE,
        "sid": config.AUTHKEY_TEMPLATE_ID,
        "otp": otp_code,
    }
    url = f"{AUTHKEY_ENDPOINT}?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            if response.status >= 400:
                logger.warning("Authkey OTP SMS delivery failed: HTTP %s", response.status)
    except urllib.error.URLError as exc:
        logger.warning("Authkey OTP SMS delivery failed: %s", type(exc).__name__)
    except Exception as exc:
        logger.warning("Authkey OTP SMS delivery failed: %s", type(exc).__name__)
