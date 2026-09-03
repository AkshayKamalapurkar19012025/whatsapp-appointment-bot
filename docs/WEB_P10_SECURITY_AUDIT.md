# WEB P10 — Security & Audit Pass — Report

Phase scope: the "P10 audit" / "security pass" referenced by name across
three earlier documents rather than defined fresh here —
`docs/WEB_EXPANSION_ARCHITECTURE.md` (§10 item 4, "not deferred to P4 or
the P10 audit"), `docs/WEB_P2_PATIENT_AUTHENTICATION.md` (absolute-only
session expiry, "flagged below as a P10 candidate"), and
`docs/WEB_P3_PATIENT_BOOKING_UI.md` ("CORS configuration ... explicitly
a WEB P10 topic"). This phase collects every item those reports pointed
here, plus a systematic pass over the whole web surface against the
standing global rules (no PHI/secret logging, every endpoint gated to
its intended audience), and closes what's actually a security gap
rather than re-litigating design decisions already made and shipped.

## PLAN

Compiled the audit's scope from three sources, not invented fresh:

1. **Named P10 candidates from prior reports:**
   - Session expiry is absolute-only (24h from login), no idle timeout —
     WEB P2's flag, restated identically for staff sessions in WEB P5's
     design (same `SESSION_TTL_HOURS` pattern, same gap).
   - CORS configuration for a non-proxied/separately-deployed frontend —
     WEB P3's flag; `frontend/vite.config.ts`'s dev proxy has kept this
     invisible in every phase's live/browser testing so far, since the
     browser never sees a cross-origin request through it.
2. **A systematic re-audit, not just the two named items** — grepping
   every logger call in `app/` for PHI/secret risk, and every route in
   `app/api/*.py` for its actual auth dependency, cross-checked against
   the RBAC table (`docs/WEB_EXPANSION_ARCHITECTURE.md` §8) and every
   phase's own stated scope decision (WEB P1's `DELETE` ownership fix,
   WEB P2/P5's session design, WEB P6's RBAC gates, WEB P9's appointments
   gate) — confirming each is still in place exactly as reported, not
   assuming a green test suite alone proves it.

**Findings, and what's in vs. out of this phase as a result:**

- **PHI/secret logging: clean, nothing to fix.** Every `logger.*` call
  in `app/` was inspected directly (not just grepped for keywords):
  `app/main.py`'s access log emits method, path, status, and timing only
  — `request.url.path` never contains PHI because every path parameter
  in this codebase is a numeric ID (confirmed by listing every
  path-parameterized route), and the access log deliberately does not
  include the query string, so `GET .../otp/_dev_lookup?whatsapp_number=...`
  never gets its query parameter logged either. Every other logger call
  (`appointment_services.py`, `availability_engine.py`, `booking.py`,
  `utils/timezone.py`) logs only internal IDs (`doctor_id`) or error
  text about a lookup/timezone failure — never a name, phone number, OTP
  code, password, or session token. This matches every individual
  phase's own logging claim; this pass is what actually re-verifies it
  holistically rather than phase-by-phase.
