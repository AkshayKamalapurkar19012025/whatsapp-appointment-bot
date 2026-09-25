# Patient Journey

## Purpose

Describe the single patient journey that every screen, table, and API in this system ultimately serves — the thing that must never be broken by a future change, no matter how small.

## The journey

```
Register/Search Patient
      ↓
Create OPD Encounter
      ↓
Appointment or Walk-in
      ↓
Check-in
      ↓
Token
      ↓
Queue
      ↓
Triage
      ↓
Doctor Consultation
      ↓
Diagnosis
      ↓
Order Lab
      ↓
Order Radiology
      ↓
Prescription
      ↓
Results
      ↓
Pharmacy
      ↓
Billing
      ↓
Payment
      ↓
Receipt
      ↓
Follow-up
      ↓
Encounter Completion
      ↓
Patient 360
```

Every step uses the same `Patient` / `UHID` / `Encounter` — there must be no manual re-entry of the patient into a downstream module. This is the system's single most important acceptance test; see `docs/implementation/ACCEPTANCE_CRITERIA.md` for how it's validated.

## Current State

This journey is real and DB-enforced end to end for OPD, confirmed by reading the actual foreign keys rather than assumed:

| Step | Table / mechanism | Encounter-linked? |
|---|---|---|
| Register/Search Patient | `patients` (UHID, duplicate detection) | n/a (identity root) |
| Create OPD Encounter | `encounters` | — |
| Appointment or Walk-in | `appointments` | `appointments.encounter_id` |
| Check-in / Token | `appointments.status`, queue token fields | via appointment → encounter |
| Queue | derived view over `appointments`/token, not a stored state (see `docs/workflows/OPD_CHECKIN_QUEUE.md`) | via appointment → encounter |
| Triage | `vitals` | `vitals.encounter_id` |
| Doctor Consultation / Diagnosis | `consultations` (+ `consultation_amendments`) | `consultations.encounter_id` |
| Order Lab / Radiology | `orders` (`order_type`) | `orders.encounter_id` |
| Results | `order_results` | via `orders.encounter_id` |
| Prescription | `prescriptions` / `prescription_items` | `prescriptions.encounter_id` |
| Pharmacy | `pharmacy_dispense_records` | via `prescription_items` → `prescriptions.encounter_id` |
| Billing | `charges`, `invoices` | `charges.encounter_id` / `invoices.encounter_id` |
| Payment | `payments` | via `invoices.encounter_id` |
| Receipt | derived/printed from `payments` + `invoices` | via `invoices.encounter_id` |
| Follow-up | `consultations.follow_up_date`/`follow_up_reason` | via `consultations.encounter_id` |
| Encounter Completion | `appointments.status = COMPLETED` (+ `VisitCompletionDialog`'s precondition summary) | the appointment/encounter itself |
| Patient 360 | `patient_timeline_service.py` — reads across all of the above by `patient_id` | reads across every encounter |

No step requires re-searching for or re-entering the patient — every screen in the chain either already has the patient/encounter in context (passed as a prop/route param) or reaches it via `ConsultationWorkspace`'s `appointmentId` → `encounter_id` lookup.

## Target State

Unchanged from Current State for OPD — this journey is the bar every future change (module toggle, new role, new screen) must continue to clear. The target adds two things not yet real:

1. **IPD continuity**: the same journey should be able to branch into an admission at "Diagnosis" and resume, on discharge, back into the same Patient 360 — not built yet (`docs/architecture/OPD_TO_IPD.md`).
2. **A literal Visit Completion checklist screen** surfaced at "Encounter Completion" showing which of the above steps are done — exists as a non-gating summary dialog (`VisitCompletionDialog.tsx`) today; see `docs/product/PRODUCT_VISION.md`'s "what changed since the audit" table for its current exact state.

## Gap

None for the OPD portion of the journey — it is real. The gap is entirely the not-yet-built IPD branch.
