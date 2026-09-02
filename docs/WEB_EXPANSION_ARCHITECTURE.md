# WEB P0 — Discovery / Architecture Report

Status: **read-only discovery deliverable**. No application code was modified to produce this
report. Per the phase gate, implementation does not begin until this report is explicitly
approved.

Scope of this document: map what exists today, identify gaps against the Web + WhatsApp
expansion requirements, and propose (not implement) a target architecture, API surface,
database changes, frontend technology, authentication/RBAC design, phased implementation
plan, risks, and testing strategy.

---

## 1. Architecture map (current state)

### 1.1 Repo shape
- Pure Python backend. **No frontend exists anywhere in the repo** — no `package.json`,
  `.tsx`/`.jsx`, `vite.config.*`, `next.config.*`, or any static asset pipeline. Nothing to
  reuse or conflict with.
- `requirements.txt` / `requirements-dev.txt` contain **zero** auth-related packages (no
  `passlib`, `bcrypt`, `argon2`, `pyjwt`, `authlib`, `itsdangerous`, session-store client,
  etc.) and zero frontend build tooling.
- FastAPI app (`app/main.py`), 12 routers mounted under `/api`, all currently **unauthenticated**
  — every endpoint is open to anyone who can reach the process. There is no auth middleware,
  no session concept, no user/staff table.

### 1.2 Routers / endpoints (confirmed live via `/openapi.json`)
| Router | Prefix | Purpose |
|---|---|---|
| `health` | `/api` | liveness |
| `appointment_types` | `/api/appointment-types` | CRUD, just wired in P3 |
| `departments` | `/api/departments` | CRUD |
| `doctors` | `/api/doctors` | CRUD |
| `doctor_schedule` | `/api/doctors/{id}/schedule` | weekly recurring hours (day_of_week, start_time, end_time — no date range) |
| `doctor_blocks` | `/api/doctors/{id}/blocks` | one-off unavailability windows |
| `availability` | `/api/availability` | computed open slots for a doctor/date/appointment-type |
| `patients` | `/api/patients` | CRUD |
| `appointments` | `/api/appointments` | create/list/cancel (REST path, concurrency-hardened) |
| `department_doctors` | `/api/departments/{id}/doctors` | join table CRUD |
| `doctor_appointment_types` | `/api/doctors/{id}/appointment-types` | per-doctor duration override |
| `booking` | `/api/booking` | the WhatsApp conversational state machine (single POST endpoint, `booking.py`, ~4,000 lines) |

**No REST reschedule endpoint exists anywhere.** Reschedule logic lives only inside
`booking.py`'s conversational state machine (`RESCHEDULE_SELECT` → `RESCHEDULE_CONFIRM` →
`RESCHEDULE_FINAL_CONFIRM`), operating directly on the WhatsApp session/patient identified by
phone number. A web UI reschedule flow has no existing backend primitive to call.

### 1.3 Data model (current schema, 10 tables, confirmed via live `\d`)
`departments`, `doctors` (has `timezone`), `appointment_types`, `department_doctors`,
`doctor_appointment_types` (duration_minutes per doctor/type), `doctor_schedule`
(day_of_week + start_time/end_time, **no start_date/end_date — a schedule row is either
"forever" or toggled off via `active`, not date-ranged**), `doctor_blocks` (start_at/end_at,
one-off), `patients` (identified by `whatsapp_number`, no password/auth field), `appointments`
(doctor_id, patient_id, appointment_type_id, start_at, end_at, status, plus the P0/concurrency
work: `EXCLUDE USING gist` constraint, partial indexes), and the WhatsApp session table backing
`booking.py`'s conversation state.

### 1.4 Booking/availability logic
Two independent implementations exist today:
- `app/api/availability.py` — REST endpoint, used by nothing internal except itself; computes
  slots for one date at a time.
