# WEB P5 — Staff/Admin Authentication + RBAC — Report

Phase scope: a username+password login subsystem for staff and admin
accounts, an RBAC dependency enforced server-side, and the account
self-management surface needed to actually create/list/deactivate staff
accounts (there being no self-service staff signup, unlike patient OTP).
Per `docs/WEB_EXPANSION_ARCHITECTURE.md` gap #2 and the P2-then-P3
precedent, this phase builds the auth subsystem itself; wiring RBAC into
the existing doctor/department/etc. CRUD routers is deliberately deferred
to a later "Admin web API" phase.

## PLAN

Mirrors `app/services/patient_auth.py` / `app/api/patient_auth.py`'s
shape (WEB P2) closely, but as a genuinely separate subsystem — staff log
in with a password, not an OTP, and a staff session must never
authenticate a patient-facing endpoint or vice versa. That ruled out the
"one shared `sessions` table with a `subject_type` discriminator" option
`docs/WEB_EXPANSION_ARCHITECTURE.md` section 5 left open; a dedicated
`staff_sessions` table (structurally identical to `patient_sessions`, but
a separate table) is safer by construction — no code path can ever
confuse the two token spaces.

Two open items from the P0 architecture doc were resolved to start this
phase:
- **Password hashing dependency** (item #6): **argon2-cffi**, using
  Argon2id (the current OWASP-recommended default for new applications).
  `passlib` was considered and rejected — it's in maintenance mode,
  whereas argon2-cffi is actively maintained.
- **Session transport** (item #5): Bearer token header, same as patient
  sessions (WEB P2/P3 already committed to this for a separate-origin
  frontend dev server).

**RBAC design** (`docs/WEB_EXPANSION_ARCHITECTURE.md` section 8): two
roles, ADMIN and STAFF, enforced by a `require_role(role)` FastAPI
dependency factory checked server-side on every gated route — never a
frontend-only check. `get_current_staff` resolves the session first (401
for no/invalid session); `require_role` layers a role check on top (403
for the wrong role). Deactivating an account is live-checked in
`get_staff_by_session_token`, so a disabled account is locked out
immediately, not just at its next login attempt — closing what would
otherwise be a gap between "admin deactivates a rogue account" and that
account's current session actually stopping.

**Anti-enumeration decision, made explicit rather than left implicit:**
an unknown username and a wrong password for a known username return the
exact same `InvalidCredentials` → 401 "Invalid username or password", so
a failed login never discloses whether a given admin/staff username
exists. This is a stricter posture than patient OTP (which doesn't hide
whether a phone number is registered), appropriate here because "does
this admin account exist" is more sensitive than "does this phone number
have an account." A deactivated account, by contrast, is intentionally
*distinguishable* (see IMPLEMENT) — once someone has already proven they
know the correct password, telling them the account is disabled doesn't
help them guess anything, and is more useful to a legitimate deactivated
staff member than a generic wrong-password message.

## IMPLEMENT

**`migrations/0005_staff_authentication.sql`** — two new tables,
additive only:
- `staff`: `username` (unique, case-insensitively normalized at the
  application layer before insert/lookup), `password_hash`, `role`
  (`CHECK (role IN ('ADMIN','STAFF'))`), `active`, plus
  `failed_login_count`/`locked_until` for the lockout mechanism below.
- `staff_sessions`: same shape as `patient_sessions` (hashed opaque
  token, expiry, revocation) — a separate table, not shared.

**`app/services/staff_auth.py`**:
- `login(cur, username, password)` — the core flow: look up the
  (case-insensitively normalized) username with `FOR UPDATE`; if locked
  and the lock hasn't expired, raise `StaffAccountLocked` before
  touching the password at all; verify the argon2 hash; on mismatch,
  bump `failed_login_count` and set `locked_until` once the count hits
  `MAX_FAILED_LOGIN_ATTEMPTS` (5), the same brute-force defense shape as
  patient OTP's `attempt_count`/`OtpLocked`, adapted from a per-code cap
  to a temporary account lockout (15 minutes); on success, reset the
  counter, issue a hashed session token (`secrets.token_urlsafe(32)`,
  SHA-256 stored, 24h TTL — identical mechanics to patient sessions).
  **Timing-safety detail**: for an unknown username, the function still
  calls `_hasher.hash(password)` (discarding the result) before raising
  `InvalidCredentials`, so "unknown username" and "known username, wrong
  password" take roughly the same wall-clock time — otherwise the
  difference itself would let an attacker enumerate valid usernames.
- `get_staff_by_session_token` / `revoke_session` — same shape as their
  patient equivalents; the former's live `active` check is what makes
  deactivation immediate (see PLAN).

**`app/services/staff_management.py`** — account self-management, with
no role-awareness of its own (authorization is the API layer's job, the
same pattern as every other `app/services/*` module):
- `create_staff_account` — atomic `INSERT ... ON CONFLICT (username) DO
  NOTHING RETURNING ...`, raising `UsernameAlreadyExists` if the row
  wasn't inserted (avoids a separate check-then-insert race).
- `list_staff_accounts` — never returns `password_hash`.
- `set_staff_active` — also revokes every currently-live session for the
  account when deactivating it (belt-and-suspenders alongside the live
  `active` check above).

**`app/api/staff_auth.py`** — `POST /api/auth/staff/login`, `POST
/logout`, `GET /me` (any authenticated staff); `GET /accounts`, `POST
/accounts`, `PATCH /accounts/{id}/active` (all three `Depends(require_role
("ADMIN"))`). The `InvalidCredentials` handler explicitly commits before
raising the 401 (same reasoning, and same bug class, as
`app/api/patient_auth.py`'s `OtpInvalid` handling: the failed-attempt
counter bump must survive even though the request itself is reported as
an error, or `StaffAccountLocked` could never be reached — an
`HTTPException` raised inside `with get_connection()` would otherwise
roll it back).

**`scripts/create_staff_account.py`** — a one-time bootstrap CLI (the
same category of tool as `scripts/provision_local_db.sh`), since nothing
can call the ADMIN-only creation endpoint before an ADMIN exists.
Prompts for a password via `getpass` rather than accepting it as an
argv value, so it never lands in shell history or a process listing.
Calls `create_staff_account` directly — the same function the API
endpoint calls, so a bootstrapped account isn't created by different
logic than one an admin creates later.

## A real bug caught while smoke-testing, before calling this done

`scripts/create_staff_account.py`'s first version failed immediately —
`ModuleNotFoundError: No module named 'app'` — because it imports
`app.config` but nothing put the repo root on `sys.path` when run
directly as `python scripts/create_staff_account.py` (that only works
automatically for a module executed as part of the `app` package
itself). `scripts/migrate.py` already solves this
(`sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`) —
fixed by adding the identical line, then re-verified the script actually
runs and produces a working account (see VERIFY).

## TEST

`tests/test_staff_auth.py`, 15 tests: valid login and session issuance;
case-insensitive username matching; unknown username and wrong password
both rejected with the identical 401 message (the anti-enumeration
guarantee, checked directly, not just asserted in a comment); a
deactivated account rejected with a distinct 403; account lockout after
`MAX_FAILED_LOGIN_ATTEMPTS` failures, and that the lockout expires and
allows a fresh attempt; unauthenticated `/me` (both no header and a
bogus token); `/me` and `/logout` with a valid session, and that the
token is dead afterward; deactivating an account instantly invalidates
its *existing* session (not just future logins); an unauthenticated
request to an ADMIN-only endpoint is 401 (not 403) — proving
`require_role` delegates authentication to `get_current_staff` first,
rather than treating "no session" as just another wrong role; a STAFF
session is rejected (403) from the ADMIN-only accounts endpoints; an
ADMIN can list/create/deactivate accounts end-to-end, including
confirming `password_hash` is never present in the list response and
that a freshly-created account can immediately log in with the password
just set; duplicate-username creation rejected (409); deactivating a
nonexistent id rejected (404).

**A real bug caught by the first full-suite run, before any of this was
considered done:** the new test file passed cleanly in isolation but
failed on a second full-suite run with `UsernameAlreadyExists` — the new
`staff`/`staff_sessions` tables weren't in `tests/conftest.py`'s
`APP_TABLES` truncation list yet, so accounts from an earlier test run
persisted into the next one instead of being cleaned up between tests.
Fixed by adding both tables to that list; re-ran the full suite twice in
a row to confirm it's now clean, not just accidentally-clean-once.

**Live end-to-end smoke test**, not just pytest, run against the actual
dev server and dev database (separately from the isolated test
database): bootstrap an ADMIN via the CLI script → login → `/me` → wrong
password rejected (401) → list accounts → create a STAFF account →
STAFF logs in and is correctly rejected (403) from the ADMIN-only
accounts endpoint → admin logs out → the old token is dead (401)
afterward. Every step behaved exactly as designed on the first attempt
after the two bugs above were already fixed. Smoke-test data was cleaned
up from the dev database afterward.

## VERIFY

- Full backend suite: **88 passed, 0 failed** (73 pre-existing + 15
  new), real Postgres, run twice consecutively to rule out the
  cross-test-run truncation bug recurring.
- Migration applied cleanly to both the test database and the local dev
  database (`scripts/migrate.py`, idempotent — confirmed via a second
  run reporting nothing pending).
- Live smoke test against the dev server and dev database, described
  above.

## REPORT

**Behavior change for WhatsApp or any existing REST endpoint:** none —
this phase adds new tables and a new, entirely separate router; nothing
existing was touched.

**New, fully tested and live-verified:** staff/admin username+password
login, session management, brute-force lockout, and a real, working RBAC
dependency (`require_role`) demonstrated end-to-end through the account
self-management endpoints it gates — not merely written and left
untested.

**Not in this phase, on purpose:**
- Gating the existing `doctors`/`departments`/`doctor_schedule`/etc. CRUD
  routers with `require_role` — a separate, later "Admin web API" phase
  (`docs/WEB_EXPANSION_ARCHITECTURE.md` section 9, step 6), matching the
  same precedent WEB P2→P3 already established for patient auth.
  `require_role` itself is built, tested, and proven correct here so
  that phase is pure wiring, not new logic.
- Any staff/admin frontend UI — no frontend work was in this phase's
  scope; the login/account-management surface is REST-only, tested via
  `httpx`/pytest and live curl, the same rigor WEB P2 used before WEB P3
  built the patient UI.
- Password reset / "forgot password" flow — not requested by any phase
  spec; an ADMIN can already deactivate a compromised account.
- Editing a staff account's role or username after creation, or
  resetting its password as an admin action — kept out to match this
  phase's minimum-viable RBAC-management scope (create, list, toggle
  active); worth a look if a later phase's admin UI needs it.

## STOP

Per the phase-gate rule: stopping here. Waiting for approval before the
next phase begins.
