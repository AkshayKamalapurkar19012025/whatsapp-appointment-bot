# Workflow: Pharmacy

## 1. Purpose

Connect a doctor's prescription to inventory-aware dispensing, so pharmacy stock/batch/expiry data stays accurate and every dispense is traceable to the encounter that prescribed it.

## 2. Actors

Doctor (prescribes), Pharmacist (dispenses), Staff/Admin.

## 3. Entry points

Prescription: `ConsultationWorkspace.tsx`'s Prescription tab. Dispensing: `PharmacyPanel.tsx`, reached from the sidebar (module-gated).

## 4. Preconditions

`PHARMACY` module must be `Available` for dispensing (degradation: `BLOCKED` — new prescribing/dispensing blocked, existing records stay visible). Prescribing itself is not module-gated — a doctor can write a prescription even if the internal pharmacy module is off; only *dispensing through this system* is blocked (the patient takes the paper/printed prescription elsewhere).

## 5. Workflow

```
Prescription tab (during/after consultation)
      ↓
Add medicine: name, generic name, dosage, route, frequency, duration, quantity,
              food/special instructions
      ↓
Allergy check (see "Allergy Safety Check" below) — conflict?
 ┌────┴────┐
NO         YES
 │          │
 │      Warning shown → clinician reviews → Continue / Cancel
 │          │
 └────┬─────┘
      ↓
Item added to DRAFT prescription
      ↓
Prescribe (DRAFT → PRESCRIBED)
      ↓
Pharmacy Queue (PharmacyPanel.tsx) — cross-patient, filtered to pending prescriptions
      ↓
Dispense: check stock/batch/expiry, dispense (full or partial, per line item)
      ↓
pharmacy_dispense_records written (transaction log, not a bare flag)
```

## 6. UI pages

`ConsultationWorkspace.tsx` (Prescription tab) / `PrescriptionPanel.tsx`, `PharmacyPanel.tsx`.

## 7. Actions

Add/remove medicine line (allergy-checked, see below), Prescribe, Cancel prescription, Dispense (partial/full), record stock/batch.

## 8. State transitions

`prescriptions.status`: `DRAFT → PRESCRIBED → CANCELLED` (note: simpler vocabulary than a hypothetical `Draft→Prescribed→Sent to Pharmacy→Partially Dispensed→Dispensed→Cancelled` — "partially dispensed"/"dispensed" are tracked at the **item level** via `quantity_dispensed` vs `quantity`, not as a prescription-level status; functionally equivalent, the pharmacy queue correctly reflects partial-vs-full, just under different status vocabulary — do not "fix" this without a concrete reason).

## 9. Domain objects

`prescriptions`, `prescription_items`, `pharmacy_dispense_records` (real stock/batch/expiry transaction log).

## 10. API requirements

Prescription CRUD under `app/api/pharmacy.py`'s `prescription_router` (prefixed `/appointments`, since a prescription is scoped to a specific appointment/encounter); dispensing endpoints under `pharmacy_router` (prefixed `/pharmacy`).

## 11. Validation

Quantity dispensed cannot exceed quantity prescribed minus already-dispensed; stock availability is checked before a dispense is allowed to complete. Adding a medicine line is checked against the patient's active recorded allergies (P0 clinical safety, see "Allergy Safety Check" below) — non-blocking, but never silent.

## 12. Error handling

Insufficient stock returns a clear inline error rather than allowing an over-dispense; the error envelope follows the standard `{success, errorCode, message, details}` shape.

## 13. Permissions

Prescribe: `DOCTOR`, `STAFF`, `ADMIN`. Dispense / manage stock: `PHARMACIST`, `ADMIN` (`canManageStock`).

## 14. Audit requirements

`pharmacy_dispense_records` is itself a real transaction log (who dispensed what, when, from which batch) — this is a stronger audit trail than a generic `audit_logs` row for this specific action. The allergy check writes `prescription.allergy_warning_shown` / `.allergy_warning_overridden` / `.allergy_warning_cancelled` to `audit_log` (see "Allergy Safety Check" below) — the one place in this workflow that does use the generic audit table, since it's a decision event, not a transaction.

## 15. Concurrency considerations