- `app/api/booking.py` — inlines its own slot computation and its own overlap/lock logic for
  the conversational flow, duplicated (not shared) with `availability.py` prior to the P1
  consolidation of `overlaps()`/`get_doctor_timezone()` into `app/utils/timezone.py`. The
  **slot-generation algorithm itself** (walking a doctor's schedule and blocks to produce
  candidate slots) is still two separate code paths, not one shared function.

### 1.5 Concurrency/consistency guarantees (from prior phases — must be preserved unchanged)
Both the REST path (`appointments.py`) and the WhatsApp path (`booking.py`) now:
1. take `pg_advisory_xact_lock(doctor_id::bigint)` at the same relative point in their flow,
2. re-check overlaps under that lock,
3. fall back on a database-level `EXCLUDE USING gist` constraint
   (`migrations/0003_prevent_overlapping_bookings.sql`) as a structural backstop,
4. handle `psycopg.errors.ExclusionViolation` identically (log doctor_id only, return the
   pre-existing friendly 409/conversational message).

This is validated by `tests/test_concurrency.py` and `tests/test_exclusion_constraint.py`
(30/30 and 15/15 clean runs respectively) and **any new booking-creation code path (web UI →
API) must go through the same `appointments.py` creation logic, not a third parallel
implementation**, or this guarantee silently stops applying to it.

### 1.6 Timezone handling
Every doctor has an IANA `timezone` column; `app/utils/timezone.py` is the single canonical
home for overlap math and timezone lookup as of P1. Slot times are computed/returned in the
doctor's local timezone; there's no per-patient timezone anywhere (WhatsApp doesn't need one —
messages just show times as text).

### 1.7 Test/dev infra
`tests/conftest.py` forces a `*_test` DB suffix, runs `scripts/migrate.py` once per session,
truncates between tests, session-scoped `TestClient` (required because `psycopg_pool` can only
open/close once per process). `docker-compose.yml` + `scripts/provision_local_db.sh` +
`SETUP.md` give a reproducible local Postgres. All of this is reusable as-is for web work; no
changes needed to support it.

---

## 2. Gap analysis

| # | Requirement | Current state | Gap |
|---|---|---|---|
| 1 | Patient web auth (mobile + OTP) | No patient auth at all; patients identified only by WhatsApp number at message-time | Full auth subsystem: OTP issuance/verification, session tokens, patient login endpoint |
| 2 | Admin/staff auth (username+password+RBAC) | No staff/user table, no roles, no login | Full staff auth subsystem + RBAC table/columns |
| 3 | `DELETE /api/appointments/{id}` authorization | **Any caller can cancel any appointment today — zero ownership check.** This is a pre-existing gap, not new. | Must be closed as part of introducing patient auth, or the web UI inherits/exposes it |
| 4 | Recurring doctor schedule with date range | `doctor_schedule` has day_of_week + times only, no start_date/end_date | Schema change: add `start_date`, `end_date` (nullable = open-ended) to `doctor_schedule` |
| 5 | Calendar: current month + next 3 months, backend-enforced | No booking-window concept exists anywhere — WhatsApp flow offers a small rolling set of dates with no explicit "how far ahead" ceiling enforced server-side | New backend rule: reject/hide dates outside the allowed window, shared by both availability paths |
| 6 | Unified availability engine | Two divergent implementations (`availability.py` vs `booking.py` inline logic) | Consolidate into one shared slot-generation function both WhatsApp and web (and REST) call |
| 7 | Non-hardcoded slot durations | Already correct — `doctor_appointment_types.duration_minutes` is already per-doctor/per-type, not hardcoded | No gap |
| 8 | Mock OTP/SMS | Nothing exists | New mock provider + a way for the requester to retrieve the code in dev/test (queryable log, not real SMS) |
| 9 | PHI/ePHI logging discipline | Already established in P1 (`logging_config.py` never logs PII) | Must extend the same discipline to new auth/OTP code — never log OTP codes, passwords, tokens |
| 10 | Reschedule via web | No REST reschedule endpoint exists; logic is trapped inside the conversational state machine | New REST reschedule endpoint needed, built on the same lock/exclusion-constraint pattern as `appointments.py`'s create path |
| 11 | Admin doctor/schedule management UI | Backend CRUD already exists (`doctors`, `doctor_schedule`, `doctor_blocks`, `department_doctors`, `doctor_appointment_types`) | Needs auth/RBAC gating added to existing routers; frontend needed; recurring-with-date-range needs the schema change in #4 |
| 12 | Frontend (patient + admin) | Does not exist | Net-new build |

