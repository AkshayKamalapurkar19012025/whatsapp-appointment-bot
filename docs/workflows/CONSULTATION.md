# Workflow: Consultation

## 1. Purpose

Record triage/vitals and the doctor's clinical assessment for one encounter, producing the diagnosis that drives Orders/Prescription/Follow-up.

## 2. Actors

Nurse (vitals), Doctor (consultation, diagnosis, amendment), Staff (both, as the unrestricted fallback role), Admin.

## 3. Entry points

Opened from Queue ("View consultation") or Appointments, landing in `ConsultationWorkspace.tsx` for a specific `appointmentId`.

## 4. Preconditions

The appointment must be checked in (a `notCheckedIn` state is explicitly handled and shown as a navigable, expected state — not an error — when a doctor clicks into a still-Waiting patient's row).

## 5. Workflow

```
ConsultationWorkspace opens (Triage tab default)
      ↓
Triage / Vitals (BP, pulse, temp, SpO2, resp rate, weight, height, BMI computed,
                 pain score, chief complaint, priority, nursing notes)
      ↓
Consultation tab: history, examination, diagnosis, clinical notes, follow-up
      ↓
Save Draft (repeatable) → Complete Consultation (locks the form)
      ↓
(locked) Amendment requires a reason + RBAC, archives the prior version
```

## 6. UI pages

`ConsultationWorkspace.tsx` (Triage/Consultation tabs).

## 7. Actions

Save Vitals, Save Draft, Complete Consultation, Amend (post-completion, reason required).

## 8. State transitions

Consultation draft → `COMPLETED` (locked) → (amendment) still `COMPLETED`, with a `consultation_amendments` history row recording the change, not an overwrite and not a reopen.

## 9. Domain objects

`vitals`, `consultations`, `consultation_amendments`.

### Diagnosis coding (OPD/HIMS interoperability master prompt Phase 6)

**Status: Implemented** (`migrations/0056_consultation_diagnosis_coding.sql`), per `docs/OPD_HIMS_STANDARDS_READINESS.md` §6's design. `consultations.diagnosis` (free text, required to complete a consultation — see §11 below) stays exactly as it was; three new, purely optional columns sit alongside it:

- `diagnosis_code_system` — free text naming the terminology a code came from (e.g. "SNOMED CT", "ICD-10"). Not an enum — no terminology system has been chosen for this codebase, and this phase doesn't choose one.
- `diagnosis_code` — the structured code itself, in `diagnosis_code_system`'s terminology.
- `diagnosis_code_display` — the terminology's own display string for that code (can differ from what the clinician typed in `diagnosis`).

The only structural rule enforced (API-level 422 and a DB `CHECK`, both): `diagnosis_code` requires `diagnosis_code_system` to also be set. Nothing else is constrained — `diagnosis_code_system` alone (no code yet), or none of the three at all, are both valid. **No code is ever assigned automatically** — every consultation's coded-diagnosis fields are `NULL` unless a caller explicitly supplies real values through the API; nothing in this codebase invents or looks up a SNOMED/ICD code from the free-text diagnosis.

**Deliberately backend-only this phase, not exposed in `ConsultationWorkspace.tsx`'s UI.** There is no terminology catalog/search anywhere in this repo for diagnoses (unlike medications — see `docs/workflows/PHARMACY.md`'s "Medication Master", which has a real search UI backed by an actual master-data table) — a raw three-field text UI would just invite clinicians to freehand a "code" with no way to verify it means anything, which contradicts this phase's own "do not invent codes" rule in spirit even if a human types it rather than the system. No repository evidence (no existing UI, workflow, or report) currently requires clinician-facing code entry, so the fields exist and are fully exercised at the API/DB layer (`tests/test_clinical.py`) but aren't surfaced yet. Exposing them is future work once there's a real terminology source to pick from — the same reasoning `docs/OPD_HIMS_STANDARDS_READINESS.md` §5/§16 already applied to medications' own (still not built) terminology slot.

