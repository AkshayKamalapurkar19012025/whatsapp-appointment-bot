"""
WhatsApp/mobile number normalization for the web-facing entry points.

Not applied to the WhatsApp inbound path (app/api/scheduling.py) -- every
number arriving there already comes from the messaging provider in
canonical E.164 form, and it's the pre-existing source of truth every
patient row is keyed against. This exists for the two places a human
*types* a number by hand: patient OTP login (app/api/patient_auth.py) and
admin patient registration (app/api/patients.py). Without it,
"9876543210", "+919876543210", and "+91 9876543210" would compare unequal
against patients.whatsapp_number's UNIQUE constraint and silently create
three different patient rows for the same person -- there is no
normalization anywhere else in the codebase today (verified by reading
every INSERT/SELECT touching whatsapp_number before writing this).
"""

import re

_SEPARATORS = re.compile(r"[\s\-()]")
_BARE_10_DIGIT = re.compile(r"\d{10}")
_91_PREFIXED_12_DIGIT = re.compile(r"91\d{10}")


def normalize_whatsapp_number(raw: str) -> str:
    """
    Best-effort normalization to E.164-ish form, defaulting bare local
    numbers to +91 (India) per product requirement -- this app has no
    other country in scope anywhere else in the codebase (doctors.
    timezone defaults to Asia/Kolkata, phone examples throughout are all
    +91). Deliberately conservative: only the shapes explicitly called
    out (a bare 10-digit number, one with a leading '91', one with a
    leading '00' in place of '+') are rewritten. Anything else -- already
    has a '+', or doesn't match a recognized shape -- is returned with
    only whitespace/hyphens/parentheses stripped, never guessed at,
    so a genuinely different (non-Indian, or already-correct) number is
    never corrupted into something wrong.
    """
    value = _SEPARATORS.sub("", raw.strip())

    if value.startswith("+"):
        return value

    if value.startswith("00"):
        return "+" + value[2:]

    if _BARE_10_DIGIT.fullmatch(value):
        return "+91" + value

    if _91_PREFIXED_12_DIGIT.fullmatch(value):
        return "+" + value

    return value
