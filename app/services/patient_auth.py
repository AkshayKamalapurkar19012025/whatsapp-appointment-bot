"""
Patient authentication: mobile number + OTP login/registration (WEB P2).

Flow, per the product spec:

    mobile number -> OTP -> existing patient -> login
                          -> new patient      -> registration

request_otp() issues a 6-digit code, rate-limited per mobile number. It
always writes the code to mock_sms_outbox (see the note below), and
additionally sends a real SMS via Authkey.io when configured -- see
app/services/sms_provider.py. With no Authkey credentials set, only the
mock delivery happens, same as before.

verify_otp() checks the code, then branches:
  - a patient already exists for this number -> log them in.
  - no patient exists and a name was given -> register + log them in.
  - no patient exists and no name was given -> raises RegistrationRequired
    and leaves the OTP row UNCONSUMED, so the same code can be retried
    with a name before it expires. This is the one deliberate exception
    to "OTP cannot be reused": that requirement is about blocking reuse
    after a login/registration has already completed (consumed_at set),
    not about blocking a benign two-step verify-then-supply-name dance
    for a first-time caller.

Security notes:
  - The OTP code and session token are both hashed (SHA-256) before
    storage -- a database read alone must never hand out something
    directly usable to log in. No per-row salt: the short TTL, the
    attempt cap, and the request-rate-limit are what bound an attacker
    here, not hash uniqueness, and anyone with DB read access to see a
    hash could see the row's other context regardless of salting.
  - Codes and tokens are generated with `secrets`, not `random`.
  - Sessions expire on two independent clocks (WEB P10): an absolute cap
    (SESSION_TTL_HOURS from login) and a shorter idle cap
    (SESSION_IDLE_TIMEOUT_MINUTES since the last authenticated request) --
    whichever is stricter wins. Set longer than staff's own idle cap
    (app/services/staff_auth.py) since a scheduling session can legitimately
    sit idle mid-flow (reviewing dates, stepping away) and a patient
    session's blast radius if stolen is that patient's own data, not the
    admin surface a staff session reaches.
  - Nothing in this module calls into app.logging_config's loggers with
    a raw OTP code or session token -- "no OTP logging" (a phase
    requirement) means the application log, not the mock_sms_outbox
    table below, which is a different, deliberately-plaintext mechanism.

mock_sms_outbox is NOT the OTP verification record (patient_otp_codes
is, hashed). It's the mock SMS/OTP provider's delivery log -- what a
real SMS would have contained -- so a non-production-only lookup
endpoint (app/api/patient_auth.py) can retrieve it without a real SMS
provider existing yet. See migrations/0004's header comment for why this
isn't built as a general notification abstraction yet (that's WEB P8).
"""

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import secrets

import psycopg

from app import config
from app.services.exceptions import (
    OtpRateLimited,
    OtpNotFound,
    OtpExpired,
    OtpAlreadyUsed,
    OtpLocked,
    OtpInvalid,
    RegistrationRequired,
    InvalidSession,
)
from app.services.notifications import KIND_OTP, send_mock_notification
from app.services.patient_identifiers import (
    DEFAULT_HOSPITAL_ID,
    resolve_patient_by_identifier,
    write_phone_identifier,
)
from app.services.sms_provider import send_otp_sms
from app.services.uhid import generate_uhid

logger = logging.getLogger(__name__)

OTP_TTL_MINUTES = 5
OTP_MAX_VERIFY_ATTEMPTS = 5
OTP_REQUEST_RATE_LIMIT_MAX = 3
OTP_REQUEST_RATE_LIMIT_WINDOW_MINUTES = 10
SESSION_TTL_HOURS = 24
SESSION_IDLE_TIMEOUT_MINUTES = 120


def _now():
    return datetime.now(timezone.utc)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def request_otp(cur, whatsapp_number: str) -> None:
    """Issue a new OTP for whatsapp_number, subject to a per-number
    request rate limit. Raises OtpRateLimited if exceeded."""
    window_start = _now() - timedelta(minutes=OTP_REQUEST_RATE_LIMIT_WINDOW_MINUTES)

    cur.execute(
        """
        SELECT count(*)
        FROM patient_otp_codes
        WHERE whatsapp_number = %s
          AND created_at > %s
        """,
        (whatsapp_number, window_start),
    )
    recent_count = cur.fetchone()[0]

    if recent_count >= OTP_REQUEST_RATE_LIMIT_MAX:
        raise OtpRateLimited()

    code = _generate_otp_code()
    code_hash = _hash_code(code)
    expires_at = _now() + timedelta(minutes=OTP_TTL_MINUTES)

    cur.execute(
        """
        INSERT INTO patient_otp_codes (whatsapp_number, code_hash, expires_at)
        VALUES (%s, %s, %s)
        """,
        (whatsapp_number, code_hash, expires_at),
    )

    message_body = (
        f"Your appointment platform verification code is {code}. "
        f"It expires in {OTP_TTL_MINUTES} minutes."
    )

    send_mock_notification(
        cur, whatsapp_number, KIND_OTP, message_body, otp_code=code
    )
    send_otp_sms(whatsapp_number, code)