**Amendment**: `diagnosis_code_system`/`diagnosis_code`/`diagnosis_code_display` follow the same archive-then-update pattern as every other consultation field (`consultation_amendments.previous_diagnosis_code_system`/`.previous_diagnosis_code`/`.previous_diagnosis_code_display`) — amending a coded diagnosis is captured in the same before/after audit trail as everything else, not silently dropped.

**Multiple diagnoses**: not built. Phase 3's original decision — one diagnosis field per consultation, no `consultation_diagnoses`/primary-secondary model — was re-verified against the current repository in Phase 6 and found still accurate: no existing workflow, UI, report, or table anywhere assumes more than one diagnosis per consultation. Not introduced without concrete evidence of a real requirement.

**Future FHIR/terminology readiness**: conceptually, `diagnosis` + `diagnosis_code_system` + `diagnosis_code` + `diagnosis_code_display` map to a future `Condition.code.text` + `Condition.code.coding[].system` + `.code` + `.display` — but no FHIR resource, endpoint, or library exists in this phase, and none is implied by these columns existing.

## 10. API requirements

Vitals CRUD and consultation CRUD/amend endpoints under `app/api/clinical.py`, scoped by `encounter_id`.

## 11. Validation

Chief complaint and diagnosis are the fields treated as required for a real consultation (exact required-field list: `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` against `app/api/clinical.py`'s Pydantic models before building anything that depends on the precise list). `diagnosis_code` requires `diagnosis_code_system` (see "Diagnosis coding" under §9) — the only validation on the Phase 6 coding fields; nothing else about them is required or cross-checked. **Known gap**: no allergy or current-medication field/table integration into this screen — `patient_allergies` exists (see `docs/workflows/PATIENT_REGISTRATION.md`) but is not yet surfaced as a clinical-safety warning inside the consultation UI itself (it is surfaced at prescribing time — see `docs/workflows/PHARMACY.md`'s "Allergy Safety Check", a different screen).

## 12. Error handling

Amendment without a reason is rejected; amending a non-`COMPLETED` consultation is rejected (there's nothing to amend yet — it's still a draft, which Save Draft already covers).

## 13. Permissions

Record vitals: `NURSE`, `DOCTOR`, `STAFF`, `ADMIN` (`canRecordVitals` in `AdminApp.tsx`). Write consultation: `DOCTOR`, `STAFF`, `ADMIN` (`canWriteConsultation`). Amend: `DOCTOR`, `ADMIN` only (`canAmendConsultation`) — a real, server-enforced boundary (`consultation.amend` permission), not just a frontend check.

## 14. Audit requirements

`consultation_amendments` is itself a full before/after audit trail for amendments — arguably stronger than a generic `audit_logs` row would be, since it captures the complete prior state, not just "something changed."

## 15. Concurrency considerations

`TODO — VERIFY AGAINST EXISTING IMPLEMENTATION`: whether two staff sessions editing the same draft consultation simultaneously (e.g. a nurse saving vitals while a doctor saves consultation notes) can race on the same row — these appear to be different tables (`vitals` vs `consultations`) so likely don't conflict, but confirm before assuming.

## 16. Idempotency requirements

Save Draft is safe to call repeatedly (idempotent upsert per encounter, not an append-only log of drafts) — confirmed by the component's own behavior (reopening a draft consultation loads the existing row, not a fresh blank one).

## 17. Tests

`tests/test_clinical.py` (includes the Phase 6 diagnosis-coding scenarios: no code, full code triple, code without system rejected, system without code allowed, clearing a previously-set code on a later save), `test_encounters.py`, `test_consultation_amendments.py` (includes the Phase 6 archive-on-amend coverage for the coding fields), `test_consultation_payments.py`.

## 18. Exit conditions

A `COMPLETED` consultation with a diagnosis exists, ready to drive Orders/Prescription.

## 19. Next workflow

`docs/workflows/ORDERS.md` (and, in parallel, prescription — see `docs/workflows/PHARMACY.md`).
