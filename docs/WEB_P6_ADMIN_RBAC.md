# WEB P6 — Admin CRUD RBAC — Report

Phase scope: gate the existing doctor/department/schedule/blocks/
appointment-type CRUD routers with WEB P5's `require_role`/
`get_current_staff` RBAC dependencies, per
`docs/WEB_EXPANSION_ARCHITECTURE.md`'s "Admin web API" step and section
8's RBAC design — the piece P5 deliberately left `require_role` unused
for outside its own account-management endpoints.

## PLAN

Mapped every existing router's endpoints against the RBAC table
(`docs/WEB_EXPANSION_ARCHITECTURE.md` §8) before touching anything, and
against what the already-shipped, merged patient web frontend (WEB P3)
actually depends on:

- `frontend/src/api.ts`'s `listDepartments`/`listDoctorsInDepartment`/
  `listAppointmentTypesForDoctor` call `GET /api/departments`,
  `GET /api/departments/{id}/doctors`, and
  `GET /api/doctors/{id}/appointment-types` **without** a Bearer token —
  genuinely public, unauthenticated reads, by WEB P3's own design.
  These three **must** stay exactly as they are; gating them would break
  the live, merged patient booking flow. Confirmed via `grep`, not
  assumption.
- WhatsApp's `booking.py` never makes HTTP calls to any of these
  routers — it's all direct DB/service-layer access — so nothing there
  depends on any of this router surface staying public or gated either
  way.

Given that, the plan: gate every **write** operation (POST/PUT/DELETE)
across `departments.py`, `doctors.py`, `doctor_schedule.py`,
`doctor_blocks.py`, `appointment_types.py`, and
`doctor_appointment_types.py`; leave every existing **read** (GET)
exactly as-is, public and unauthenticated, on all of them — not just the
three the frontend depends on. Nothing in the gap analysis or RBAC table
calls for locking down reads generally, and doing so beyond what's
actually needed would be inventing a restriction nobody asked for.

**Role split**, per the RBAC table:
- ADMIN only: departments, doctors (including department assignment),
  doctor recurring schedule, appointment types, and per-doctor
  appointment-type/duration assignment — "Manage doctors, departments,
  appointment types" and the already-resolved WEB P0 open item #3
  ("STAFF role and recurring-schedule management: ADMIN only").
- ADMIN or STAFF (any authenticated staff): doctor blocks (one-off
  unavailability) — "likely day-to-day front-desk task" per the RBAC
  table.

**Two scope decisions flagged to the user before implementing, both
resolved before writing any code:**

1. **`GET`/`POST /api/patients` returns/creates PHI (patient name +
   WhatsApp number) with zero authentication today** — a real,
   pre-existing gap found while mapping this phase, comparable to the
   appointment-cancellation-ownership gap fixed earlier in this project.
   Not called out by name in the RBAC table, but squarely PHI exposure.
   **Decision: fix it now**, gating both with `get_current_staff` (any
   authenticated staff — the RBAC table doesn't restrict "who can see a
   patient record" to ADMIN specifically, and front-desk STAFF
   legitimately need this).
2. **`app/api/appointments.py` (GET/POST/DELETE)** is the original,
   pre-Web-expansion REST path — still fully unauthenticated, and relied
   on directly, unauthenticated, by dozens of existing concurrency/
   exclusion-constraint tests (`test_concurrency.py`,
   `test_exclusion_constraint.py`, `test_appointment_services.py`, and
   others). The architecture doc's plan suggests this eventually becomes
   the staff/admin appointment-management surface (RBAC table: staff can
   create/cancel/reschedule on a patient's behalf). **Decision: defer to
   its own phase** — gating it now would mean updating ~30+ existing
   tests as a side effect of this phase rather than as its own
   deliberate, reviewable change. **Left exactly as it was: no auth
   change to `app/api/appointments.py` in this phase.**

## IMPLEMENT

Every gated write endpoint gained one new dependency parameter —
`Depends(require_role("ADMIN"))` or `Depends(get_current_staff)`,
imported from `app.api.staff_auth` (WEB P5) — no other logic touched:

| Router | Gated | Role |
|---|---|---|
| `departments.py` | `POST` | ADMIN |
| `doctors.py` | `POST`, `POST`/`DELETE .../departments/{id}` | ADMIN |
| `doctor_schedule.py` | `POST`, `PUT`, `DELETE` | ADMIN |
| `doctor_blocks.py` | `POST`, `PUT`, `DELETE` | ADMIN or STAFF |
| `appointment_types.py` | `POST` | ADMIN |
| `doctor_appointment_types.py` | `POST`, `PUT`, `DELETE` | ADMIN |
| `patients.py` | `GET`, `POST` | ADMIN or STAFF |

`department_doctors.py` has only a `GET` (no writes) — nothing to gate,
left untouched.

**A larger-than-expected ripple effect, found and fixed before this was
considered done:** `tests/helpers.py`'s `seed_basic_doctor` — used by
nearly every existing test that needs a doctor/department/appointment
type — calls exactly the endpoints this phase just gated, unauthenticated.
Rather than touching every one of its ~15+ call sites across the test
suite, `seed_basic_doctor` now authenticates as a freshly-created ADMIN
**internally** (via a new `create_admin_and_get_headers` /
`create_staff_and_get_headers` helper), so every existing caller gets
this for free with no signature change. Six additional call sites that
call `POST /api/patients` directly (now gated) outside that helper —
across `test_concurrency.py` (4), `test_patient_auth.py` (1), and
`test_patient_booking_api.py` (1) — needed the same explicit fix, plus
`test_appointment_types.py`'s three tests (its own direct
`POST /api/appointment-types` calls). All confirmed via `grep` across
the whole test suite before starting, not discovered piecemeal by
chasing failures.