def verify_otp(cur, whatsapp_number: str, code: str, name: str | None = None):
    """
    Verify an OTP code and log the patient in, registering them first if
    they're new and a name was given.

    Returns {"patient": {...}, "session_token": str, "is_new_patient": bool}
    on success. Raises OtpNotFound / OtpExpired / OtpAlreadyUsed /
    OtpLocked / OtpInvalid on a bad code, or RegistrationRequired if the
    code is correct but no patient exists yet and no name was supplied
    (the OTP row is left unconsumed in that case).

    If config.TEST_STATIC_OTP is set (never in production, see
    app/config.py), that fixed code is also accepted in place of the
    real per-number code below -- lets someone testing over a tunnel/
    public URL log in with a code you've told them out of band, without
    real SMS delivery configured. A request_otp() call still has to have
    happened first (there must be a pending, unexpired, unconsumed row)
    -- this only widens *which* code is accepted, not whether one is
    required.
    """
    cur.execute(
        """
        SELECT id, code_hash, expires_at, consumed_at, attempt_count
        FROM patient_otp_codes
        WHERE whatsapp_number = %s
        ORDER BY created_at DESC
        LIMIT 1
        FOR UPDATE
        """,
        (whatsapp_number,),
    )
    row = cur.fetchone()

    if row is None:
        raise OtpNotFound()

    otp_id, code_hash, expires_at, consumed_at, attempt_count = row

    if consumed_at is not None:
        raise OtpAlreadyUsed()

    if _now() > expires_at:
        raise OtpExpired()

    if attempt_count >= OTP_MAX_VERIFY_ATTEMPTS:
        raise OtpLocked()

    # Belt-and-suspenders: config.TEST_STATIC_OTP is already forced empty
    # whenever ENVIRONMENT=production at load time (app/config.py), but
    # this bypass is sensitive enough to also re-check ENVIRONMENT here
    # directly, the same defense-in-depth app/api/patient_auth.py's
    # _dev_lookup endpoint uses for the same reason.
    is_static_test_code = (
        config.ENVIRONMENT != "production"
        and bool(config.TEST_STATIC_OTP)
        and code == config.TEST_STATIC_OTP
    )

    if is_static_test_code:
        logger.info("Patient OTP verified via TEST_STATIC_OTP bypass (non-production only)")
    elif _hash_code(code) != code_hash:
        cur.execute(
            "UPDATE patient_otp_codes SET attempt_count = attempt_count + 1 WHERE id = %s",
            (otp_id,),
        )
        raise OtpInvalid()

    # M4-M5: migrated onto the identifier resolver (web OTP verify) --
    # see app/services/patient_identifiers.py. DEFAULT_HOSPITAL_ID: no
    # patient is resolved yet at this point, so there's no authenticated
    # actor to derive a real hospital_id from (see that constant's own
    # comment).
    resolved = resolve_patient_by_identifier(cur, DEFAULT_HOSPITAL_ID, "PHONE", whatsapp_number)
    patient_row = (
        (resolved["id"], resolved["name"], resolved["whatsapp_number"])
        if resolved is not None
        else None
    )

    is_new_patient = False

    if patient_row is None:
        if not name or not name.strip():
            # Correct code, but nothing to log in to yet -- leave the OTP
            # unconsumed so the same code works on the follow-up call.
            raise RegistrationRequired()

        # M7 (in progress): ON CONFLICT (whatsapp_number) is gone -- it
        # needs a matching unique/exclusion constraint or index to
        # target, so it becomes invalid SQL the moment
        # patients.whatsapp_number's UNIQUE constraint is actually
        # dropped (see migrations/0027's follow-up report for the rest
        # of that migration). Until that drop ships, the constraint is
        # still live, so the same race this used to resolve via DO
        # NOTHING can still raise UniqueViolation here -- caught below
        # and funneled into the exact same race-recovery read as before.
        # Once the constraint is gone this except simply never fires
        # again; two patients sharing a number then both insert
        # successfully, which is the point of dropping it.
        try:
            cur.execute(
                """
                INSERT INTO patients (name, whatsapp_number)
                VALUES (%s, %s)
                RETURNING id, name, whatsapp_number, hospital_id
                """,
                (name.strip(), whatsapp_number),
            )
            patient_row = cur.fetchone()
        except psycopg.errors.UniqueViolation:
            cur.connection.rollback()
            patient_row = None

        if patient_row is None:
            # Lost a race with another request for the same number
            # (e.g. two tabs registering at once) -- the patient now
            # exists either way, so just read it back.
            resolved = resolve_patient_by_identifier(cur, DEFAULT_HOSPITAL_ID, "PHONE", whatsapp_number)
            patient_row = (
                (resolved["id"], resolved["name"], resolved["whatsapp_number"])
                if resolved is not None
                else None
            )
        else:
            is_new_patient = True
            # M4-M5 dual write -- see app/services/patient_identifiers.py.
            # Not needed on the race-recovery branch above: that read
            # back a patient this same call didn't create, so nothing
            # about their identifier changed.
            write_phone_identifier(
                cur,
                hospital_id=patient_row[3],
                patient_id=patient_row[0],
                whatsapp_number=patient_row[2],
            )
            # M6: assign this patient's permanent UHID at creation time
            # -- see app/services/uhid.py. Same "not on the race-recovery
            # branch" reasoning as above.
            uhid = generate_uhid(cur, patient_row[3])
            cur.execute("UPDATE patients SET uhid = %s WHERE id = %s", (uhid, patient_row[0]))

    patient = {
        "id": patient_row[0],
        "name": patient_row[1],
        "whatsapp_number": patient_row[2],
    }

    cur.execute(
        "UPDATE patient_otp_codes SET consumed_at = %s WHERE id = %s",
        (_now(), otp_id),
    )

    token = _generate_session_token()
    token_hash = _hash_token(token)
    session_expires_at = _now() + timedelta(hours=SESSION_TTL_HOURS)

    cur.execute(
        """
        INSERT INTO patient_sessions (patient_id, token_hash, expires_at)
        VALUES (%s, %s, %s)
        """,
        (patient["id"], token_hash, session_expires_at),
    )

    return {
        "patient": patient,
        "session_token": token,
        "is_new_patient": is_new_patient,
    }


