# WEB P11 — Admin/Staff Frontend — Report

Phase scope: the admin-facing frontend UI, consuming the REST admin
surface built across WEB P5–P10 — staff auth, admin CRUD RBAC, doctor
schedule date ranges, mock notifications, admin appointment management,
and the security pass. Every one of those phases' reports named this
explicitly as deferred: "Any admin-facing frontend UI — this phase is
REST-only" (WEB P6, P7, P9), "no frontend work was in this phase's
scope" (WEB P5). `docs/WEB_EXPANSION_ARCHITECTURE.md` §9 step 7 groups
it as "Frontend build — patient UI, then admin UI"; the patient half
shipped in WEB P3/P4, so this phase is the second half, and the final
phase in the WEB P1–P11 sequence.

## PLAN

No new backend work — this phase builds against the API surface exactly
as it stands after WEB P10, per the architecture doc's own framing
("frontend build... against the now-stable API surface"). Mapped every
admin-reachable REST endpoint from P5/P6/P7/P9 against a page/panel
before writing any component:

| REST surface | Admin UI panel |
|---|---|
| `/auth/staff/login`, `/me`, `/logout` | Staff login + session (`AdminApp`) |
| `/auth/staff/accounts` (ADMIN only) | Staff Accounts panel |
| `/departments` | Departments panel |
| `/doctors`, `/doctors/{id}/departments` | Doctors panel + department assignment |
| `/doctors/{id}/schedule` (ADMIN only) | Recurring schedule, in doctor detail |
| `/doctors/{id}/blocks` (either role) | One-off blocks, in doctor detail |
| `/appointment-types` | Appointment Types panel |
| `/doctors/{id}/appointment-types` (ADMIN only) | Appointment-type assignment, in doctor detail |
| `/patients` (either role) | Patients panel |
| `/appointments` (list/create/cancel/reschedule, either role) | Appointments panel |

**A real gap found while planning, deliberately not backend-fixed in
this phase:** no API exposes or sets a doctor's `timezone` — not in
`GET /api/doctors`, not in `POST /api/doctors`. `doctors.timezone`
defaults to `'Asia/Kolkata'` at the schema level
(`migrations/0001`) and nothing in this project's REST surface can ever
set it to anything else. This matters for the admin UI specifically
because `doctor_blocks` and `appointments.py`'s create/reschedule
endpoints take full ISO datetimes with an explicit UTC offset — the
admin needs to know a doctor's timezone to construct a correct one.
**Decision: don't add a `timezone` field to `doctors.py` in this
phase** — this phase's own framing is "build the frontend for the
existing API," and adding new backend capability is a different kind
of change from the rest of this phase. Since every doctor reachable
through any current API caller is `Asia/Kolkata` by the column's own
default, the admin UI's block-creation and appointment-creation forms
assume IST directly (labeled as such in the UI, documented in
`types.ts`'s `DoctorBlockEntry` docstring and in the relevant
components) — not a workaround for a UI gap, but the actual, only
possible truth about doctor timezone in this system today. Flagged
here as a real limitation for whoever adds multi-timezone doctor
support later, not silently baked in.

**Routing decision**: `/admin` for the staff/admin UI, everything else
for the patient UI — one Vite app, a plain `window.location.pathname`
check in `main.tsx`, no router dependency. Two paths is too small a
surface to justify a new dependency (matching this project's "propose
a specific new dependency only when actually needed" posture from
`docs/WEB_EXPANSION_ARCHITECTURE.md` §10 item 6); the same
`window.location.href` navigation the patient/admin cross-links use
would work identically with a router installed later if the surface
grows.

## IMPLEMENT

**`frontend/src/api.ts`**: a second token key (`staff_session_token`,
separate localStorage entry from the patient token) with its own
get/set/clear functions — never the same storage as the patient
session, matching `app/services/staff_auth.py`'s own "never
authenticatable through each other's token" design. `request()`'s
`auth` option now accepts `boolean | 'staff'` (`true` still means
"patient", unchanged, so none of the ~7 existing patient-flow call
sites needed touching) so one shared request helper serves both token
types without ever mixing them up. ~25 new exported functions for
every admin endpoint above.

**`frontend/src/types.ts`**: `Staff`, `StaffAccount`,
`AppointmentTypeSummary` (the plain catalog shape — no
`duration_minutes`, since duration is set per doctor assignment, unlike
the existing `AppointmentType` interface which already covers that
assignment shape), `DoctorScheduleEntry`, `DoctorBlockEntry` (with the
IST-assumption docstring above), `AdminAppointment`,
`AdminAppointmentActionResult`.

**`frontend/src/admin/`** (new directory, 8 components):
- `StaffLoginFlow.tsx` — username/password, mirrors `LoginFlow.tsx`'s
  shape.
- `AdminApp.tsx` — session check + shell: a left nav (role-aware —
  "Staff Accounts" only rendered for ADMIN, matching the RBAC table)
  and a section switcher using local state, the same pattern
  `App.tsx`'s `view` state already uses for the patient UI.
- `DepartmentsPanel.tsx`, `AppointmentTypesPanel.tsx`,
  `PatientsPanel.tsx`, `StaffAccountsPanel.tsx` — list + create (+
  activate/deactivate for staff accounts), each hiding its create form
  for a non-ADMIN session where the corresponding write is ADMIN-only
  (server-side enforcement is what actually matters — WEB P6/P9 already
  proved that — hiding the form is just avoiding a pointless 403 round
  trip for a control the viewer could never use).