---

## 3. Proposed target architecture

```
                    ┌─────────────────────┐
WhatsApp  ────────▶ │ booking.py (unchanged)│───┐
(existing, frozen)  └─────────────────────┘    │
                                                 ▼
                                     ┌─────────────────────────┐
                                     │  shared booking/         │
                                     │  availability engine     │
                                     │  (app/services/*)        │
                                     └─────────────────────────┘
                                                 ▲
                    ┌─────────────────────┐    │
Patient Web UI ───▶ │ /api/web/* (new)     │────┤
                    └─────────────────────┘    │
                    ┌─────────────────────┐    │
Admin/Staff Web UI─▶│ /api/admin/* (new)   │────┘
                    └─────────────────────┘
                              │
                              ▼
                     existing appointments/
                     doctors/schedule tables
                     + new auth tables
```

Key principle: **the WhatsApp conversational flow is not touched.** New web-facing endpoints
call into the *same* underlying appointment-creation/cancellation/reschedule logic
(`appointments.py`, extended with a reschedule endpoint) rather than re-implementing booking
rules a third time. Where `booking.py` currently inlines logic that the web path also needs
(slot generation), that logic gets extracted into a shared `app/services/` module that both
`booking.py` and the new web endpoints import — `booking.py`'s behavior stays byte-identical
because the extraction is a pure move, not a rewrite (same pattern already used successfully
for `overlaps()`/`get_doctor_timezone()` in P1).

New pieces:
- `app/api/auth_patient.py` — mobile+OTP login for patients
- `app/api/auth_staff.py` — username+password login for staff/admin
- `app/api/admin_*.py` (or reuse existing CRUD routers with auth dependencies added)
- `app/services/otp.py` — mock OTP/SMS provider (swappable)
- `app/services/availability_engine.py` — the consolidated slot-generation logic
- `app/middleware/auth.py` — session validation dependency, RBAC dependency
- Frontend: two separate SPAs (or one app with two route trees) — see §6

---

## 4. Proposed API list (new/changed)

**Patient auth**
- `POST /api/auth/patient/otp/request` — {mobile} → sends mock OTP
- `POST /api/auth/patient/otp/verify` — {mobile, otp} → session token, creates patient record if new
- `POST /api/auth/patient/logout`

**Staff auth**
- `POST /api/auth/staff/login` — {username, password} → session token
- `POST /api/auth/staff/logout`