- **Auth completeness: clean, nothing to fix.** Every route in every
  router under `app/api/` was listed with its dependency (or lack of
  one) and checked against what that router's own phase report says it
  should be: patient-PHI endpoints (`patients.py`, and every
  `patient_booking.py` appointment endpoint) require the intended
  session type; every admin write (`departments`, `doctors`,
  `doctor_schedule`, `doctor_appointment_types`) requires ADMIN;
  `doctor_blocks` and `appointments.py` accept either staff role, per
  the RBAC table; every deliberately-public `GET` (department/doctor/
  appointment-type listings, `availability.py`'s slot lookup,
  `patient_booking.py`'s calendar) returns no PHI — doctor/department/
  slot data only, never a patient's name or number — so leaving them
  public is confirmed still correct, not merely unchanged.
- **CORS: a real gap, fixed this phase** (see IMPLEMENT).
- **Session idle timeout: a real gap, fixed this phase** (see
  IMPLEMENT).
- **Rate limiting beyond what already exists: considered, left as is.**
  OTP requests are rate-limited per number (WEB P2); staff login is
  rate-limited per account via the failed-attempt lockout (WEB P5). Both
  are the specific brute-force surfaces those phases' own credentials
  create. A general IP-based rate limiter would need shared state
  (Redis or equivalent) this project has no infrastructure for yet, and
  nothing in any phase's spec or gap analysis calls for one — adding
  infrastructure speculatively is exactly what this project's standing
  rules say not to do. Not fixed, because it isn't a gap relative to
  anything actually promised.
- **`reschedule_appointment_service` never checks `doctor_schedule`**
  (flagged, not fixed, in WEB P7's report): re-confirmed still true, and
  still out of scope here too — it's a booking-correctness gap (a
  reschedule can land outside a doctor's working hours), not a security
  one (no unauthorized access, no data exposure), so it doesn't belong
  in a security pass any more than it belonged in P7's date-range phase.
  Left flagged for whichever future phase actually owns booking-rule
  correctness.

## IMPLEMENT

**CORS** (`app/config.py`, `app/main.py`, `.env.example`):
`ALLOWED_ORIGINS` is a new comma-separated env var, empty by default,
parsed into a list. `app/main.py` adds `CORSMiddleware` with
`allow_origins=ALLOWED_ORIGINS`, `allow_credentials=False` (every
endpoint here is Bearer-token authenticated, never cookie-based, so
there's no ambient browser credential to protect against leaking
cross-site — the usual reason `allow_credentials=True` needs a strict
allowlist doesn't apply here, but the allowlist still matters: without
it, any website's JavaScript could call this API using a bearer token
it already has, e.g. stolen via XSS elsewhere, so `allow_origins`
defaults to empty, denying every cross-origin browser request rather
than defaulting open). No default value ships that would work
out-of-the-box for a separately-deployed frontend — a real deployment
choosing that shape must set `ALLOWED_ORIGINS` explicitly, matching
this project's existing "explicit opt-in, safe default" pattern
(`ENVIRONMENT=production` for dev-only endpoints, is the same shape).
`frontend/vite.config.ts`'s dev-time proxy and any same-origin
deployment need no entry here at all — the browser never sees a
cross-origin request through either.

**Session idle timeout** (`migrations/0008`,
`app/services/patient_auth.py`, `app/services/staff_auth.py`):
`migrations/0008_session_idle_timeout.sql` adds `last_seen_at` to both
`patient_sessions` and `staff_sessions`, backfilled to each existing
row's own `created_at` (not the migration's run time, so a session
issued long ago doesn't read as freshly active the moment this
migration runs). `get_patient_by_session_token()` and
`get_staff_by_session_token()` now check `last_seen_at` against a new
`SESSION_IDLE_TIMEOUT_MINUTES` cutoff *in addition to* the existing
absolute `expires_at` check — either one failing invalidates the
session — and advance `last_seen_at` to now on every successful lookup,
so the idle clock resets on activity rather than being fixed at login
time. Two different constants, not one shared value, each justified in
its module's own docstring: **staff, 30 minutes** — reaches the
admin/PHI-management surface, the higher-value target; **patient, 120
minutes** — a booking session can legitimately sit idle mid-flow
(reviewing dates, stepping away mid-form) and a stolen patient token's
blast radius is that one patient's own data, not the admin surface.
Both stay well under the existing 24-hour absolute cap, which is
unchanged — the idle timeout is a new, additional constraint, not a
replacement for the old one.

## TEST

`tests/test_p10_security.py`, 7 new tests:
- CORS: the shared test app (built with `ALLOWED_ORIGINS` unset, the
  real default) correctly omits `Access-Control-Allow-Origin` for a
  cross-origin request; a second test builds a minimal standalone app
  wired with the exact same `CORSMiddleware` call `app/main.py` uses to
  positively confirm an allowlisted origin *does* get the header and a
  non-allowlisted one doesn't — `ALLOWED_ORIGINS` is read once at
  `app.main` import time, so this is the only way to test the
  allow-when-listed path without reloading (and re-opening the DB pool
  of) the whole shared app; a third test confirms `app/config.py`'s
  comma-separated parsing (whitespace-trimmed, empty entries dropped)
  via a scoped `importlib.reload`.
- Idle timeout, for both staff and patient sessions: a session backdated
  past its idle cutoff (but still inside the 24h absolute cap) is
  rejected (401) on its next authenticated request; a session backdated
  to just inside the cutoff succeeds and its `last_seen_at` is confirmed
  (via a direct DB read) to have advanced past the backdated value,
  proving the idle clock actually resets on activity rather than being
  a one-time check.

Full backend suite: **154 passed, 0 failed** (147 pre-existing + 7 new),
real Postgres, run twice consecutively — no existing test needed
modification (the idle-timeout change is additive: every existing
session in every existing test is used within seconds of being created,
never idle long enough to trip the new check).

**Live smoke test**, not just pytest, against the real dev server and
dev database: started the server with
`ALLOWED_ORIGINS=https://example-frontend.test` and confirmed via curl
that a request with that `Origin` header gets
`access-control-allow-origin: https://example-frontend.test` back, while
one with `Origin: https://evil.test` gets no such header; logged in as
an existing staff account, confirmed `GET /api/auth/staff/me` succeeds,
then backdated that session's `last_seen_at` by 31 minutes directly in
the dev database and confirmed the identical request now returns 401.

## VERIFY

- Full backend suite: **154 passed, 0 failed**, real Postgres, run
  twice.
- Live smoke test against the dev server + dev database (CORS
  allow/deny, staff session idle-timeout rejection), described above.
- Migration applied cleanly to both the test database and the local dev
  database.
- Re-read every `logger.*` call site in `app/` directly and every
  route's auth dependency in `app/api/*.py` directly (not from memory
  of earlier phases) — both audits described under PLAN.

