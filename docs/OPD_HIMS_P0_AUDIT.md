# OPD/HIMS Master Spec — Phase 0 Repository Audit

Status: **read-only discovery deliverable**, produced per the master spec's own
phase-gate rule ("Start with PHASE 0 — REPOSITORY AUDIT... Do not implement
code until you have inspected the existing repository sufficiently... DO NOT
MODIFY CODE YET"). No application code was changed to produce this report.

This audit exists because the master spec's framing ("We already have an
existing HospitalOS / OPD application... evolve it into a production-ready
Advanced OPD/HIMS") needs to be checked against what is actually in this
repository before any of the 13 implementation phases start. The short
version: the framing is *roughly* right — this is not a bare WhatsApp bot
anymore — but one architectural assumption in the spec (Principle 2:
"Encounter is the clinical context") does not hold today, and that gap has
to be resolved as a decision, not discovered mid-implementation.

---

## 1. Architecture summary

This started as a WhatsApp-only appointment bot (FastAPI + Postgres) and has
grown, over ~27 migrations and 11 documented "WEB_P" phases (`docs/WEB_P1`
through `WEB_P11`), a full admin web application on top of the same backend:

- **Backend**: FastAPI, 21 routers under `app/api/`, business logic in
  `app/services/` (13,400 lines across api+services). Postgres via
  `psycopg`/`psycopg_pool`, plain SQL migrations (`migrations/0001`...`0027`),
  no ORM.
- **Frontend**: React + TypeScript + Vite, one app with a patient-facing tree
  (`frontend/src/*.tsx` — WhatsApp-style OTP login, browse doctors, book/
  reschedule/cancel, "My Appointments") and an admin tree
  (`frontend/src/admin/*.tsx` — dashboard, patients, doctors, departments,
  schedules, appointments, live queue, billing, staff accounts, a public
  queue display board).
- **Design system**: centralized CSS custom properties (`docs/design-system.md`,
  `frontend/src/styles.css`) — token-based colors, consistent status→color
  mapping, documented button hierarchy. This already satisfies most of the
  master spec's §10–§12 (color semantics, typography, no-per-page-hex-values).
- **Auth/RBAC**: real, not stubbed. `staff` table with `role IN ('ADMIN',
  'STAFF')`, bcrypt-style password hashing, DB-backed opaque bearer sessions,
  lockout after failed attempts, a `require_role()`-style FastAPI dependency
  gating routes. Patient auth is mobile+OTP (mock provider, dev-only code
  lookup). This is a fully-built version of what the master spec's §56
  ("Security") and §6 ("Role-Based Work") ask for, at least for the two roles
  that exist so far.
- **Scheduling/availability**: single consolidated engine
  (`app/services/availability_engine.py`), used by both the WhatsApp
  conversational flow and the REST/web paths — the master spec's repeated
  instruction ("do not create another scheduling engine", "use existing
  doctor availability logic") is already satisfied; there is exactly one.
- **Concurrency**: `pg_advisory_xact_lock` + a Postgres `EXCLUDE USING gist`
  constraint as a structural backstop against double-booking, validated by
  dedicated concurrency tests (`tests/test_concurrency.py`,
  `tests/test_exclusion_constraint.py`). This is the master spec's §57/§58
  ("concurrency", "idempotency") already done correctly, and it is the one
  invariant every later phase must not silently bypass.
- **Tests**: 419 test functions across 40 files, real-Postgres-backed
  (`tests/conftest.py` provisions a `*_test` DB, no mocking of the DB layer).
- **IPD**: `ipd-service/schema/0001_baseline_ipd_schema.sql` — a *schema
  sketch only* (no app code, no migrations run, not wired to anything). It
  is deliberately designed as a **separate service with its own database**,
  referencing the OPD app's `patients.uhid` as a loose string, not a live
  foreign key.

## 2. Current patient/visit workflow (what already works end-to-end)

There is already one connected journey, just not phrased as "Encounter":

```
Patient (WhatsApp OR web, mobile+OTP)
   -> Appointment (PENDING/CONFIRMED, doctor+department+slot, advisory-locked)
   -> Confirm (staff or auto)
   -> Check-in (mark_visited_service) -> status CHECKED_IN
   -> Payment (record_payment_service / waive_consultation_fee_service)
        -> only once paid/waived does token generation unlock
   -> Queue token (generate_queue_token_service) -> integer token_number,
        with hold/recall and priority-flagging on top (migration 0027)
   -> Live queue (doctor queue view, public DisplayBoard)
   -> mark_completed_service -> COMPLETED
   -> No-show / cancel / reject / reschedule, each a guarded status transition
   -> Billing: consultation_fee + invoice_line_items (ad-hoc charges),
        refunds (migration 0025), per-visit invoice_number (INV-00000123)
   -> Patient identity: permanent uhid (HOS-0000123, migration 0024),
        distinct from whatsapp_number
```

This already covers, functionally, master-spec screens: OPD queue, check-in,
token, live queue (with hold/priority — more advanced than the spec's basic
model), billing, payment, receipt (invoice_number), patient UHID, doctor/
department admin, staff RBAC, a public queue display board (not even asked
for in the spec, but matches its "no dead ends" operational spirit).

## 3. The architectural gap that matters: there is no Encounter

**Everything above is modeled as columns and status transitions on a single
`appointments` row.** Check-in, token, hold/priority, payment, refund, and
invoice line items are all `appointment_id`-keyed. There is no separate
`encounters` table, and no `orders` table of any kind.

The master spec's Principle 2 is explicit: *"The patient is permanent. The
encounter is specific to a care episode... Orders, consultation, billing and
queue activity must belong to the correct encounter,"* and Principle 3:
*"Do NOT create disconnected modules... The encounter determines whether \[an
order\] originated from OPD/IPD/Emergency."*

Today:
- There is no clinical layer at all yet — no vitals, chief complaint,
  consultation notes, diagnosis, prescriptions, lab orders, radiology orders,
  or pharmacy. Phases 5–8 of the master spec (Triage, Consultation, Orders,
  Diagnostics, Pharmacy) are **greenfield**, not "evolve existing" — there is
  nothing to reuse for these because nothing like them exists.
- The one piece of forward-looking design that *does* exist for this
  (`ipd-service`) chose the opposite architecture from what the spec asks
  for: a **separate database/service**, patient linked only by a
  non-enforced string (`patient_uhid` with no FK), explicitly justified as
  "IPD's failure modes must never be able to take down appointment booking."
  That is a defensible reliability argument on its own, but it directly
  conflicts with master-spec §76 ("IPD Compatibility" — orders, results,
  medications, billing, documents, and patient timeline must be **shared**
  across OPD/Emergency/IPD encounters) and §4 ("never create disconnected
  modules").

This is the one decision that has to be made *before* Phase 3
("OPD Encounter Foundation") of the master spec's own phased plan, because it
determines the shape of every table after it (orders, lab_orders,
radiology_orders, prescriptions, all keyed to "the correct encounter" per the
spec):

**Option A — Add `encounters` inside the existing OPD database**, with
`appointments` becoming (or gaining) a 1:1 link to an `encounters` row that
new clinical tables (vitals, consultations, diagnoses, orders,
prescriptions) key off. IPD is then a second `encounter_type` in the *same*
table, sharing orders/results/billing/timeline the way §76 asks for. This
satisfies the spec's data-model principles directly but means the existing
`ipd-service` sketch gets abandoned/rethought, and the existing
`appointments`-centric billing (invoice_line_items, refunds keyed to
`appointment_id`) would need to be re-pointed at `encounter_id` (an additive
migration, not destructive, but real work touching billing/queue/payment
code that is currently working and well-tested).

**Option B — Keep the service-boundary pattern the `ipd-service` sketch
already chose**: OPD stays appointment-centric, a shared "encounter registry"
or event/order bus sits between OPD, IPD, and future Lab/Radiology/Pharmacy
services, each owning its own data but referencing patient/encounter by ID
only. This preserves the "IPD can't take down OPD" isolation already argued
for, but is a materially different, harder architecture to build correctly
(cross-service consistency, no real FKs, distributed transactions for
"orders span OPD+Lab"), and doesn't match how the rest of *this* codebase is
built today (one Postgres DB, real FKs, real transactions everywhere else).

I'm not picking between these — this is exactly the kind of call the master
spec's own §10 ("Implementation Behavior for Claude": audit → plan → approve
architecture → implement) reserves for explicit approval, and it changes the
shape of Phases 3 through 10, not just one file.

## 4. Reusable functionality (do not rebuild)

- Scheduling/availability engine (`app/services/availability_engine.py`)
- Concurrency-safe booking (advisory lock + exclusion constraint pattern)
- Staff auth/RBAC + patient OTP auth, session middleware
- Appointment status state machine (`_transition_appointment_status` +
  guarded service functions per transition) — this is already the "explicit
  transitions, no arbitrary state changes" model the spec's §25 asks for,
  just scoped to appointments rather than a queue-token entity
- Queue token generation, hold/recall, priority — already more capable than
  the spec's baseline queue model (§24–§25)
- Billing: consultation fee, ad-hoc line items, refunds, invoice numbers
- Design system / token-based theming, status→color mapping
- Admin shell: sidebar with "Coming soon" disabled-item support already
  built (`AdminSidebar.tsx`), dashboard, patients, doctors, departments,
  schedule config (weekly + date-ranged + one-off blocks), patient visit
  history modal, billing panel

## 5. Gaps vs. the master spec (greenfield, not "evolve")

Phases with **no existing code to build on** at all:
- Triage / vitals (§26)
- Clinical consultation workspace, diagnosis, clinical notes (§27–§29)
- Orders spine — lab, radiology, procedures, external referral (§30–§31)
- Laboratory workflow (§32)
- Radiology workflow (§33)
- Prescription (§35)
- Pharmacy + inventory-aware dispensing (§36–§37)
- Patient 360 / unified timeline (§44) — patient history exists only as
  "list of past appointments" (`list_patient_appointments_service`), not a
  cross-domain event timeline
- Packages, insurance/TPA extension points (§39–§40)
- Exception engine (§46–§47) — no alerting/threshold layer exists
- Audit log as a queryable entity (§55) — some actions are attributable
  (`refunded_by`, `priority_set_by`, `added_by` columns exist per-table) but
  there is no unified `audit_logs` table/view

## 6. Database model (as-is)

Core tables (all in the one OPD Postgres DB): `departments`, `doctors`,
`appointment_types`, `department_doctors`, `doctor_appointment_types`,
`doctor_schedule` (day-of-week + optional date range), `doctor_blocks`,
`patients` (+ generated `uhid`), `appointments` (the de facto encounter —
status, token_number, payment_status/amount, refund_*, is_priority,
queue_held_at, invoice_number), `invoice_line_items`, `staff`,
`staff_sessions`/`patient_sessions` (session tables from the auth phases),
plus WhatsApp booking-session state. `ipd-service` is a separate,
disconnected schema sketch (see §3).

## 7. API model (as-is)

21 routers under `/api`, REST-conventional (resource-noun paths, standard
HTTP verbs), Bearer-token auth, role-gated via a shared dependency,
consistent pagination/filtering already established for list endpoints per
`docs/WEB_P9_ADMIN_APPOINTMENTS.md`. No `/api/orders`, `/api/lab`, `/api/
radiology`, `/api/pharmacy`, `/api/billing` (billing is appointment-nested:
`GET/POST` on `/api/appointments/{id}/...`) — these don't exist yet because
the domains don't exist yet.

## 8. UI model (as-is)

Admin app already uses most of the master spec's §9 interface-pattern
taxonomy correctly: full workspaces (Dashboard, Queue, Billing), guided
modals (Add Doctor, Configure Schedule, Patient Form), drawers (Appointment
Details), a patient-context header pattern on relevant screens. The
information architecture is flatter than the spec's target (§7) — no
grouped sections like "PATIENT CARE / CLINICAL / DIAGNOSTICS" yet — but the
sidebar already supports disabled "Coming soon" items, so extending the IA
without a redesign is straightforward.

## 9. Testing status

419 tests, real-Postgres-backed, covering: concurrency/exclusion
constraints, availability/scheduling (including date-first and boundary
cases), patient/staff auth, RBAC, queue tokens (including hold/priority),
billing/refunds, timezone handling, phone normalization, admin CRUD across
every existing entity. No frontend/e2e test suite exists (no Playwright/
Cypress config found) — UI changes so far have been manually verified per
session (consistent with this session's own instructions to browser-test UI
changes).

## 10. Recommended migration strategy

1. **Resolve the encounter-model decision (§3) before writing any new
   migration.** Everything else compounds on top of it.
2. If Option A is chosen: add `encounters` additively (nullable FK on
   `appointments`, or `appointments` becomes the OPD encounter and a
   `encounter_type` discriminator gets introduced for future IPD/Emergency
   rows) — no destructive change to the 27 existing migrations, matching
   this repo's own established discipline (new migration file per change,
   additive/nullable-first, `scripts/migrate.py`).
