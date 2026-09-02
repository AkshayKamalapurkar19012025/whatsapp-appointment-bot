# WEB P1 — Backend Foundation / API Contracts — Report

Phase scope (per the original spec): API structure, shared booking services,
reusable availability services, reusable appointment services, API
request/response models, backend validation, calendar-window validation,
shared Web/WhatsApp interfaces. No frontend, no Admin UI, no WhatsApp UX
redesign.

## PLAN

1. Extract `app/api/booking.py`'s slot-computation logic (`get_available_slots`,
   `get_appointment_type_for_doctor`) into `app/services/availability_engine.py`
   as a pure move, so both `booking.py` and `app/api/availability.py` call one
   implementation.
2. Add new, additive-only calendar-window logic to that same module:
   `booking_window()` / `is_within_booking_window()` (current month + next 3
   calendar months, per the product spec) and `list_available_dates_in_range()`
   for a future web calendar view.
3. Extract `app/api/appointments.py`'s create/cancel logic into
   `app/services/appointment_services.py`, raising typed, transport-agnostic
   exceptions (`app/services/exceptions.py`) that `appointments.py` translates
   back into the exact same `HTTPException`s as before.
4. Add two opt-in parameters on top of the moved logic, both defaulting to
   today's exact behavior: `enforce_booking_window` (create) and
   `requesting_patient_id` (cancel) — see "Course correction" below for why
   the latter is infrastructure, not a security fix, in this phase.
5. Rewire `booking.py` and `availability.py` onto the shared engine.
6. Full existing regression suite must pass unchanged; new tests cover the
   new, additive behavior.

## IMPLEMENT

**Files created:**
- `app/services/__init__.py`
- `app/services/availability_engine.py` — `get_appointment_type_for_doctor`,
  `get_available_slots` (both pure moves from `booking.py`, byte-identical
  logic), `booking_window`, `is_within_booking_window`,
  `list_available_dates_in_range` (new).
- `app/services/exceptions.py` — 10 typed exceptions
  (`DoctorNotFound`, `PatientNotFound`, `AppointmentTypeNotAssigned`,
  `OutsideDoctorSchedule`, `DoctorBlockConflict`, `SlotOverlap`,
  `OutsideBookingWindow`, `AppointmentNotFound`, `AlreadyCancelled`,
  `NotAppointmentOwner`).
- `app/services/appointment_services.py` — `create_appointment_service`,
  `cancel_appointment_service` (pure moves of `appointments.py`'s handler
  bodies, including the pg_advisory_xact_lock/EXCLUDE-constraint handling,
  unchanged, plus the two new opt-in parameters).
- `docs/WEB_P1_BACKEND_FOUNDATION.md` — this report.

**Files modified:**
- `app/api/appointments.py` — now a thin wrapper: builds the
  connection/cursor, calls the service, translates typed exceptions to the
  same `HTTPException` status codes/messages as before.
- `app/api/availability.py` — keeps its own doctor/appointment-type 404
  checks and response shape; slot computation delegates to the shared
  engine.
- `app/api/booking.py` — removed its two local function definitions,
  imports them from the shared engine instead. Also dropped two now-unused
  imports (`time`, `make_aware_datetime`) left dangling by the move.

**No migrations.** No database schema changes in this phase.

## Course correction on the DELETE ownership decision

When we walked through phase feasibility, the decision was "fix the
`DELETE /api/appointments/{id}` ownership gap now, in P1." Implementing it
surfaced a real constraint I should have flagged before, not after: genuine
ownership enforcement requires knowing *who is asking* — and no identity
system exists until WEB P2 (patient auth). Nothing today authenticates a
caller of that endpoint.

I considered requiring a `patient_id` parameter on the endpoint itself and
checking it server-side. I did not ship that — it would be security theater,
not a fix: patient ids are small sequential integers, not secrets, so
anyone could pass the correct one for an appointment they don't own. Shipping
that as "the fix" would create false confidence, which is worse than the
status quo.

What this phase does ship: `cancel_appointment_service(cur, appointment_id,
requesting_patient_id=None)`. Passing `None` (what the existing REST
endpoint does today) skips the check entirely — today's behavior is
byte-for-byte unchanged, confirmed by `test_cancel_appointment_service_default_stays_unrestricted`
and by the unchanged existing suite. Passing a real patient id enforces
ownership (`NotAppointmentOwner`, checked before the already-cancelled
check, so a non-owner doesn't learn an appointment's cancellation state).
This is proven with real tests
(`test_cancel_appointment_service_rejects_non_owner` /
`_allows_owner`) even though no HTTP endpoint exercises it yet.