## TEST

`tests/test_admin_rbac.py`, 9 new tests: every ADMIN-only write rejects
an unauthenticated caller (401) and a STAFF-role caller (403), and
succeeds for ADMIN; the two "assign X to Y" endpoints get their own
dedicated tests (need a fresh, not-yet-assigned pair to meaningfully
exercise the success path, rather than reusing already-assigned seed
data); `doctor_blocks`' three endpoints accept *either* role and reject
none; `patients.py`'s two endpoints reject unauthenticated callers and
accept either role; and a regression guard explicitly re-confirms the
three patient-frontend-dependent GETs are still public — the crux of
this phase's scoping decision, checked directly rather than only implied
by "nothing else changed."

Existing suite: 6 files updated for the reasons above (`tests/helpers.py`,
`tests/test_appointment_types.py`, `tests/test_concurrency.py`,
`tests/test_patient_auth.py`, `tests/test_patient_booking_api.py`) — no
test's actual assertions changed, only how they authenticate before
calling a now-gated endpoint.

**Live smoke test**, not just pytest, against the real dev server and
dev database: bootstrapped an ADMIN via `scripts/create_staff_account.py`
→ confirmed `POST /api/departments` returns 401 unauthenticated → logged
in and confirmed the same call succeeds as ADMIN → created a doctor and
assigned it to the department → confirmed `GET /api/departments` and
`GET /api/departments/{id}/doctors` are still fully public and correctly
reflect the new data. Every step behaved exactly as designed. Smoke-test
data cleaned up from the dev database afterward.

## VERIFY

- Full backend suite: **123 passed, 0 failed** (114 pre-existing + 9
  new), real Postgres, run twice consecutively (the lesson from WEB
  P5's cross-test-run truncation bug) — clean both times.
- Live smoke test against the dev server + dev database, described
  above.

## REPORT

**Behavior change for WhatsApp:** none — `booking.py` never calls these
routers over HTTP.

**Behavior change for the patient web frontend (WEB P3/P4):** none —
the three GETs it depends on were explicitly identified and left
untouched, then re-confirmed via both an automated regression test and
a live check.

**Behavior change for any existing REST caller of the now-gated
endpoints:** yes, by design — `POST /api/departments`, `POST /api/doctors`,
doctor schedule/blocks/appointment-type writes, and both
`GET`/`POST /api/patients` now require an authenticated staff session
(and, for most, specifically ADMIN). Anyone integrating directly against
this REST surface before this phase will need to authenticate now. No
such external caller is known to exist outside this project's own test
suite, which has been updated accordingly.

**New, fully tested and live-verified:** RBAC enforcement across the
doctor/department/schedule/blocks/appointment-type admin surface, and a
closed PHI-exposure gap on `patients.py`.

**Not in this phase, on purpose:**
- `app/api/appointments.py`'s own REST auth — explicitly deferred to its
  own phase (see PLAN above); still fully unauthenticated.
- Locking down any existing `GET` beyond the two PHI-sensitive
  `patients.py` endpoints — every other read stays public, deliberately,
  since nothing in the gap analysis calls for it.
- `doctor_schedule`'s date-range fields (`start_date`/`end_date`) — WEB
  P0 open item #7, explicitly deferred to WEB P7.
- Any admin-facing frontend UI — this phase is REST-only, matching the
  same pattern WEB P5 used for staff auth before any UI consumed it.

## STOP

Per the phase-gate rule: stopping here. Waiting for approval before the
next phase begins.
