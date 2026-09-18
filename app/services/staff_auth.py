"""
Staff/admin authentication: username + password login (WEB P5).

Mirrors app/services/patient_auth.py's shape (session tokens hashed the
same way, same "opaque DB-backed token" design from
docs/WEB_EXPANSION_ARCHITECTURE.md section 7) but is a genuinely separate
subsystem, not a generalization of it: staff log in with a password, not
an OTP, and a staff session must never be usable to authenticate a
patient-facing endpoint or vice versa -- hence a fully separate table
(staff_sessions) and module, not a shared "sessions" table with a
subject_type discriminator (one of the two options
docs/WEB_EXPANSION_ARCHITECTURE.md section 5 left open; picked in favor
of the simpler, more obviously-safe-by-construction option).

Security notes:
  - Passwords are hashed with argon2id (the current OWASP-recommended
    default), never stored or logged in plaintext.
  - Session tokens are hashed (SHA-256) before storage, same as patient
    sessions -- a database read alone must never hand out something
    directly usable to log in.
  - login() takes the same amount of work whether the username is
    unknown or the password is wrong (see the comment at that branch),
    so a timing difference can't be used to enumerate valid usernames.
  - Repeated failed attempts temporarily lock an account
    (failed_login_count / locked_until on the `staff` row), the same
    brute-force defense pattern as patient_otp_codes.attempt_count,
    adapted to a password-login context.
  - Deactivating a staff account (app/services/staff_management.py)
    checks live in get_staff_by_session_token -- an account disabled
    mid-session is locked out on its very next request, not just at its
    next login.
  - Sessions expire on two independent clocks (WEB P10): an absolute cap
    (SESSION_TTL_HOURS from login) and a shorter idle cap
    (SESSION_IDLE_TIMEOUT_MINUTES since the last authenticated request) --
    whichever is stricter wins. Before P10 only the absolute cap existed,
    so a stolen-but-unused token stayed valid for the full 24 hours
    regardless of activity.
  - Nothing in this module calls into app.logging_config's loggers with
    a raw password, password hash, or session token.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.services.exceptions import (
    InvalidCredentials,
    StaffAccountLocked,
    StaffAccountInactive,
    InvalidSession,
)

SESSION_TTL_HOURS = 24
SESSION_IDLE_TIMEOUT_MINUTES = 30
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

_hasher = PasswordHasher()


def _now():
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    """Used by app/services/staff_management.py when creating an
    account -- kept here (not duplicated) since it's the same argon2
    instance/config login() verifies against."""
    return _hasher.hash(password)


def login(cur, username: str, password: str) -> dict:
    """
    Verify a staff username+password and issue a session token.

    Returns {"staff": {"id", "username", "role"}, "session_token": str}
    on success.

    Raises InvalidCredentials for an unknown username OR a wrong
    password for a known username -- deliberately the same exception
    (and, at the API layer, the same message) for both, so a failed
    login never discloses whether a given username exists. This is a
    stricter anti-enumeration posture than patient OTP, which is
    appropriate here: OTP already deals with a self-registering
    population where "does this phone number exist" isn't sensitive the
    same way "does this admin account exist" is.

    Raises StaffAccountLocked if too many recent failed attempts have
    temporarily locked the account (checked before touching the
    password at all, so a locked-out attacker can't keep spending
    guesses).

    Raises StaffAccountInactive if the username and password are BOTH
    correct but the account has been deactivated. This is deliberately
    distinguishable from InvalidCredentials, unlike the unknown-username
    case above: knowing "this account exists but is disabled" doesn't
    help an attacker guess a password (they'd already have needed the
    correct one to reach this branch), and a legitimate deactivated
    staff member benefits from a message that tells them to contact an
    admin instead of assuming they mistyped their password.
    """
    username_normalized = username.strip().lower()

    cur.execute(
        """
        SELECT id, username, password_hash, role, active,
               failed_login_count, locked_until
        FROM staff
        WHERE username = %s
        FOR UPDATE
        """,
        (username_normalized,),
    )
    row = cur.fetchone()

    if row is None:
        # Burn roughly the same amount of CPU as a real password check
        # below, so "unknown username" and "known username, wrong
        # password" aren't distinguishable by response time.
        _hasher.hash(password)
        raise InvalidCredentials()

    staff_id, db_username, password_hash, role, active, failed_count, locked_until = row

    if locked_until is not None:
        if _now() < locked_until:
            raise StaffAccountLocked()
        # The lockout has expired -- give a fresh attempt window rather
        # than immediately re-locking on the next single failure.
        failed_count = 0

    try:
        _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        new_failed_count = failed_count + 1
        new_locked_until = (
            _now() + timedelta(minutes=LOCKOUT_MINUTES)
            if new_failed_count >= MAX_FAILED_LOGIN_ATTEMPTS
            else None
        )
        cur.execute(
            "UPDATE staff SET failed_login_count = %s, locked_until = %s WHERE id = %s",
            (new_failed_count, new_locked_until, staff_id),
        )
        raise InvalidCredentials()

    if not active:
        raise StaffAccountInactive()

    cur.execute(
        "UPDATE staff SET failed_login_count = 0, locked_until = NULL WHERE id = %s",
        (staff_id,),
    )

    token = _generate_session_token()
    token_hash = _hash_token(token)
    session_expires_at = _now() + timedelta(hours=SESSION_TTL_HOURS)

    cur.execute(
        """
        INSERT INTO staff_sessions (staff_id, token_hash, expires_at)
        VALUES (%s, %s, %s)
        """,
        (staff_id, token_hash, session_expires_at),
    )

    return {
        "staff": {"id": staff_id, "username": db_username, "role": role},
        "session_token": token,
    }


def get_staff_by_session_token(cur, token: str) -> dict:
    """Resolve a bearer session token to its staff account. Raises
    InvalidSession if the token is unknown, revoked, expired (absolute
    SESSION_TTL_HOURS cap or WEB P10's SESSION_IDLE_TIMEOUT_MINUTES idle
    cap -- whichever is stricter for this session), or the account has
    since been deactivated -- the live `active` check means deactivating
    an account invalidates every one of its existing sessions
    immediately, not just future logins.

    A valid lookup also advances last_seen_at to now, so idle time is
    measured from the most recent authenticated request, not from
    login."""
    token_hash = _hash_token(token)

    cur.execute(
        """
        SELECT s.staff_id, s.expires_at, s.revoked_at, s.last_seen_at,
               st.username, st.role, st.active, st.hospital_id
        FROM staff_sessions s
        JOIN staff st ON st.id = s.staff_id
        WHERE s.token_hash = %s
        """,
        (token_hash,),
    )
    row = cur.fetchone()

    if row is None:
        raise InvalidSession()

    staff_id, expires_at, revoked_at, last_seen_at, username, role, active, hospital_id = row

    now = _now()
    idle_cutoff = now - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)

    if revoked_at is not None or now > expires_at or not active or last_seen_at < idle_cutoff:
        raise InvalidSession()

    cur.execute(
        "UPDATE staff_sessions SET last_seen_at = %s WHERE token_hash = %s",
        (now, token_hash),
    )

    # hospital_id (M2): request-scoped tenant context, resolved here so
    # every endpoint depending on get_current_staff has it available --
    # not used to filter anything yet (see migrations/0024's own
    # docstring).
    return {"id": staff_id, "username": username, "role": role, "hospital_id": hospital_id}


def revoke_session(cur, token: str) -> None:
    """Log out: revoke the session this token maps to, if any. Silently
    a no-op for an already-invalid token -- logout should never error."""
    token_hash = _hash_token(token)

    cur.execute(
        """
        UPDATE staff_sessions
        SET revoked_at = %s
        WHERE token_hash = %s
          AND revoked_at IS NULL
        """,
        (_now(), token_hash),
    )
