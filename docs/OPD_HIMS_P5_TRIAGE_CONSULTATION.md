# OPD/HIMS Master Spec — Phase 5: Triage + Clinical Consultation

Follows `docs/OPD_HIMS_P3_ENCOUNTER_FOUNDATION.md`. Phase 4 (Check-in +
Queue) was already satisfied by existing functionality per the Phase 0
audit, so this is the first genuinely greenfield phase: nothing in the
repository did triage, vitals, or clinical documentation before this.

## Scope

The master spec's Phase 5 goal is "build the clinical core" with exit
criterion "doctor can complete an actual OPD consultation without mock
data." Implemented: vitals/triage recording, and a consultation record
(chief complaint, history, examination, diagnosis, clinical notes,
follow-up recommendation) with a draft → completed lifecycle. Explicitly
**not** in this phase (belongs to later phases per the master spec's own
phasing): orders (lab/radiology/procedures — Phase 6), prescriptions
(Phase 8), a structured/coded diagnosis list, and auto-booking a follow-up
appointment from the recommendation (see "Known gaps" below).

## What changed

**Data model.** Two new tables, both keyed off `encounters`
(migrations/0028): `vitals` (append-only — a nurse can recheck during the
same visit, "current" is just the latest row) and `consultations` (one
per encounter, DRAFT → COMPLETED). BMI is a generated column, never
client-supplied. Diagnosis is a single free-text field, not a structured/
coded table — the master spec itself warns against building entities
blind; a real diagnosis-code list is future work once there's an actual
code set to back it.

**Write gate.** Every write (record vitals, save/complete a consultation)
requires the appointment's status to be `CHECKED_IN` — deliberately
`appointments.status`, not `encounters.status`. Gating on
`encounters.status` would have created a real interaction bug: front
desk's existing one-click "Mark completed" (unchanged by this phase)
closes the encounter the moment it's clicked, which would silently lock a
doctor out of finishing documentation if staff click it first. Gating on
`CHECKED_IN` instead mirrors the exact condition every other
CHECKED_IN-scoped action in this codebase already uses (payment, queue
actions), and reflects the master spec's own Visit Completion ordering:
consultation is meant to be done *before* the visit closes out, not after.
Reads are never gated — a completed consultation stays viewable after the
visit closes, verified by test and by hand in a running browser.

**Backend**: `app/services/clinical_services.py` (new), 4 new exceptions
in `app/services/exceptions.py`, `app/api/clinical.py` (new router,
mounted under the existing `/appointments/{id}/...` URL convention rather
than inventing a separate `/encounters/...` surface the frontend would
need an extra lookup to reach).

**Frontend**: `ConsultationWorkspace.tsx` (new) — a "Full Workspace" per
the master spec's own screen-type taxonomy (§9), with a patient-context
header (name, UHID, age/gender, doctor, token — master spec Principle 4)
and two tabs, Triage/Vitals and Consultation. Reached from the live queue
(`QueueSection.tsx` gained a "Consultation" action on every row, "View
consultation" on completed ones) — no new top-level sidebar destination,
matching how Queue itself is already reached only as an action, not a
nav item.

## Files changed

- `migrations/0029_vitals_and_consultations.sql` — new.
- `app/services/clinical_services.py` — new.
- `app/services/exceptions.py` — `EncounterNotFound`, `EncounterClosed`,
  `ConsultationAlreadyCompleted`, `ConsultationIncomplete`.
- `app/api/clinical.py` — new router; registered in `app/main.py`.
- `tests/conftest.py` — `vitals`/`consultations` added to `APP_TABLES`.
- `tests/test_clinical.py` — new, 12 tests.
- `frontend/src/types.ts` — `EncounterSummary`, `Vitals`, `VitalsInput`,
  `Consultation`, `ConsultationInput`.
- `frontend/src/api.ts` — `getEncounterSummary`, `recordVitals`,
  `getLatestVitals`, `getOrCreateConsultation`, `saveConsultationDraft`,
  `completeConsultation`.
- `frontend/src/admin/ConsultationWorkspace.tsx` — new.
- `frontend/src/admin/QueueSection.tsx`, `QueuePanel.tsx`,
  `AdminApp.tsx` — wired navigation to the new workspace.
- `frontend/src/styles.css` — `.patient-context-header` and related
  classes (reuses `.doctor-form-grid`, `.inline-label`, `.tabs`/`.tab`,
  `.pill.status-*` — no other new classes needed).

## Database changes

`vitals` and `consultations` (see migration for full column-level
reasoning). Zero changes to any existing table. Both additive.

## API changes

New, all under the existing `/appointments/{id}/...` prefix:
`GET .../encounter`, `POST .../vitals`, `GET .../vitals/latest`,
`GET .../consultation` (creates a draft on first call), `PUT
.../consultation`, `POST .../consultation/complete`. All require staff
auth (ADMIN or STAFF) — see "Known gaps" below on why there's no
separate NURSE/DOCTOR role gating these differently.

## Tests

12 new (`tests/test_clinical.py`): encounter summary content, vitals
write-gated on CHECKED_IN, BMI computed correctly, latest-vitals
picks the most recent of several, consultation creation gated on
CHECKED_IN and idempotent (a second GET returns the same row), draft
save/overwrite, complete requires chief complaint + diagnosis (422),
complete locks further edits (409) while staying readable, and the
specific interaction this phase's write-gate design was built to handle:
vitals/consultation remain readable after the front-desk "Mark completed"
closes the visit, but a *new* vitals write after that point is correctly
refused.

Full suite: 441 passed (429 existing + 12 new), 1 skipped, 1 pre-existing
failure unrelated to this change (same one flagged in the Phase 3 report,
confirmed still isolated).

**Browser-verified, not just tested at the API layer**: ran both the
FastAPI backend and the Vite dev server locally, logged in as a seeded
ADMIN account, and drove the actual golden path in a real Chromium
browser — Appointments → Queue → "Consultation" on the now-serving
patient → recorded vitals (confirmed BMI computed) → switched to the
Consultation tab → saved a draft → completed it → confirmed the UI
shows "Completed" and the form is genuinely disabled (checked
programmatically, not just visually) → confirmed it's still viewable
and correctly blocked from further edits after re-opening.

## Known gaps / deliberately out of scope

- **No NURSE/DOCTOR login role.** The master spec's §6 wants distinct
  role-based work; today only ADMIN/STAFF exist (per the Phase 0 audit),
  and adding real doctor authentication (linking a `staff` account to a
  `doctors` row, a login flow, RBAC) is a meaningfully separate piece of
  work from clinical documentation itself. Staff record vitals/write
  consultations today the same way they already record payments and run
  the queue — a proxy model, not a gap introduced by this phase.
- **Follow-up is a recommendation, not a booking.** `consultations.
  follow_up_date`/`follow_up_reason` are stored, but nothing yet creates
  an actual follow-up appointment from them (master spec §21: "allow
  scheduling directly from the completed consultation"). The composition
  is straightforward — call the existing `create_appointment_service`
  with a doctor/slot picker inside `ConsultationWorkspace` — deliberately
  left as clearly-scoped follow-on work rather than bolted on here.
- **Diagnosis is free text.** No structured/coded diagnosis list yet
  (see "What changed" above).
- **No amendment workflow.** A completed consultation is frozen; there's
  no supervised "reopen and correct" path (master spec §70). Not needed
  until a real correction scenario shows up.

## Next phase

Phase 6 (Order Spine — lab/radiology/procedure orders, external
referral), the first consumer of `encounter_id` beyond consultations
themselves, and the natural next addition to the Consultation
workspace's "Plan" section.