## REPORT

**Behavior change for WhatsApp:** none — `booking.py`'s webhook endpoint
and the WhatsApp conversational flow don't use CORS (no browser
involved) or either session table.

**Behavior change for the patient/staff web frontends:** none observed
in this phase's own testing (the dev proxy stays same-origin, so CORS
is a no-op for the existing dev/test setup) and none expected for a
same-origin production deployment either. A *separately*-deployed
frontend now requires `ALLOWED_ORIGINS` to be set — before this phase,
no CORS headers existed at all, so a separately-deployed frontend would
already have been silently broken by the browser's own same-origin
policy; this phase makes that configurable rather than introducing a
new restriction.

**Behavior change for any existing session:** a staff session now
becomes unusable after 30 minutes of inactivity, and a patient session
after 120 minutes, in addition to the existing 24-hour absolute cap.
Every session created by this project's own test suite is used well
within either window, so no existing test was affected; a real
long-idle browser tab would newly need to log in again where it
previously wouldn't have for up to 24 hours.

**New, fully tested and live-verified:**
- Configurable CORS via `ALLOWED_ORIGINS`, deny-by-default.
- Session idle timeout for both patient and staff sessions, layered on
  the existing absolute cap.
- A holistic PHI/secret-logging and auth-completeness audit across the
  entire `app/` tree (not just each phase's own new code), with its
  findings recorded above rather than only implied by "the tests still
  pass."

**Not in this phase, on purpose:**
- IP-based / general rate limiting — no gap identified relative to any
  phase's actual spec; would require new shared-state infrastructure
  this project doesn't otherwise need. See PLAN.
- `reschedule_appointment_service`'s missing `doctor_schedule` check —
  a booking-correctness gap, not a security one; still flagged from WEB
  P7, still not this phase's to fix.
- Any admin-facing frontend UI for security settings (session policy,
  CORS) — this phase is backend/config-only, matching every other
  admin-surface phase's own precedent before a frontend existed for it.
- Rotating or shortening the absolute 24-hour session TTL itself — not
  flagged as a gap by any prior phase (only the *lack of an idle
  timeout* was), and 24 hours matches both session types' original,
  deliberate design in WEB P2/P5.

## STOP

Per the phase-gate rule: stopping here. Waiting for approval before WEB
P11 begins.
