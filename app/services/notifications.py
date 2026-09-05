"""
Mock outbound notification delivery (WEB P8).

Generalizes the WEB P2 mock_sms_outbox table -- previously OTP-only, see
migrations/0004's header comment -- into a shared abstraction any
patient-facing action can use to "send" a mock SMS/WhatsApp business
message. This is now the single insertion point for that table:
app.services.patient_auth's OTP issuance (request_otp) calls
send_mock_notification() too, not just this phase's new callers, so
there is exactly one place that writes to mock_sms_outbox.

Scope: WEB (patient) actions, plus one staff-initiated exception.
app/api/booking.py's WhatsApp flow already gives real-time confirmation
via its own conversational reply in the same chat -- it is deliberately
NOT wired to this module, since sending an *additional* "confirmation"
for an action the patient just watched happen live would be a pointless
duplicate, not a fix for anything. A web-originated booking/cancel/
reschedule has no such live channel to confirm in, which is exactly the
gap this phase closes (see frontend/src/BookingFlow.tsx's WEB P3-era
placeholder text about a confirmation SMS "implemented in a later
phase", updated this phase to match).

KIND_CHECK_IN is the one exception to "WEB (patient) actions only": it
fires from app/api/appointments.py's staff-only /visit endpoint
(migrations/0012), not a patient action at all -- the patient is at the
front desk, not mid-chat with the bot, when staff check them in, so
there is no live channel to duplicate here either.

Security/PHI notes (same posture as OTP's own mock delivery):
  - Real content is never returned to any authenticated web response --
    retrieval is dev/test-only, via the *_dev_lookup endpoints (disabled
    whenever ENVIRONMENT=production).
  - Nothing in this module calls into app.logging_config's loggers with
    a notification body or the whatsapp_number it was sent to.
"""

KIND_OTP = "OTP"
KIND_BOOKING_CONFIRMATION = "BOOKING_CONFIRMATION"
KIND_CANCELLATION = "CANCELLATION"
KIND_RESCHEDULE = "RESCHEDULE"
KIND_CHECK_IN = "CHECK_IN"


def send_mock_notification(
    cur,
    whatsapp_number: str,
    kind: str,
    message_body: str,
    *,
    otp_code: str | None = None,
) -> None:
    """Record a mock outbound notification. otp_code is only ever set
    for kind=KIND_OTP -- every other kind leaves it NULL (the column
    became nullable in migrations/0007 for exactly this reason)."""
    cur.execute(
        """
        INSERT INTO mock_sms_outbox (whatsapp_number, kind, otp_code, message_body)
        VALUES (%s, %s, %s, %s)
        """,
        (whatsapp_number, kind, otp_code, message_body),
    )
