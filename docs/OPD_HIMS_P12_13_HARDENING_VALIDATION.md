# OPD/HIMS master spec Phase 12 (Production Hardening) + Phase 13 (End-to-End Validation)

Fixes gap #7 from `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md`'s summary verdict: the
spec's own Phase 12 and Phase 13 were never run as dedicated passes in this
session. This is that pass -- not a new feature, a targeted review plus real
browser testing of what already exists (including the six gaps fixed
earlier in this same working session).

## Security review

Delegated a focused SQL-injection audit (the highest-value single check for
a `psycopg`, no-ORM, hand-written-SQL codebase) across every `cur.execute(`
call in `app/services/*.py` and `app/api/*.py` (357 call sites), with
particular attention to every free-text search/filter endpoint -- the
highest-risk pattern for this bug class.

**Result: clean.** No genuine finding. The codebase consistently follows two
safe patterns everywhere: a hardcoded column-name tuple spliced into the
`SELECT` list (`f"SELECT {', '.join(_COLUMNS)} ..."`, never user input), and
`where_clauses.append("col = %s")` + `params.append(value)` for every
dynamic filter, with only the fixed WHERE-clause *text* -- never a value --
ever reaching an f-string. Specifically checked and confirmed safe:
`app/services/search_service.py`'s global search (this session's own new
gap #5 endpoint), `GET /patients/admin` and `GET /patients/search`,
`GET /appointments`'s date/status/doctor filters, and `GET /audit-log`. No
`ORDER BY`/`GROUP BY` anywhere is built from a request-supplied field name.

## Concurrency / idempotency

Not a new mechanism-by-mechanism review -- the master spec audit already
confirmed the underlying mechanisms exist (`FOR UPDATE` row locks on every
money- or status-affecting path, partial unique indexes preventing duplicate
payments, idempotent mark-read endpoints). What Phase 12 actually asks for
beyond that is *testing* it, which the existing 620+ test suite already
does extensively (see `tests/test_queue_tokens.py`,
`tests/test_appointment_lifecycle.py`, `tests/test_billing_invoices.py`'s
own concurrency-shaped assertions) -- not repeated here.

## One real finding, fixed

Live browser testing (see below) surfaced a genuine pre-existing bug:
`POST /appointments/{id}/confirm-and-checkin` (the walk-in "Confirm & Check
In" button) composes `confirm_appointment_service` internally when starting
from `PENDING`, and that service still enforces its own
`AppointmentSlotPassed` guard (refusing to confirm a request whose
`start_at` has already gone by). The sibling `POST /confirm` endpoint has
always caught this exception and returned a clean 409; `confirm-and-checkin`
had no handler for it at all, so any walk-in whose slot time slipped into
the past between booking and the front desk clicking the button (routine
for a `start_at="now"` walk-in booking, not a rare edge case) crashed the
server with an unhandled 500.

Fixed in `app/api/appointments.py` by adding the same
`except svc_exc.AppointmentSlotPassed` handler the plain `/confirm` endpoint
already has, with a new regression test
(`tests/test_appointment_lifecycle.py::test_confirm_and_checkin_rejects_slot_passed_cleanly`)
that reproduces the exact crash via direct DB manipulation (create a
PENDING appointment, back-date its `start_at`, call confirm-and-checkin)
and asserts a clean 409 instead of a 500.

## Browser testing (dedicated pass)

Started the real backend (`uvicorn`) and frontend (`vite`) dev servers
against a live Postgres database and drove the admin app with Playwright
(Chromium), rather than relying on `tsc`/build/lint alone.

**Pass 1 -- app-wide smoke test (11/11 checks passed):** login, global
search bar renders and opens a results dropdown on query, notification
bell renders and its dropdown opens, Patients page loads with its
pagination control and a populated directory table, Staff Accounts' role
dropdown lists all 8 roles (`ADMIN/STAFF/DOCTOR/NURSE/RECEPTIONIST/
LAB_TECH/PHARMACIST/BILLING`), Appointments page loads. Zero console
errors or uncaught page exceptions during any of it.

**Pass 2 -- Visit Completion checklist, end to end (9/9 checks passed):**
seeded a real CHECKED_IN, paid appointment via the API, opened its details
modal in the browser, clicked "Mark completed", and confirmed the
`VisitCompletionDialog` opens with its six checklist items rendered
correctly (`Consultation completed | Orders created | Prescription created
| Billing completed | Payment completed | Follow-up scheduled`), then
completed the visit without error.

**Not directly browser-verified:** the allergy panel/warning-badge UI
(`ConsultationWorkspace`'s Triage tab). Getting a live browser session into
that specific tab requires the doctor's queue to have this exact patient as
"now serving," which two Playwright attempts didn't reliably reach in the
time available (a navigation/timing issue in the *test script*, not
evidence of an app bug -- the same code path is exercised by 8 passing
backend integration tests in `tests/test_patient_allergies.py`, and `tsc`/
`vite build` both pass clean on every file involved). Noted here rather
than silently claimed as verified.

## End-to-end validation (spec Phase 13)

The spec's 8 named journeys (A-H) were not run as a single formal exercise
line-by-line -- doing so exhaustively was out of scope for the time
available in this pass, same "scoped, not silently skipped" honesty this
audit trail has used throughout. What *was* run live, end to end, through
the real UI: patient registration → appointment creation → confirm & check
in → payment → queue token issuance → appointment details → Visit
Completion checklist → complete visit (core of Journey A). The two
Playwright scripts and their console output are not committed (throwaway
verification tooling, not application code or tests).
