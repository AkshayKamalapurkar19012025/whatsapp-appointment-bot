# OPD/HIMS Master Spec — Phase 3: Encounter Foundation

Follows `docs/OPD_HIMS_P0_AUDIT.md`. That audit identified one decision
blocking every phase past patient identity: whether "Encounter" (the
master spec's Principle 2) gets added inside this app's own database
(Option A) or modeled as a separate service, the direction the
`ipd-service/` sketch had already taken (Option B). Decision: **Option A**.

Phase 1 (design system) and Phase 2 (patient identity/UHID) were already
substantially satisfied by the existing codebase per the audit, so this is
the first phase with real implementation work.

## What changed

Every OPD appointment now opens exactly one `encounters` row — the entity
future clinical/order tables (vitals, consultations, diagnoses, lab/
radiology/pharmacy orders — Phases 5–8) will key off, per the master
spec's Principle 2/3. Scope was deliberately kept to the data-model
foundation only: no new screens, no new REST resource, no clinical tables
yet — those are later phases' jobs, and building them before this
foundation existed would have meant redoing them once it landed.

Specifically:
- An encounter opens in the same transaction as appointment creation
  (`create_appointment_service`), for every creation path — WhatsApp,
  admin REST, and the patient web API all go through this one function,
  so no channel needed separate wiring.
- A reschedule (`reschedule_appointment_service`) carries the *same*
  encounter forward onto the new appointment row, rather than closing one
  and opening another — a reschedule is the same care episode moved in
  time, not a new one.
- The four staff-driven terminal transitions — cancel, reject, complete,
  no-show — each close the encounter (`status='CLOSED'`, `closed_at`
  set) in the same transaction as the appointment's own status update.
- Every pre-existing appointment was backfilled with exactly one
  encounter, computed from its current status (open if still live, closed
  with the appointment's own `updated_at` as `closed_at` if already
  terminal) — no appointment is left without one.

## Files changed

- `migrations/0028_encounters.sql` — new. Creates `encounters`
  (additive; no column added to `appointments`, no existing constraint
  touched) and backfills it for every existing row.
- `app/services/appointment_services.py` — `create_appointment_service`
  now opens an encounter and returns `encounter_id`;
  `reschedule_appointment_service` carries it forward;
  `cancel_appointment_service`, `_transition_appointment_status` (reject/
  complete), and `mark_no_show_service` close it. New
  `TERMINAL_STATUSES` tuple and `_close_encounter_for_appointment` helper.
- `tests/conftest.py` — added `encounters` to `APP_TABLES` so it's
  truncated (and its identity sequence reset) between tests, same as
  every other application table.
- `tests/test_encounters.py` — new, 7 tests.
- `ipd-service/schema/0001_baseline_ipd_schema.sql` — annotated as
  superseded by the Option A decision (left in place as a record of the
  alternative considered, not deleted).

## Database changes

One new table, `encounters` (see migration for full column-level
reasoning): `patient_id` (required), `encounter_type` (`OPD`/`IPD`/
`EMERGENCY`, only `OPD` populated by any code today), `status` (`OPEN`/
`CLOSED`), `appointment_id` (nullable — reserved for a future non-OPD
encounter; required whenever `encounter_type='OPD'`, enforced by a CHECK),
`opened_at`/`closed_at`. A partial unique index enforces one encounter per
appointment; a plain index on `patient_id` serves the future Patient 360
"this patient's encounters" lookup. Zero changes to any existing table.

## API changes

`POST /api/appointments`, `POST /api/web/appointments` (patient web), and
the WhatsApp booking flow's create step now return an additional
`encounter_id` field alongside the existing appointment fields — additive,
no existing field changed or removed, no route signature changed. No new
endpoints yet; there is no `/api/encounters` resource in this phase (the
audit and this phase's own scope call for the data model landing first).

## UI changes

None. Nothing in this phase touches the frontend.

## Tests

- Added: `tests/test_encounters.py` (7 tests) — encounter opened on
  create, two visits for the same patient get two distinct encounters,
  each terminal transition (cancel/reject/complete/no-show) closes the
  encounter, reschedule carries the same encounter forward onto the new
  appointment row without leaving a duplicate or orphan.
- Full suite: 429 passed, 1 skipped, 1 failed
  (`test_date_first_lists_multiple_doctors_with_their_own_slot_counts`).
  That failure is pre-existing and unrelated — confirmed by running the
  full suite before this phase's changes (422 passed / 1 skipped / the
  same 1 failed), then again after (429 passed = 422 + this phase's 7 new
  tests / same 1 skipped / same 1 failed, nothing else regressed).

## Known issues / deliberately out of scope

- No `/api/encounters` read endpoint yet. Every consumer of encounter
  data today has to be the code that created it; a dedicated lookup
  (needed by Patient 360, Phase 10) is future work.
- `encounters.status` only distinguishes OPEN/CLOSED — it does not
  mirror `appointments.status`'s full lifecycle (PENDING/CONFIRMED/
  CHECKED_IN/...). That's deliberate: the master spec's Phase 3 exit
  criterion only asks for "Patient → OPD Encounter → Appointment/Walk-in
  works end-to-end," not a second status machine duplicating the first.
  A richer encounter-level status (if a future phase's screens need one)
  should be designed against what actually consumes it, not guessed here.
- `encounter_type` accepts `IPD`/`EMERGENCY` at the schema level, but no
  code path creates either — intentionally not implemented per the master
  spec's "do not implement future modules merely for visual completeness."

## Next phase

Phase 4 (Check-in + Queue) per the master spec is already largely covered
by existing functionality (check-in, queue tokens, hold/priority) that
this phase deliberately left untouched. The next genuinely new work is
Phase 5 (Triage + Clinical Consultation) — the first phase with real
greenfield UI and API work, and the first consumer of `encounter_id`
beyond appointment creation itself.
