# WEB P4 — Patient Appointments (View / Cancel / Reschedule) — Report

Phase scope: let an authenticated web patient see their own appointments
(upcoming / history / cancelled) and cancel or reschedule an upcoming one,
sharing every rule (ownership, concurrency, timezone display, booking
window) with the existing WhatsApp flow via the shared service layer — no
Web-specific business logic.

## PLAN

Three new endpoints were needed under `app/api/patient_booking.py`:

1. `GET /web/appointments/me` — list the authenticated patient's own
   appointments, split into `upcoming` / `history` / `cancelled`.
2. `DELETE /web/appointments/{id}` — cancel an appointment the patient owns.
3. `POST /web/appointments/{id}/reschedule` — move an appointment the
   patient owns to a new slot.

For (2), `cancel_appointment_service` (from WEB P1) already accepts a
`requesting_patient_id` and raises a distinct `NotAppointmentOwner` — it
just had no authenticated web caller yet. Wiring it in closes a gap
flagged as a known limitation in the WEB P1 report. Both `AppointmentNotFound`
and `NotAppointmentOwner` map to the same 404, so a non-owner can't tell
"not yours" from "doesn't exist."

For (3), no reschedule service existed yet — `app/api/booking.py`'s
`RESCHEDULE_FINAL_CONFIRM` handler had the only reschedule logic, written
inline for the WhatsApp conversation. Building a second, hand-written
reschedule for the web endpoint would violate global rule 4 (don't
duplicate booking logic between Web and WhatsApp) and risk the two
diverging over time. Instead: extract `reschedule_appointment_service`
into `app/services/appointment_services.py` as a faithful move of the
existing logic, then refactor `booking.py`'s own handler to call it. Both
callers now share one implementation, exactly as `create_appointment_service`
and `cancel_appointment_service` already do.

