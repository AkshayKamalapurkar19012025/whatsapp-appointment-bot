# WEB P2 — Patient Authentication — Report

Phase scope: mobile number → OTP → existing patient login / new patient
registration, mock OTP, OTP expiration, no reuse, rate limiting, secure
session/token handling, no OTP logging, existing patient lookup by verified
mobile number, patient ID stays internal, reuse existing patient fields.

## Decision made before implementing

Session transport was the one item explicitly left open at the end of WEB
P1: **Bearer token in the `Authorization` header**, decided this round —
works regardless of same-origin vs. separate frontend deploy, no CSRF
token needed. Everything below is built on that.

## PLAN

1. New migration (`0004`): `patient_otp_codes` (hashed verification
   record), `patient_sessions` (hashed bearer tokens), `mock_sms_outbox`
   (the mock provider's plaintext delivery log — a different, deliberately
   separate mechanism from the hashed verification record, and from the
   application's own logger).
2. `app/services/patient_auth.py`: `request_otp`, `verify_otp` (handles
   the login/registration branch), `get_patient_by_session_token`,
   `revoke_session`. Typed exceptions added to the existing
   `app/services/exceptions.py` (renamed its base class
   `AppointmentServiceError` → `ServiceError`, since it now covers auth
   too — a same-file, no-other-usages rename, not a wider refactor).
3. `app/api/patient_auth.py`: the router, plus `get_current_patient`, a
   FastAPI dependency exposed for WEB P4's future patient-facing booking
   endpoints to reuse — a patient's identity must always come from a
   verified session, never a client-supplied `patient_id`, which is what
   "patient ID remains internal" means in practice here.
4. `app/config.py`: `ENVIRONMENT` (defaults to `"development"`), gating
   the dev-only OTP lookup endpoint.
5. Tests covering every case the phase spec lists by name, plus two it
   implies: the `registration_required` intermediate state, and OTP
   locking after too many wrong attempts.

## IMPLEMENT

**New files:**
- `migrations/0004_patient_authentication.sql`
- `app/services/patient_auth.py`
- `app/api/patient_auth.py`
- `tests/test_patient_auth.py` (11 tests)
- `docs/WEB_P2_PATIENT_AUTHENTICATION.md` (this report)

**Modified:**
- `app/services/exceptions.py` — 8 new exceptions
  (`OtpRateLimited`, `OtpNotFound`, `OtpExpired`, `OtpAlreadyUsed`,
  `OtpLocked`, `OtpInvalid`, `RegistrationRequired`, `InvalidSession`);
  base class renamed `AppointmentServiceError` → `ServiceError`.
- `app/config.py` — added `ENVIRONMENT`.
- `app/main.py` — mounted the new router.
- `tests/conftest.py` — added the three new tables to `APP_TABLES` so
  they're truncated between tests (required for rate-limit test
  isolation).

**Endpoints (`/api/auth/patient/...`):**
- `POST otp/request` — `{whatsapp_number}` → `200 {"message": "OTP sent"}`,
  or `429` if rate-limited (3 requests / 10 minutes per number).
- `POST otp/verify` — `{whatsapp_number, otp, name?}` →
  `200 {patient, session_token, is_new_patient}` on success;
  `200 {"registration_required": true}` if the code is correct but no
  patient exists yet and no name was given (OTP left unconsumed so the
  same code works on the follow-up call with a name); `401` for
  not-found/expired/already-used/locked/invalid.
- `GET me` — Bearer token required → `200` patient info, `401` otherwise.
- `POST logout` — revokes the current session; always `200` (logging out
  with no/invalid session is treated as already-achieved, not an error).