**Patient-facing booking (thin wrappers around the shared engine, auth-required)**
- `GET /api/web/departments`, `GET /api/web/doctors`, `GET /api/web/appointment-types` (read-only, could reuse existing routers directly if made auth-optional for GET)
- `GET /api/web/availability?doctor_id&month` — calendar-month view honoring the current+3-month window server-side
- `POST /api/web/appointments` — create (delegates to `appointments.py` logic)
- `GET /api/web/appointments/me` — patient's own appointments only
- `PATCH /api/web/appointments/{id}/reschedule` — **new**, built on the same lock pattern
- `DELETE /api/web/appointments/{id}` — cancel, ownership-checked (closes gap #3)

**Admin/staff**
- Existing CRUD routers (`doctors`, `departments`, `doctor_schedule`, `doctor_blocks`,
  `appointment_types`, `doctor_appointment_types`, `department_doctors`) gain an auth+RBAC
  dependency; no route shape changes needed for most.
- `doctor_schedule` gains optional `start_date`/`end_date` in its create/update body.
- `GET /api/admin/appointments` (dashboard list/filter — existing `appointments.py` GET can
  likely be reused/extended with query filters rather than duplicated).

---

## 5. Proposed database changes

1. `doctor_schedule`: add `start_date DATE NULL`, `end_date DATE NULL` (NULL = open-ended, matches "either forever or bounded" requirement). Additive, nullable, no backfill risk to existing rows.
2. New `staff` table: id, username (unique), password_hash, role, active, timestamps. Never
   store plaintext; hash with a vetted library added to requirements (e.g. `argon2-cffi` or
   `passlib[bcrypt]` — needs a dependency-addition decision, see open items).
3. New `patient_sessions` / `staff_sessions` (or one shared `sessions` table with a `subject_type`
   discriminator) — opaque token, expiry, subject id. Recommended over JWT (see §7).
4. New `otp_codes` table — mobile, code_hash, expires_at, consumed_at, for the mock provider;
   never store the raw code past issuance in logs.
5. Possibly a `role_permissions` table, or a simpler hardcoded permission-matrix-in-code
   approach if the RBAC surface stays small — see §8 and open item.

All of these are additive migrations (new tables, nullable new columns) — zero risk to
existing WhatsApp data or behavior, consistent with the project's established "new migration
file per change, never edit an existing one" convention (`migrations/000N_*.sql`).

---

## 6. Proposed frontend technology

**Recommended: React + TypeScript + Vite**, two route trees (`/patient/*`, `/admin/*`) in one
app, or two separate Vite apps sharing a small component/util package if the team prefers
fully independent deploys. Reasoning: calendar UI with backend-enforced date constraints and
light-orange unavailable-date styling, multi-step forms, and an admin dashboard all benefit
from componentization and type safety against the API contracts above.

**Lower-complexity alternative: server-rendered Jinja2 + HTMX**, staying inside the existing
Python/FastAPI stack with no separate build pipeline, no `npm`/Node dependency, and no new
deploy artifact. Viable given the UI surface described (forms, a calendar, a dashboard table)
isn't especially JS-heavy. Tradeoff: less capable calendar interactivity, more server-side
templating code.

This is listed as an **open decision** (see §10) — I'm not choosing between them without your
input, since it affects the whole implementation plan's shape.

---

## 7. Proposed authentication design

- **Session model**: DB-backed opaque random tokens (not JWT). Rationale: this app already
  has Postgres as its source of truth for everything; opaque tokens let a session be revoked
  instantly (delete the row) — important for a PHI-adjacent system — whereas JWT revocation
  needs an extra denylist mechanism that duplicates the same DB round-trip anyway, forfeiting
  JWT's main advantage (statelessness) while keeping its main drawback (revocation lag).
  Token delivered as an `Authorization: Bearer <token>` header (simplest to reason about for a
  separate SPA origin; cookie-based sessions are the alternative if same-origin serving is
  chosen — depends on the frontend-serving decision in §6/§10).
- **Patient OTP**: `POST otp/request` generates a 6-digit code, stores only its hash +
  expiry, and — since this is a *mock* provider — writes it to a queryable table/log instead
  of a real SMS gateway, so the frontend or a test can retrieve it without embedding the code
  directly in the API response (which would defeat the point of an OTP even in mock form).
  Rate-limited per mobile number to prevent brute-force (small in-window attempt counter).
- **Staff login**: username + password, hashed with a standard KDF (bcrypt/argon2 — needs a
  new dependency, see open items), same session-token mechanism as patients but a distinct
  `role` on the session so RBAC checks are a single lookup.
- **PHI discipline**: OTP codes, password hashes, and session tokens are never written to
  application logs — extending the exact discipline already established in
  `app/logging_config.py` for P1.

---

## 8. Proposed RBAC

Two staff roles, mirroring the ADMIN/RECEPTIONIST distinction implied by the original P0
build prompt:

| Capability | ADMIN | STAFF/RECEPTIONIST |
|---|---|---|
| View appointments/dashboard | ✓ | ✓ |
| Create/cancel/reschedule appointments (on behalf of a patient) | ✓ | ✓ |
| Manage doctors, departments, appointment types | ✓ | ✗ (read-only) |
| Manage a doctor's recurring schedule / date-ranged availability | ✓ | ✗ or ✓ — **ambiguous, needs your decision** (see §10) |
| Manage doctor blocks (one-off unavailability) | ✓ | ✓ (likely day-to-day front-desk task) |
| Create/deactivate staff accounts | ✓ | ✗ |

Enforcement: a FastAPI dependency (`require_role("ADMIN")` / `require_staff()`) applied per
route, checked server-side on every request — never trusting a frontend-only role check, per
your global rules.

---

## 9. Proposed implementation plan (phases WEB P1 onward, for your approval)

This is a proposal for how to sequence the phases you've already defined (WEB P1–P11), not a
redefinition of them:

1. **Auth foundation** — staff + patient auth tables/migrations, session middleware, RBAC
   dependency, mock OTP service. No UI yet — REST-testable via `httpx`/pytest, same rigor as
   existing test suite.
2. **Shared availability engine extraction** — pure refactor: move `booking.py`'s slot
   generation into `app/services/availability_engine.py`, have both `booking.py` and
   `availability.py` call it. Zero behavior change, verified by full existing regression suite
   passing unchanged (same discipline as the P1 timezone-util extraction).
3. **Booking-window + calendar-month rule** — add the current+3-month enforcement to the
   shared engine, backend-enforced, with tests for the boundary (last day of month 3, first
   day of month 4).
4. **REST reschedule endpoint** — new, built on the appointments.py lock/exclusion-constraint
   pattern, with its own concurrency test (same rigor as Test C/D from the prior phase).
5. **Patient web API** — the `/api/web/*` endpoints wrapping the shared engine + reschedule
   endpoint, ownership checks closing the `DELETE` authorization gap.
6. **Admin web API** — RBAC-gated access to existing CRUD routers + the new
   `doctor_schedule` date-range fields.
7. **Frontend build** — patient UI, then admin UI (or in parallel if resourced), against the
   now-stable API surface.
8. **End-to-end verification** — full regression (existing WhatsApp suite + new web suite),
   manual browser walkthrough of both UIs' golden paths and edge cases.

Each of these could map to one or more of your already-defined WEB P1–P11 phases; I'd rather
you tell me how you want them grouped than guess, since you've reserved that structure
explicitly.

---

## 10. Open items requiring your decision before implementation

1. **Frontend stack**: React+TS+Vite vs. Jinja2+HTMX (§6).
2. **Session transport**: Bearer token header vs. cookie — depends on whether the frontend is
   served same-origin by FastAPI (e.g. via `StaticFiles`) or as a fully separate deployed app.
3. **New dependencies**: password hashing needs a new library (`argon2-cffi` or
   `passlib[bcrypt]`) — none exists today. Confirm you want a new dependency added (consistent
   with your "database changes must be justified" rule extended to new packages).
4. **STAFF role and recurring-schedule management**: can STAFF/RECEPTIONIST edit a doctor's
   recurring weekly schedule, or is that ADMIN-only? (Table in §8 flags this as ambiguous.)
5. **`doctor_schedule` date-range semantics**: does a *new* date-ranged schedule row coexist
   with old open-ended rows for the same doctor/day (e.g. an override), or does creating one
   require deactivating the open-ended row first? Affects both schema and admin UX.
6. **Mock OTP delivery mechanism for the frontend**: a dev-only endpoint to fetch the last
   code for a mobile number, vs. always echoing it in a non-production-only response field, vs.
   something else. Needs a decision so it's mock-but-realistic without weakening the pattern
   for a future real-provider swap.
7. **Patient identity linkage to WhatsApp**: should a patient who registers on the web with
   mobile number X be the *same* patient record as one who has an existing WhatsApp history
   under that number, or deliberately separate? (Affects whether "my appointments" on the web
   shows WhatsApp-booked appointments too.)
8. **Existing `DELETE /api/appointments/{id}` gap**: fix ownership-checking on the *existing*
   endpoint too (closing a pre-existing security hole immediately), or leave the legacy REST
   endpoint as-is and only enforce ownership on the new `/api/web/*` wrapper? Recommend fixing
   the underlying endpoint since it's the actual point of enforcement no matter which endpoint
   a UI calls.
9. **Same-origin vs. separate deploy** for the frontend(s) — affects CORS configuration and
   session-transport decision (#2).
10. **Booking window edge case**: "current month + next 3 calendar months" — does that mean
    exactly the last calendar day of month+3, or a rolling 90/120-day window? The spec says
    calendar months, so I'll assume calendar-month boundaries unless corrected.

---

## 11. Risks

- **Scope/complexity**: this is materially larger than any prior phase (new auth subsystem,
  two frontends, RBAC, schema changes) — recommend strict adherence to your phase-gate process
  to keep review tractable.
- **Shared-logic extraction risk**: moving `booking.py`'s slot-generation logic carries the
  same category of risk as the P1 `overlaps()`/`get_doctor_timezone()` extraction — mitigated
  by doing it as a pure move with the full existing regression suite as a safety net, exactly
  as done before.
- **New security surface**: authentication, session management, and OTP are the highest-risk
  category of new code in this expansion (credential handling, session fixation, rate
  limiting, timing attacks on OTP comparison). Needs deliberate, careful implementation and
  dedicated security-focused tests, not just happy-path coverage.
- **PHI/HIPAA-adjacent handling**: patient names, mobile numbers, and appointment data are
  ePHI; the auth layer must not leak them via error messages, logs, or timing side-channels.
- **Pre-existing gap surfaces now**: the current `DELETE /api/appointments/{id}` authorization
  gap (item #3 in the gap analysis) becomes more exploitable once a public web UI exists
  pointing at the same backend, even before any new code ships — recommend prioritizing its
  fix early regardless of which implementation-plan phase it lands in.
- **Concurrency guarantee dilution**: any new booking-creation code path that doesn't route
  through the shared advisory-lock + exclusion-constraint logic silently reintroduces the
  double-booking bug that was already found and fixed once. This is the single most important
  invariant to protect through the refactor.

---

## 12. Testing strategy

- Reuse the existing pytest infra unchanged (`tests/conftest.py`, session-scoped client,
  per-test truncation, real local Postgres) — no new test infra needed, only new test files.
- **Auth**: OTP request/verify (including expiry, wrong-code, rate-limit), staff login
  (including wrong-password, inactive-account), session expiry/invalidation, RBAC dependency
  (403 for wrong role on every gated route).
- **Shared availability engine extraction**: before/after parity tests — same inputs must
  produce identical outputs from both the old inline logic and the new shared function, run as
  part of the extraction PR itself, then the full existing WhatsApp regression suite must pass
  unchanged (byte-identical behavior requirement from your global rules).
- **Booking-window rule**: boundary tests at month+3's last day / month+4's first day, one
  each side.
- **Reschedule endpoint concurrency**: same rigor as Test C/D from the prior remediation —
  concurrent reschedule-vs-reschedule, reschedule-vs-fresh-booking, verified against real
  Postgres with `threading.Barrier`.
- **Ownership/authorization**: patient A cannot cancel/reschedule patient B's appointment via
  the web API.
- **End-to-end**: browser-driven walkthrough of both patient and admin golden paths plus
  documented edge cases (per your standing UI-testing rule), once frontend exists.

---

## STOP

This completes the WEB P0 discovery deliverable. Per your phase-gate rule, no implementation
work begins until you've reviewed this and explicitly approved moving to WEB P1 — including
resolving (or explicitly deferring) the open items in §10, since several of them materially
change the implementation plan's shape (frontend stack and session transport especially).