**Net effect:** the gap is not closed in P1, but the path to closing it is
built and tested. The first thing WEB P4's authenticated web cancel
endpoint should do is pass its authenticated patient's id through this
exact parameter — that's a one-line wire-up at that point, not a fourth
reimplementation of the cancel rules.

## Calendar-window enforcement: same shape of decision

`create_appointment_service(..., enforce_booking_window=False)` follows the
identical pattern, for a less severe reason: no web booking endpoint exists
yet to enforce the window on (that's WEB P3/P4), and the existing REST
endpoint/tests book arbitrary future dates that must keep working unchanged.
`is_within_booking_window()` is implemented and tested now (calendar-month
boundary, year-rollover, past-date rejection); wiring
`enforce_booking_window=True` into the future web endpoint is a one-line
change when that endpoint is built.

## A finding for WEB P7, not fixed here

Writing the overnight-schedule regression test surfaced that
`app/api/doctor_schedule.py`'s own `POST` endpoint rejects `end_time <=
start_time` — so an overnight doctor schedule can't actually be created
through today's admin API at all, only by direct SQL (which the test does).
The availability engine has always correctly handled overnight schedules
(that logic came from `booking.py`, unchanged); it's the schedule *creation*
API that's inconsistent with what the engine and database support. Left
as-is in this phase (changing that validator is out of P1's scope and not
blocking anything) but worth deciding in WEB P7 (doctor availability
management): either lift the validator's restriction, or confirm overnight
schedules are intentionally out of scope for the recurring-availability
feature.

## TEST

New tests, 20 total:

- `tests/test_availability_engine.py` (8): `booking_window`/
  `is_within_booking_window` boundaries (calendar-month math, year rollover,
  today allowed, window-end allowed, day-after-window rejected, past-date
  rejected), the overnight-schedule REST regression, and
  `list_available_dates_in_range` sanity.
- `tests/test_appointment_services.py` (6): `enforce_booking_window`
  reject/allow/default-off, `requesting_patient_id` reject/allow/default-off.

## VERIFY

- Full existing suite (42 tests, unchanged) + new tests (14) = **56 passed,
  0 failed**, run against real local Postgres 16 (not mocks/sqlite).
- Live smoke test against a running server (not just pytest):
  - `POST /api/availability` via the shared engine — correct slots returned.
  - Full WhatsApp conversational booking flow, Hi → registration → department
    → doctor → type → date → slot → confirm — booked successfully.
  - `POST /api/appointments` (REST, now via `create_appointment_service`) —
    booked; a duplicate-slot request correctly 409s.
  - `DELETE /api/appointments/{id}` (REST, now via `cancel_appointment_service`)
    — cancels; a second cancel 409s "already cancelled"; a nonexistent id
    404s. All three status codes/messages identical to pre-refactor.
- `python -c "import app.main"` and per-file `ast.parse` checks confirm no
  import/syntax breakage from the module moves.

## REPORT

**Behavior change for WhatsApp:** none. `booking.py`'s slot logic is an
unmodified move; `get_available_dates` (WhatsApp's own date-picking
function) is untouched.

**Behavior change for the existing REST API:** one, deliberate:
`POST /api/availability` now handles overnight schedules (it didn't before;
`booking.py`'s version always did). No test depended on the old, incomplete
behavior. Everything else — status codes, response shapes, error messages —
is byte-identical, verified by the unchanged existing test suite plus live
smoke testing.

**New, currently-inert capability:** `enforce_booking_window` and
`requesting_patient_id` exist, are tested, and are not yet wired into any
reachable endpoint (both default off). They become one-line integrations in
WEB P3/P4.

**Ownership gap:** still open, honestly reported as such — see "Course
correction" above. Recommend WEB P4 close it as its first task, once WEB P2
provides a real patient id to pass through.

**New finding:** overnight schedule creation is blocked at the admin API
layer today, inconsistent with what the engine/database support — flagged
for a WEB P7 decision, not fixed here.

## STOP

Per the phase-gate rule: stopping here. Waiting for approval before WEB P2
(Patient Authentication) begins.
