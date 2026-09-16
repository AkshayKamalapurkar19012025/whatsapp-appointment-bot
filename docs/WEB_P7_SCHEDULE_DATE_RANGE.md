# WEB P7 — Doctor Schedule Date Range — Report

Phase scope: add `start_date`/`end_date` to `doctor_schedule`, closing
WEB P0 gap analysis item #4 ("Recurring doctor schedule with date
range") and resolving open item #7 ("doctor_schedule date-range
semantics"), per `docs/WEB_EXPANSION_ARCHITECTURE.md` sections 5 and 10.

## PLAN

The schema change itself (`docs/WEB_EXPANSION_ARCHITECTURE.md` §5) was
already specified: nullable `start_date`/`end_date`, NULL meaning
open-ended on that side, purely additive. What P0 explicitly left open
was the *semantics*: "does a new date-ranged schedule row coexist with
old open-ended rows for the same doctor/day (e.g. an override), or does
creating one require deactivating the open-ended row first?"

**Resolution:** neither special case — extend the *existing*
`schedule_overlaps()` overlap check (already used to reject two rows
with conflicting day/time for the same doctor, added before this
project's review) to also require the two rows' date ranges to
intersect. Two rows now conflict only when **both** their time ranges
**and** their date ranges overlap. This is a direct, minimal extension
of already-tested logic, not a new "override precedence" concept:
- A NULL start_date/end_date is unbounded on that side (matches every
  pre-P7 row's "applies forever" meaning), so a permanent row still
  conflicts with any dated row at the same day/time, exactly as it did
  with any other permanent row before this phase.
- Two rows with the same day/time but genuinely non-overlapping date
  ranges (e.g. "Saturdays 9-12 in March" and "Saturdays 9-12 in May")
  are allowed to coexist — this is what "date-ranged schedule" is
  actually for.
- `start_date`/`end_date` are both inclusive, so two ranges touching on
  the same calendar day (row A ends the day row B starts) still
  conflict — deliberately checked as its own boundary test, not just
  implied.

This was resolved via direct reasoning from the existing, already-tested
overlap-prevention pattern rather than asked as an open question — unlike
WEB P6's two scope decisions, there wasn't a real fork with materially
different blast radius or risk between the options; extending the
existing check was the only option consistent with "don't duplicate
business rules" and required no new precedence machinery.

**Three touch points, not just the CRUD router**, all needed to learn
about date ranges consistently:
1. `app/api/doctor_schedule.py` — `schedule_overlaps()` and the
   create/update endpoints (what an admin is *allowed to configure*).
2. `app/services/availability_engine.py` — slot generation (what's
   *shown* as bookable to a calendar/patient).
3. `app/services/appointment_services.py`'s `create_appointment_service`
   — the actual booking-creation schedule check (what's *allowed to be
   booked*), so a booking can never succeed for a date the calendar
   wouldn't have offered.

Found and deliberately left alone: `reschedule_appointment_service`
never checked `doctor_schedule` at all, even before this phase — a
pre-existing characteristic inherited unchanged from the original
WhatsApp `RESCHEDULE_FINAL_CONFIRM` logic (documented in its own
docstring). Date-ranged schedules don't introduce a new gap here; a
reschedule already didn't validate against *any* schedule row, ranged
or permanent. Not fixed in this phase — out of scope, and fixing it here
would touch reschedule behavior for reasons unrelated to date ranges.

**Update (2026-09-16): closed.** `reschedule_appointment_service` now
re-checks `doctor_schedule` for the new slot (raising
`OutsideDoctorSchedule`, wired to a 409 in `appointments.py` and
`patient_scheduling.py`, and surfaced as a retry prompt in
`scheduling.py`'s WhatsApp flow), tested in
`tests/test_reschedule_service.py`.

## IMPLEMENT

**`migrations/0006_doctor_schedule_date_range.sql`** — `ALTER TABLE
doctor_schedule ADD COLUMN start_date DATE, ADD COLUMN end_date DATE`.
No backfill; every existing row has both NULL, unchanged meaning.

**`app/api/doctor_schedule.py`**:
- `DoctorScheduleCreate` gains optional `start_date`/`end_date`, with a
  field validator mirroring the existing `start_time`/`end_time` one
  (`end_date` must be on or after `start_date` when both given).
- `schedule_overlaps()` now takes the candidate row's date range and
  checks date-range intersection alongside the existing time-range
  check, using the standard NULL-as-unbounded interval-overlap
  condition. The function's two near-duplicate SQL branches
  (with/without `exclude_schedule_id`) were merged into one
  parameterized query in the same change, since both needed the new
  date-range clauses anyway.
- `GET`/`POST`/`PUT` all read/write/return `start_date`/`end_date`
  (ISO date strings, `null` when unbounded).

**`app/services/availability_engine.py`**: the slot-generation query
gained `AND (start_date IS NULL OR start_date <= %s) AND (end_date IS
NULL OR end_date >= %s)` against the requested date — a schedule row
now only contributes slots for a date it actually covers.

**`app/services/appointment_services.py`**: `create_appointment_service`'s
own doctor-schedule check gained the identical date-range clauses
against the booking's `start_at.date()`, so `OutsideDoctorSchedule` is
now also raised for a date outside a schedule row's range — closing the
gap between "the calendar wouldn't offer this" and "the booking
endpoint would still accept it."

Booking.py's WhatsApp `get_available_dates()` needed no change — it was
already delegating to the shared `get_available_slots()` (the WEB P1
consolidation), so it picked up correct date-range behavior for free.

## TEST

`tests/test_doctor_schedule_date_range.py`, 11 tests: a date-ranged row
is created and reflected correctly by `GET`; a permanent (no-range) row
still returns `null`/`null`; slot generation includes a date inside the
range and excludes one outside it; booking creation succeeds inside the
range and is rejected (409, "Appointment is outside doctor's working
schedule") outside it; two rows with genuinely non-overlapping date
ranges coexist at the same day/time; overlapping date ranges at the same
day/time are rejected (409); two explicit boundary tests — ranges
touching on the same calendar day conflict, ranges on immediately
adjacent days don't; a permanent row conflicts with any dated row at the
same day/time (the NULL-as-unbounded consequence, checked directly);
`end_date` before `start_date` is rejected (422); updating a permanent
row to add a date range works.

Full existing suite: **134 passed, 0 failed** (123 pre-existing + 11
new), run twice consecutively — no existing test needed modification,
confirming every pre-P7 row's "applies forever" behavior is genuinely
unchanged, not just asserted to be.

**Live smoke test**, not just pytest, against the real dev server and
dev database: bootstrapped an admin, seeded a department/doctor/
appointment-type, created a Saturdays-only schedule scoped to December
2027 → confirmed `POST /api/availability` returns real slots for a
Saturday inside that range → confirmed the identical query for a
Saturday just past the range's end returns zero slots. Smoke-test data
cleaned up afterward.

## VERIFY

- Full backend suite: **134 passed, 0 failed**, real Postgres, run
  twice.
- Migration applied cleanly to both the test database and the local dev
  database.
- Live smoke test against the dev server + dev database, described
  above.

## REPORT

**Behavior change for WhatsApp:** none — `booking.py` never queries
`doctor_schedule` directly; it goes through the same shared engine this
phase updated, so it automatically respects date ranges for any future
date-ranged row without any code change to `booking.py` itself.

**Behavior change for any existing schedule row:** none — every row
predating this phase has `start_date`/`end_date` both NULL, which this
phase's interval-overlap logic treats as unbounded on that side,
reproducing the exact pre-P7 "applies forever" behavior. Verified by the
full existing suite passing unmodified.

**New, fully tested and live-verified:** an admin can scope a doctor's
recurring weekly schedule to a specific date window, and that window is
honored consistently by calendar/slot display, booking creation, and
overlap validation — not just one of the three.

**Not in this phase, on purpose:**
- `reschedule_appointment_service` still performs no doctor-schedule
  check at all (a pre-existing characteristic, not a P7 regression) —
  flagged above, not fixed, since it's unrelated to date ranges
  specifically and out of this phase's scope.

  **Update (2026-09-16): closed.** `reschedule_appointment_service` now
  re-checks `doctor_schedule` for the new slot (raising
  `OutsideDoctorSchedule`, wired to a 409 in `appointments.py` and
  `patient_scheduling.py`, and surfaced as a retry prompt in
  `scheduling.py`'s WhatsApp flow), tested in
  `tests/test_reschedule_service.py`.
- Any admin-facing frontend for managing date-ranged schedules — this
  phase is REST-only, consistent with WEB P5's and P6's own scope
  (frontend work for the admin surface remains a later phase, per
  `docs/WEB_EXPANSION_ARCHITECTURE.md` section 9's "Frontend build"
  step).

## STOP

Per the phase-gate rule: stopping here. Waiting for approval before the
next phase begins.