- `GET otp/_dev_lookup?whatsapp_number=...` — dev/test-only, `404` when
  `ENVIRONMENT=production` (chosen over `403` so the endpoint's existence
  isn't revealed in production either).

**Security choices, each with a stated reason (not defaults I didn't
think about):**
- OTP codes and session tokens hashed (SHA-256) before storage — a DB
  read alone can never hand out something directly usable.
- No per-row salt — the 5-minute TTL, 5-attempt cap, and 3-per-10-minute
  request rate limit already bound the attacker; documented in the
  migration and service module rather than left implicit.
- `secrets`, not `random`, for both code and token generation.
- Session expiry is absolute (24 hours) — no idle-timeout refinement in
  this phase; flagged below as a P10 candidate, not silently skipped.

## Zero-logging verification (not assumed)

`grep -n "logger\." app/services/patient_auth.py app/api/patient_auth.py`
returns nothing — neither file calls a logger at all, so there is no code
path that could log an OTP code or session token. `mock_sms_outbox` is a
dedicated table for the mock provider's own delivery record, not the
application log — it exists specifically so a code needs *some* durable,
plaintext place to live for testing purposes, without that place being
the log stream PHI-safe logging discipline (`app/logging_config.py`)
governs.

## A bug found and fixed during TEST (not just written up)

The OTP-attempt-lockout test initially failed: after `OTP_MAX_VERIFY_ATTEMPTS`
wrong attempts, a correct code still succeeded (200, not the expected 401
locked-out response). Root cause: `app/db/connection.py`'s connection
context manager commits on a clean exit and **rolls back on any
exception** — and `verify_otp`'s `attempt_count` increment was being
undone every time, because the router raised `HTTPException` from inside
that same `with get_connection()` block, which counts as an exception
exiting it. Fixed by committing explicitly before each such
`raise HTTPException` in `otp_verify` (matching the precedent already set
in `app/api/booking.py`'s `ExclusionViolation` handling, which does the
same for the same reason). Documented inline at the fix site so the next
person adding an error branch here doesn't reintroduce it.

## TEST

`tests/test_patient_auth.py`, 11 tests, matching the phase spec's own
list one-for-one plus two implied cases:
valid OTP (new patient) · verify-without-name → registration_required,
same code retried with a name succeeds · existing patient logs in without
a duplicate row · invalid OTP · expired OTP (manually backdated
`expires_at`) · reused OTP · OTP locked after max wrong attempts · OTP
request rate limiting · unauthorized requests to `/me` (no header, bogus
token) · `/me` + `/logout` end-to-end, session invalid immediately after
logout · dev-lookup endpoint disabled under `ENVIRONMENT=production`.

## VERIFY

- Full suite: **67 passed, 0 failed** (56 pre-existing + 11 new), real
  local Postgres.
- Live smoke test against a running server: request → dev-lookup → verify
  → `/me` → logout → `/me` (401), all behaving as designed.
- **Cross-channel proof, live, not just asserted**: the same phone number
  used for a Web OTP login was recognized by the WhatsApp flow immediately
  afterward as the identical existing patient record (same `id`,
  "Welcome back" greeting) — direct confirmation that Web and WhatsApp
  share the same patient data, per the product spec's explicit requirement.
- `python -c "import app.main"` sanity check.

## REPORT

**Behavior change for WhatsApp or the existing REST API:** none. Nothing
in this phase touches `booking.py`, `appointments.py`,
`availability.py`, or their services.

**New capability, fully wired and tested:** patient mobile+OTP
authentication end-to-end, including registration for new numbers and
login for existing ones, sharing the exact same `patients` table
WhatsApp already writes to.

**Ready for WEB P4:** `get_current_patient` (a FastAPI dependency) is the
one thing a future patient-facing booking/cancel/reschedule endpoint
needs to depend on to get a verified `patient_id` — which is exactly the
value WEB P1's `cancel_appointment_service(..., requesting_patient_id=...)`
was built to accept. Wiring those two together is what actually closes
the `DELETE /api/appointments/{id}` ownership gap; that wiring is P4's
job, not this one's, since P4 is where the endpoint calling both exists.

**Flagged, not silently deferred:**
- Session expiry is absolute only (24h); no idle timeout. Candidate for
  WEB P10's security pass, not a gap I'm pretending doesn't exist.
- `mock_sms_outbox` is scoped to OTP delivery only, on purpose — WEB P8
  generalizes it for booking-confirmation SMS. Documented in the
  migration so P8 doesn't have to rediscover why the table's this shape.

## STOP

Per the phase-gate rule: stopping here. Waiting for approval before WEB
P3 (Patient Booking UI — the first phase that touches the frontend)
begins.