- `DoctorsPanel.tsx` + `DoctorDetail.tsx` — list/create doctors, then a
  per-doctor detail view covering all four of a doctor's sub-resources:
  department assignment, recurring weekly schedule (WEB P7 date
  ranges included), one-off blocks (either role, per the RBAC table),
  and appointment-type assignment with duration.
- `AppointmentsPanel.tsx` — the WEB P9 admin appointment surface:
  doctor/patient/status filters, "book on behalf of a patient" (plain
  datetime input, not the patient flow's calendar/slot picker — staff
  creation bypasses the booking window by design, per WEB P9's report,
  so the same UX constraints don't apply), inline reschedule, cancel.

**`frontend/src/main.tsx`**: mounts `AdminApp` for `/admin`, `App`
(unchanged) for everything else.

**`frontend/src/styles.css`**: new admin-specific rules
(`.admin-shell`, `.admin-nav`, `.data-table`, `.inline-form`,
`.tag-list`, `.detail-section`, etc.) — deliberately not reusing the
patient flow's single narrow `.card`, since an admin dashboard's
multi-column data (staff account tables, appointment listings, a
doctor's four sub-resource sections at once) doesn't fit a phone-width
card. `button.link`'s styling was generalized to also cover `a.link`
(the "Patient site" cross-link uses a real anchor, not a button, since
it's a navigation, not an action).

## TEST

- `tsc -b && vite build`: clean, no TypeScript errors (two minor
  unused-variable errors caught and fixed during development, before
  this was considered done).
- `npm run lint` (oxlint): exits 0. The `set-state-in-effect` warnings
  it reports on every new admin component are the identical warning
  already present on `App.tsx`, `Calendar.tsx`, and `MyAppointments.tsx`
  today — the same data-fetching-in-`useEffect` pattern this project's
  patient UI has used since WEB P3, not a new pattern introduced here.
- Full backend suite: **154 passed, 0 failed**, run twice consecutively
  — unaffected, since this phase makes no backend changes.

**Live browser E2E**, not just a build check — a Playwright script
driving the real dev server + dev database through the entire admin
golden path, 18 checks, all passing: staff login → create a department,
an appointment type, and a doctor → assign the department to the
doctor → add a recurring Monday 09:00–17:00 schedule → add a one-off
block → assign the appointment type to the doctor with a duration →
create a patient → book an appointment on that doctor/patient/type from
the Appointments panel → filter the list by doctor → reschedule it
(confirmed via a new BOOKED row at the new time, distinct from the
original row which correctly shows CANCELLED — not just "the patient's
name appears somewhere," which the first draft of this test wrongly
asserted and which mis-diagnosed a real UI behavior as a bug before the
assertion itself was corrected) → cancel the rescheduled appointment
(confirmed CANCELLED) → create a STAFF account → deactivate it → log
out back to the login screen.

**Role-boundary check**, separate from the golden path above: logged in
as a plain STAFF account and confirmed the nav hides "Staff Accounts"
entirely and the Departments panel hides its create form — the UI
correctly reflects what WEB P6/P9's own tests already proved the server
enforces, rather than just assuming it would.

**Patient-site regression check**: loaded `/` (not `/admin`) and
confirmed the existing patient login screen renders exactly as before
— the routing change in `main.tsx` doesn't affect the already-shipped
patient flow.

## VERIFY

- `tsc -b && vite build`: clean.
- `npm run lint`: exits 0.
- Full backend suite: **154 passed, 0 failed**, run twice.
- Live Playwright E2E against the dev server + dev database: 18/18
  checks passed, covering every admin panel end to end.
- Role-boundary and patient-site-regression checks, both live, both
  described above.

## REPORT

**Behavior change for WhatsApp:** none.

**Behavior change for the existing patient web frontend:** none — `/`
serves the exact same `App.tsx` as before; `/admin` is new, additive
routing in `main.tsx` only.

**Behavior change for the REST API:** none — this phase adds no
backend code.

**New, fully tested and live-verified:** the complete staff/admin
frontend — login, department/doctor/appointment-type management,
per-doctor recurring schedule and one-off block management, staff
account management, patient lookup, and the full admin appointment
workflow (list/filter, book on behalf of a patient, reschedule,
cancel) — closing the "admin-facing frontend UI" gap every admin-surface
phase since WEB P5 deferred by name.

**Not in this phase, on purpose:**
- A `doctors.timezone` field on the create/list API, or any other new
  backend capability — this phase builds against the existing API
  surface only (see PLAN's timezone finding above, flagged for a future
  phase, not silently worked around).
- A client-side router library — two paths (`/`, `/admin`) don't need
  one yet; revisit if the admin surface grows enough sub-routes to want
  real nested routing.
- Production static-hosting configuration for the `/admin` path (the
  host must fall back to `index.html` for any client-routed path, the
  same SPA-fallback requirement any client-routed path already has) —
  an infrastructure/deployment decision this project has consistently
  left for whoever owns the actual hosting choice (the same category as
  WEB P3's CORS deferral, which WEB P10 then resolved on the backend
  side only — the frontend-hosting half of that story is still open).
- Editing an existing department/doctor/appointment-type's name, or any
  other update beyond what WEB P6/P7/P9's REST surface itself exposes
  — this phase is a frontend for the existing API, not a reason to add
  new REST capability.
- Staff password reset / "forgot password" — not built in WEB P5
  either (its own report named this out of scope); nothing for this
  phase to add a UI for.

## STOP

Per the phase-gate rule: stopping here. This is the last phase in the
WEB P1–P11 sequence — no further phase to wait for approval on, unless
new work is requested.
