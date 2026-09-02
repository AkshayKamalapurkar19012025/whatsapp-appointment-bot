# WEB P3 — Patient Booking UI — Report

Phase scope: build the patient-facing web UI (React + TS + Vite, per the
WEB P0 decision), responsive desktop/mobile, the booking flow through to a
confirmation page, calendar-window rules backend-enforced, no doctor
timezone exposed, all data through FastAPI, test the full flow.

## PLAN

P1/P2 built the reusable pieces; P3 is where they meet an actual
patient-facing REST surface plus the frontend that consumes it. Two new
backend endpoints were needed (P1 explicitly built the service layer but
no new routes beyond what already existed):

1. `GET /api/web/calendar` — per-day availability for a calendar month,
   wrapping `availability_engine.list_available_dates_in_range`,
   intersected with `is_within_booking_window` so a date outside the
   window (including a past date) is always `false` regardless of raw
   slot computation. Left unauthenticated, matching the existing (also
   unauthenticated) `POST /api/availability` single-day endpoint's
   security posture — this is doctor schedule/capacity information, not
   patient data.
2. `POST /api/web/appointments` — the actual booking-creation step.
   Requires a valid session (`Depends(get_current_patient)`, from WEB
   P2); the request body has **no `patient_id` field at all** — the
   session's own patient is always used, so there's nothing to spoof.
   Calls `create_appointment_service(..., enforce_booking_window=True)`,
   the exact function the existing REST endpoint and the WhatsApp flow
   both call — same concurrency guarantees, no third implementation.

Department/doctor/appointment-type browsing reuse the existing `GET
/api/departments`, `GET /api/departments/{id}/doctors`, and `GET
/api/doctors/{id}/appointment-types` endpoints directly — already
public, already tested, not patient-specific, so nothing new was built
for them.

**One deliberate deviation from the prompt's literal text, flagged, not
silent:** the WEB P3 spec lists the booking flow as Department → Doctor →
**Date → Time Slot → Appointment Type** → Patient Details → Confirmation.
That literal order isn't achievable as written: slot computation (both
here and in the WhatsApp flow) needs `appointment_type_id` already known,
because each type has its own duration and duration determines the slot
boundaries — see `get_available_slots`'s signature, unchanged since
before this phase. The frontend instead follows Department → Doctor →
**Appointment Type → Date → Slot** → Review → Confirmation, which is
`app/api/booking.py`'s actual, real WhatsApp step order. Global rule 4
("do not duplicate booking logic between Web and WhatsApp") means reusing
the shared engine's real ordering constraint too, not inventing a second
one that would require a different backend implementation.

"Patient Details" is interpreted as the authenticated patient's own name
(already known from WEB P2 login), shown on the Review step for
confirmation before booking — not a profile-editing form, since no
edit-profile capability exists or was requested by any phase's spec.

## IMPLEMENT

**Backend, new:**
- `app/api/patient_booking.py` — the two endpoints above.
- Mounted in `app/main.py`.

**Frontend, new (`frontend/`, React + TypeScript + Vite):**
- `src/api.ts` — the only fetch layer; Bearer token in `localStorage`.
- `src/format.ts` — date/time formatting that reads wall-clock digits
  directly out of the backend's ISO strings, never through a `Date`
  object's local-timezone conversion (see "A real bug found" below for
  exactly why this matters).
- `src/LoginFlow.tsx` — mobile number → OTP → login/registration, using
  WEB P2's endpoints as-is.
- `src/Calendar.tsx` — month grid against `GET /api/web/calendar`,
  light-orange unavailable dates, past dates and out-of-window months
  disabled (the "next month" arrow is disabled once the following month
  would fall outside the booking window, computed from the endpoint's
  own `booking_window_end`).
- `src/BookingFlow.tsx` — the full step flow through to confirmation.
- `src/styles.css` — plain CSS, mobile-first, no UI kit dependency —
  "simple neutral healthcare UI" as specified.
- `vite.config.ts` — dev-server proxy of `/api` to the FastAPI backend,
  so the browser sees one origin in development without needing CORS
  configuration (explicitly a WEB P10 topic, not pulled forward here).

**Tests:** `tests/test_patient_booking_api.py`, 6 backend tests — see
TEST below.

## A real bug found and fixed by actually testing in a browser