Ownership for reschedule is baked into the initial lookup query
(`WHERE id = %s AND patient_id = %s`), so a wrong-owner id raises the same
`AppointmentNotFound` a genuinely-missing id would — deliberately matching
WhatsApp's own indistinguishable-404 pattern, and different from
`cancel_appointment_service`'s optional/distinct-exception pattern by
design (documented in the service's docstring, not silently inconsistent).

## A pre-existing WhatsApp bug found before writing any new listing code

Before building `list_patient_appointments_service`, I checked the
existing `get_upcoming_booked_appointments()` (used by WhatsApp's own
cancel/reschedule menus) for the same class of bug WEB P3 found in the
booking-confirmation display. It has it: the function returns `start_at`/
`end_at` straight from a Postgres read, which psycopg normalizes to the
connection's own timezone (UTC) on read-back — not the doctor's local
time. Live reproduction confirmed: a 2:00 PM IST booking displayed as
**"8:30 AM"** in WhatsApp's own `cancellation_details_message()` and
`reschedule_selection_message()`. This is a real, pre-existing production
bug, unrelated to P4 and un-caused by any of this phase's own code — I
stopped and asked how to handle it (fix now vs. defer vs. just document);
the answer was to fix it now, alongside P4.

**Fix:** `get_upcoming_booked_appointments()` now also selects the
doctor's `timezone` and converts both returned datetimes via
`convert_to_timezone()` before returning them (falling back to
`"Asia/Kolkata"` if the stored value fails validation, matching the
fallback already used elsewhere in this codebase). Verified two ways: a
before/after live reproduction script, and a new permanent regression
test (`test_cancel_and_reschedule_selection_show_doctor_local_time` in
`tests/test_booking_flow.py`) — confirmed to actually catch the bug by
temporarily reverting the fix (`git stash`) and re-running the test
(FAILED as expected), then restoring it (PASSED).

Booking and slot-selection were never affected by this bug — those
compute times fresh via `make_aware_datetime`, they never read a stored
row back.

## IMPLEMENT

**`app/services/appointment_services.py`:**
- `reschedule_appointment_service(cur, appointment_id, *, patient_id,
  new_start_at, enforce_booking_window=False)` — extracted from
  `booking.py`, same order of checks as the original: appointment +
  status → appointment type → optional booking-window check → doctor
  blocks → `pg_advisory_xact_lock(doctor_id)` → overlap check (excluding
  self) → cancel old row → insert new row (`ExclusionViolation`-guarded).
  Documents one deliberate, inherited omission: no `doctors.active`
  re-check, since `doctor_id` comes from the existing appointment in both
  the original code and this extraction — flagged as a finding per
  "don't create Web-specific business rules," not silently fixed.
- **A real bug caught before it shipped, via code review, before any
  test was run:** the original WhatsApp code calls `conn.rollback()`
  after catching `ExclusionViolation`, because its caller keeps using the
  connection afterward to build a fallback response. My first draft of
  the extraction omitted this — Postgres aborts a transaction until an
  explicit rollback, so any later query on the same cursor would raise
  `InFailedSqlTransaction`. Fixed by adding `cur.connection.rollback()`
  at the same point, with a comment explaining why (unlike
  `create_appointment_service`'s equivalent catch, whose callers
  immediately re-raise and never reuse the connection). Verified with a
  new, deterministic mechanism test
  (`test_rollback_after_exclusion_violation_restores_cursor_usability`)
  rather than a flaky concurrency race — it manually triggers a real
  `ExclusionViolation` via two conflicting raw inserts, then proves
  `rollback()` restores the cursor.
- `list_patient_appointments_service(cur, patient_id)` — one query,
  joined with doctor/appointment-type, applying the same
  `convert_to_timezone` fix as above. Classifies `CANCELLED` rows into
  `cancelled` regardless of date; everything else into `upcoming` (start
  in the future) or `history` (start in the past), compared as aware
  datetimes so the UTC-vs-local label never matters for the comparison
  itself. `upcoming` sorts soonest-first, `history`/`cancelled` sort
  most-recent-first.

**`app/api/booking.py`:** `RESCHEDULE_FINAL_CONFIRM` now calls
`reschedule_appointment_service` instead of ~400 lines of inline SQL,
translating each typed exception back to the exact original conversational
message/session state. One deliberate, minor consolidation: two
slightly-different "not reschedulable" strings that depended on exactly
when in the original flow that was detected (an initial check vs. a
redundant re-check that the initial check's row lock makes practically
unreachable) now both map to `AlreadyCancelled` and one message.

**`app/api/patient_booking.py`:**
- `GET /appointments/me` → `list_patient_appointments_service`.
- `DELETE /appointments/{id}` → `cancel_appointment_service` with
  `requesting_patient_id`; not-found and not-owner both → 404;
  already-cancelled → 409.
- `POST /appointments/{id}/reschedule` (body: `new_start_at`) →
  `reschedule_appointment_service` with `enforce_booking_window=True`;
  not-found → 404; already-cancelled / no appointment type / doctor
  block / outside window / overlap → 409 with distinct messages.

**Frontend (`frontend/src/`):**
- `MyAppointments.tsx` (new) — tabs for Upcoming/History/Cancelled,
  Cancel (with a native confirm dialog) and Reschedule (an inline
  sub-flow reusing the existing `Calendar` component) on the Upcoming
  tab only.
- `BookingFlow.tsx` — "My appointments" link in the topbar and on the
  confirmation screen.
- `App.tsx` — a simple `view` state switch between the booking flow and
  My Appointments, no router library (matching the existing pattern).
- `types.ts` / `api.ts` — new types and the three fetch functions, with
  `types.ts` explicitly noting that (unlike `BookedAppointment`'s
  create/reschedule-response fields) `MyAppointment`'s fields ARE
  correctly doctor-local, since the listing service was deliberately
  fixed to convert.
- `styles.css` — tabs, appointment cards/actions, mobile stacking.

**A bug self-caught via code review, before any browser testing ran:**
`MyAppointments.tsx`'s `load()` and `handleCancel()` initially lacked the
same 401→`onLoggedOut()` handling that `confirmReschedule()` (same file)
and WEB P3's `BookingFlow.confirmBooking()` fix already established as
the pattern for an expired/revoked session. Fixed both before running
anything.

## TEST

**Backend**, 25 new tests:
- `tests/test_reschedule_service.py` (9): moves to a new slot; rejects
  nonexistent / another patient's / already-cancelled appointment; rejects
  a doctor-block conflict, an overlapping slot, and an out-of-window date
  when requested; confirms the returned `start_at` is the same *instant*
  as requested (not the same UTC offset label — the same
  round-trip-normalization characteristic as `create_appointment_service`,
  not a bug, corrected from an initial wrong assertion caught during
  authoring); the rollback-mechanism test above.
- `tests/test_patient_appointments_api.py` (15): auth required on all
  three endpoints; listing splits correctly into upcoming/history/
  cancelled and only shows the caller's own; cancel/reschedule succeed
  for the owner and are rejected (404, no existence leak) for a
  non-owner, a nonexistent id, or an already-cancelled appointment;
  reschedule rejects an overlap and enforces the booking window; and the
  two phase-spec cross-channel checks — **a web cancellation is visible
  to WhatsApp** (book+cancel via web, then confirm via WhatsApp's own
  reschedule-menu response that no upcoming appointments remain) and
  **a WhatsApp reschedule is visible to the web** (book via web,
  reschedule through the full WhatsApp conversational menu, then confirm
  via `GET /web/appointments/me` that the old id moved to `cancelled` and
  the new id appears in `upcoming`).
- `tests/test_booking_flow.py` (+1): the UTC-display regression guard
  described above.

**Browser** (Playwright against a real Chromium, driving the actual dev
server + backend, real Postgres writes) — golden path, 8 checks, all
passing: login → book two appointments → My Appointments shows 2 upcoming
→ cancel one (1 upcoming, 1 cancelled) → reschedule the remaining one
(slot options shown, new time differs from the old) → history tab loads
without error → "Book an appointment" returns to the booking flow.
Screenshots reviewed directly (upcoming list; history tab after
reschedule).

**A test-script bug caught before trusting the result:** the first
Playwright run failed the "2 upcoming appointments" check — investigation
found the second booking genuinely existed (`status='BOOKED'`) but was
classified as `history`, and confirmed via `date -u` that the booked slot
(the first available calendar day, i.e. "today") had genuinely elapsed by
the time the listing step ran later in the same script — the same
"today can already be past by a later test step" pitfall this project's
own pytest suite already documents and works around (see
`tests/test_booking_flow.py`'s module docstring, `date_option='5'` rather
than `'1'`). This was a bug in the throwaway E2E script, not the
application: `list_patient_appointments_service`'s classification was
correct for the real wall-clock time. Fixed by having the script pick a
later available calendar day for both bookings, matching the established
project convention; the corrected script then passed cleanly.

## VERIFY

- Full backend suite: **98 passed, 0 failed** (73 pre-existing + 25 new),
  real Postgres — run twice: once mid-phase, and again as a final check
  after the environment interruption noted below, with identical results.
- Live WhatsApp reschedule smoke test via curl, end to end (booked at
  9:00 AM IST, rescheduled through the full menu flow to a new slot,
  confirmed via direct DB query that the old row became `CANCELLED` and
  the new row is `BOOKED`).
- `npm run build`: clean, no TypeScript errors.
- Live browser E2E, described above: 8/8 passing, screenshots reviewed.

**Environment note, not a code issue:** partway through this phase's
final verification, this container's Postgres service stopped running
independently of any of this phase's code or tests (its `peer` auth also
no longer matched the container's current OS user, following an
environment/session reset). This was a container/infrastructure issue,
not an application bug — it was diagnosed and repaired (restoring the
`pg_hba.conf` ownership a fix attempt had briefly disturbed, then
starting the Postgres cluster), and the full pytest suite was re-run
afterward against the live database with the same 98/98 result recorded
above, closing out this note.

## REPORT

**Behavior change for WhatsApp:** one bug fix
(`get_upcoming_booked_appointments()` now shows doctor-local time
correctly in the cancel/reschedule menus) — a correction, not a feature
or flow change. The reschedule conversation flow itself, its messages,
and its `next_step`/session behavior are unchanged; only its
implementation now calls the shared service.

**Behavior change for any existing REST endpoint:** none.

**New, fully working, browser- and cross-channel-verified:** patient
self-service viewing (upcoming/history/cancelled), cancellation, and
rescheduling from the web, sharing every rule with WhatsApp through the
shared service layer — no second implementation of ownership,
concurrency, or booking-window logic.

**Not in this phase, on purpose:**
- Doctor/staff-facing views of appointments — later phase.
- Push/SMS notification on cancel or reschedule — WEB P8 territory (mock
  SMS delivery).
- Editing patient profile fields — never requested by any phase spec.

## STOP

Per the phase-gate rule: stopping here. Waiting for approval before WEB
P5 begins.