3. Follow the master spec's own phase order (Phase 1 design-system polish →
   Phase 2 patient identity, both largely already done → Phase 3 encounter
   foundation, the real starting line) rather than jumping to clinical
   screens before the data model under them is settled.
4. Preserve, unchanged: the advisory-lock/exclusion-constraint booking path,
   the single availability engine, the WhatsApp conversational flow
   (`booking.py`) — none of the master spec's asks require touching these.

## 11. Risks

- **Scope**: the master spec is a ~100-section, 13-phase, multi-month
  program (full clinical + diagnostics + pharmacy + billing/insurance +
  operational intelligence layer). It explicitly forbids attempting it in
  one uncontrolled change (§96–§100); this audit is the Phase 0 gate, not a
  green light to start writing clinical tables.
- **The `appointments`-as-encounter coupling** is the highest-risk spot to
  get wrong: billing, queue, and payment code that is currently correct and
  well-tested would need re-pointing if Option A is chosen, and that touches
  the one part of the system with the strongest existing correctness
  guarantees (concurrency, idempotency). Any refactor here needs the same
  rigor as the WEB_P1 timezone-util extraction (pure move, full regression
  suite as the safety net) — not a rewrite.
- **`ipd-service` divergence**: if left as a separate-database sketch while
  OPD clinical data lives in the main DB, "shared timeline/orders/results
  across OPD+IPD" (spec §76) becomes a cross-service integration problem,
  not a query. This should be decided, not left to drift.

---

## STOP

Per the master spec's own phase-gate rule, no implementation begins until
this audit is reviewed and the Option A/B decision in §3 is made. Phase 1
(design system polish) and Phase 2 (patient identity) are largely already
satisfied by the existing codebase and could start immediately regardless of
that decision; Phase 3 onward cannot.