Per my own standing instruction to verify UI changes in a real browser
before reporting done, not just pass pytest: the first full run through
Chromium showed the confirmation page displaying **"Time: 4:00 AM"** for
an appointment actually booked at 10:00 AM.

Root cause: `POST /api/web/appointments`' response (via
`create_appointment_service`, unchanged since WEB P1) returns `start_at`
after it has round-tripped through Postgres — and Postgres/psycopg
normalize a `TIMESTAMPTZ` to the connection's own timezone (UTC here) on
read-back, not the original `+05:30` offset the request was sent with.
This is a **pre-existing characteristic** of `appointments.py`'s
original, unmodified code (confirmed: `POST /api/appointments`'s
response has always done this) — it was never noticed before because
nothing previously *displayed* that value to an end user. WhatsApp's own
confirmation message never re-states the time either, for the same
underlying reason, just never surfaced as a problem.

**Fix:** the confirmation page displays `selectedSlot.start_at` (the
value already shown correctly in the doctor's local time during the
"choose a time" step, computed directly by `get_available_slots`) instead
of `confirmed.start_at`. A regression guard was added to the browser
check asserting the confirmation time equals the review-step time
character-for-character — this exact bug could not silently return.

## TEST

**Backend** (`tests/test_patient_booking_api.py`, 6 tests): calendar
returns per-day availability matching the doctor's schedule, with past
dates always `false`; calendar rejects a month entirely outside the
booking window (409); booking requires authentication (401 without a
session); booking succeeds for an authenticated patient and the created
row's `patient_id` matches the session; a client-supplied `patient_id` in
the body is ignored — the appointment always belongs to the session
owner; booking-window enforcement is reachable through this endpoint
(409 for a date past the window).

**Browser** (Playwright against a real Chromium, driving the actual dev
server + backend — not a mock), 15 checks, all passing after the fix
above:
- Full golden path: mobile number → OTP (retrieved via the dev-lookup
  endpoint, the same way a tester would) → registration (new number) →
  department → doctor → appointment type → calendar → date → time slot →
  review → confirm → confirmation page → logout.
- Calendar renders both `available` and `unavailable` (light-orange)
  cells; at least one available date is clickable.
- Time slots display as "10:00 AM – 10:30 AM", not a raw offset.
- Review page shows patient name, doctor, and duration correctly.
- Confirmation page shows the appointment type and the **same** time as
  the review step (the regression guard for the bug above).
- No raw timezone offset or zone name (`+05:30`, `Asia/Kolkata`) appears
  anywhere in the rendered page text — "do not expose doctor timezone",
  checked against the actual DOM, not just code review.
- Logout returns to the login screen.
- A 375×812 mobile viewport renders the login screen correctly (screenshot
  captured).

Screenshots captured during this verification (mobile login, the
calendar month view showing unavailable/available styling, and the fixed
desktop confirmation page) were reviewed directly, not assumed correct
from code alone.

## VERIFY

- Full backend suite: **73 passed, 0 failed** (67 pre-existing + 6 new),
  real Postgres.
- `npm run build` (`tsc -b && vite build`): clean, no TypeScript errors.
- Live smoke-tested end to end through a real browser, twice (before and
  after the timezone-display fix).

## REPORT

**Behavior change for WhatsApp or any existing REST endpoint:** none.

**New, fully working, browser-verified:** the patient web booking UI,
department through confirmation, sharing every booking rule (schedule,
blocks, overlap, advisory lock, EXCLUDE constraint, calendar-window) with
WhatsApp via WEB P1's shared service layer — nothing here is a second
implementation of those rules.

**Not in this phase, on purpose:**
- My Appointments / cancel / reschedule from the web — WEB P4.
- Actually sending a confirmation SMS — WEB P8 (the confirmation page
  says as much to the patient, rather than silently doing nothing or
  overclaiming).
- CORS configuration for a non-proxied deployment — WEB P10.
- Editing patient profile fields — never requested by any phase spec.

**A bug found here would have shipped invisibly without browser
testing** — pytest alone had no way to catch a value that's structurally
correct JSON but wrong when read by a human. Recorded here as the reason
this phase's verification included an actual browser, not just API
tests.

## STOP

Per the phase-gate rule: stopping here. Waiting for approval before WEB
P4 (Patient Appointments — cancel/reschedule from the web) begins.
