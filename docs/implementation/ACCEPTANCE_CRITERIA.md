# Acceptance Criteria

## The most important acceptance test

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

Every step must use the same `Patient`/`UHID`/`Encounter` where applicable. **There must be no manual re-entry of the patient into downstream modules.** See `docs/product/PATIENT_JOURNEY.md` for the full current-state table proving this is DB-enforced end to end, not just UI-convenient.

## Current State

Every individual step above has been exercised and browser-verified at least once, in the phase that built it (per `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §73-76 and this session's own live-testing discipline). **What has not happened**: running all of these steps in one continuous sitting, against one patient, as a single scripted acceptance run, with the result recorded as a pass/fail artifact. This is Phase 14's open item — see `docs/implementation/PHASES.md`.

## Target State — how this should be run

A single, scripted (ideally Playwright-automated, matching the pattern already used throughout this codebase's own live-verification passes) run that:

1. Registers a genuinely new patient (not a leftover from a previous run — test-data pollution across repeated runs has already caused one false-positive investigation in this codebase's history, per session notes; use fresh, uniquely-named test data every run).
2. Walks every step above in order, capturing the patient/UHID/encounter id at each step and asserting it's unchanged from the previous step (this is the concrete, checkable form of "no manual re-entry").
3. Ends at Patient 360 and asserts every event from every prior step is visible in the timeline.
4. Is re-run after any change to a table on the core spine (`encounters`, `orders`, `prescriptions`, `charges`, `invoices`) — not just after a change to the specific screen that looked affected, since the whole point of the connected model is that a change can have non-obvious downstream effects.

## Gap

No such scripted run exists yet as a standing artifact (a script that can be re-run, not just a one-time manual walkthrough). This is the concrete deliverable Phase 14 should produce.

## Recommended Implementation

Build this as a Playwright script under a `tests/` or `scripts/` location (not a pytest backend test — this needs to exercise the real frontend, matching this session's own live-verification pattern for UI changes), parameterized so it can run against a fresh seed each time. Do not treat individual pytest workflow tests (`test_appointment_lifecycle.py`, `test_encounters.py`, etc.) as a substitute — they're valuable and necessary (see `docs/implementation/TESTING_STRATEGY.md`) but each covers one workflow in isolation, not the continuous chain this acceptance test specifically checks.

## Configuration-scoped acceptance

Per `docs/implementation/TESTING_STRATEGY.md`'s configuration matrix, the core patient journey above must remain valid under at least these module configurations, run as separate passes:

- OPD only (all of `LAB_RADIOLOGY`/`PHARMACY`/`PACKAGES` disabled — Orders should route to `EXTERNAL_REFERRAL`, Prescription should still work but Pharmacy dispensing should be `BLOCKED`, Billing should still work without packages)
- OPD + Laboratory (`LAB_RADIOLOGY` enabled)
- OPD + Laboratory + Pharmacy
- Full configuration (all modules enabled)

This is not yet run as a matrix — `docs/implementation/TESTING_STRATEGY.md`'s "Configuration tests" section has the detail.