def get_patient_by_session_token(cur, token: str):
    """Resolve a bearer session token to its patient. Raises
    InvalidSession if the token is unknown, revoked, or expired (absolute
    SESSION_TTL_HOURS cap or WEB P10's SESSION_IDLE_TIMEOUT_MINUTES idle
    cap -- whichever is stricter for this session).

    A valid lookup also advances last_seen_at to now, so idle time is
    measured from the most recent authenticated request, not from
    login."""
    token_hash = _hash_token(token)

    cur.execute(
        """
        SELECT s.patient_id, s.expires_at, s.revoked_at, s.last_seen_at,
               p.name, p.whatsapp_number, p.hospital_id
        FROM patient_sessions s
        JOIN patients p ON p.id = s.patient_id
        WHERE s.token_hash = %s
        """,
        (token_hash,),
    )
    row = cur.fetchone()

    if row is None:
        raise InvalidSession()

    patient_id, expires_at, revoked_at, last_seen_at, name, whatsapp_number, hospital_id = row

    now = _now()
    idle_cutoff = now - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)

    if revoked_at is not None or now > expires_at or last_seen_at < idle_cutoff:
        raise InvalidSession()

    cur.execute(
        "UPDATE patient_sessions SET last_seen_at = %s WHERE token_hash = %s",
        (now, token_hash),
    )

    # hospital_id (M2): request-scoped tenant context, resolved here so
    # every endpoint depending on get_current_patient has it available --
    # not used to filter anything yet (see migrations/0024's own
    # docstring).
    return {
        "id": patient_id,
        "name": name,
        "whatsapp_number": whatsapp_number,
        "hospital_id": hospital_id,
    }


def revoke_session(cur, token: str) -> None:
    """Log out: revoke the session this token maps to, if any. Silently
    a no-op for an already-invalid token -- logout should never error."""
    token_hash = _hash_token(token)

    cur.execute(
        """
        UPDATE patient_sessions
        SET revoked_at = %s
        WHERE token_hash = %s
          AND revoked_at IS NULL
        """,
        (_now(), token_hash),
    )