Two pharmacists dispensing from the same batch simultaneously must not oversell stock — `FOR UPDATE` row locking is used across billing/dispense paths per `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §56-58 ("`FOR UPDATE` row locks used throughout billing... and pharmacy dispense" per this session's own concurrency test additions); `tests/test_concurrency_hardening.py` covers this.

## 16. Idempotency requirements

A retried dispense request must not double-decrement stock — covered by the same row-lock discipline as #15.

## 17. Tests

`tests/test_pharmacy.py`, `test_concurrency_hardening.py` (pharmacy dispense race coverage), `tests/test_allergy_check.py` (the allergy safety check specifically — 10 scenarios: no allergies, unrelated allergy, unrelated drug class, matching allergen, generic-name match, cancel, override, duplicate submission, resolved allergy, historical allergy from an earlier visit).

## 18. Exit conditions

Every prescribed item is either dispensed (fully/partially, with a real stock transaction) or the prescription is cancelled; visible in Patient 360.

## 19. Next workflow

`docs/workflows/BILLING.md` (dispensed medicine becomes a charge).

---

## Allergy Safety Check (P0 clinical safety)

**Status: Implemented** (per `docs/OPD_HIMS_STANDARDS_READINESS.md` §17's design, `docs/OPD_HIMS_INTEROPERABILITY_AUDIT.md`/`docs/OPD_HIMS_CANONICAL_MODEL_AUDIT.md`/`docs/OPD_HIMS_TERMINOLOGY_AUDIT.md`'s repeatedly-flagged gap). This section documents actual, verified behavior — not aspiration.

### What it does

When a medicine is added to a DRAFT prescription (`POST /appointments/{id}/prescription/items`), the backend checks the patient's active `patient_allergies` rows against the medicine's `medicine_name`/`generic_name` **before** inserting it. If the recorded allergen text appears as a case-insensitive substring of either field, the item is **not added**; the response carries the conflict(s) instead (`allergy_warning: { conflicts: [...] }`), and the frontend (`PrescriptionPanel.tsx`) shows a warning dialog naming the allergen, its severity, and which field matched. The clinician chooses:

- **Continue Anyway** — resubmits the same add-item request with `allergy_decision: "continue"`; the item is added, and `prescription.allergy_warning_overridden` is written to `audit_log` with the medicine name and the exact conflicts overridden.
- **Cancel Prescription** — resubmits with `allergy_decision: "cancel"`; the item is never added, and `prescription.allergy_warning_cancelled` is written to `audit_log`.

If there's no conflict at all, the item is added immediately with no interruption — existing behavior for the overwhelming majority of prescriptions is unchanged. A `prescription.allergy_warning_shown` audit row is written the moment a conflict is first detected, before the clinician has decided anything.

The check re-runs on every attempt, including the continue/cancel resubmission — it never trusts a cached result from the initial warning, so it stays authoritative up to the actual `INSERT`.

### Matching strategy and its limitation (stated explicitly, not glossed over)

**What is matched**: the recorded `allergen` text as a substring of the medicine's `medicine_name` or `generic_name`, case-insensitively. A patient allergic to "Penicillin" is flagged for "Penicillin", "Penicillin V", or any medicine whose name or generic name contains that text.

**What is not matched**: clinically related but differently-named medicines. A "Penicillin" allergy does **not** flag a prescription for "Amoxicillin" — they're in the same drug class, but nothing in this schema (confirmed across three prior audit phases: no SNOMED/RxNorm/drug-class terminology exists anywhere) encodes that relationship, and this implementation does not invent one.

**False-positive possibility**: a short or generically-worded allergen string could coincidentally appear inside an unrelated medicine's name.

**False-negative possibility**: any allergy/medicine pair that doesn't share literal text — brand-vs-generic naming the recorded allergen doesn't match, misspellings, or (as above) drug-class relationships — will not be caught. This is a real, meaningful limitation, not a hidden one.

**This is a medication-to-recorded-allergy conflict warning based on the currently supported matching rules — not drug-allergy decision support, and not a substitute for clinical judgment.**

### Future terminology enhancement

Once `patient_allergies.allergen` and `prescription_items.medicine_name` gain terminology code slots (`docs/OPD_HIMS_STANDARDS_READINESS.md` §5/§7 — proposed, not built), the same check can additionally match on `allergen_code`, which would close the drug-class gap once a real terminology (SNOMED CT substance codes, or a medication-terminology-aware mapping) is adopted — a decision this phase deliberately did not make.

### Evidence

`app/services/allergy_check_service.py`, `app/services/pharmacy_services.py`'s `add_prescription_item_service`, `app/api/pharmacy.py`'s `add_prescription_item` endpoint, `frontend/src/admin/PrescriptionPanel.tsx`'s warning dialog, `tests/test_allergy_check.py`.
