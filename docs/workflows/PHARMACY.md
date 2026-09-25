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

Add/remove medicine line, Prescribe, Cancel prescription, Dispense (partial/full), record stock/batch.

## 8. State transitions

`prescriptions.status`: `DRAFT → PRESCRIBED → CANCELLED` (note: simpler vocabulary than a hypothetical `Draft→Prescribed→Sent to Pharmacy→Partially Dispensed→Dispensed→Cancelled` — "partially dispensed"/"dispensed" are tracked at the **item level** via `quantity_dispensed` vs `quantity`, not as a prescription-level status; functionally equivalent, the pharmacy queue correctly reflects partial-vs-full, just under different status vocabulary — do not "fix" this without a concrete reason).

## 9. Domain objects

`prescriptions`, `prescription_items`, `pharmacy_dispense_records` (real stock/batch/expiry transaction log).

## 10. API requirements

Prescription CRUD under `app/api/pharmacy.py`'s `prescription_router` (prefixed `/appointments`, since a prescription is scoped to a specific appointment/encounter); dispensing endpoints under `pharmacy_router` (prefixed `/pharmacy`).

## 11. Validation

Quantity dispensed cannot exceed quantity prescribed minus already-dispensed; stock availability is checked before a dispense is allowed to complete.

## 12. Error handling

Insufficient stock returns a clear inline error rather than allowing an over-dispense; the error envelope follows the standard `{success, errorCode, message, details}` shape.

## 13. Permissions

Prescribe: `DOCTOR`, `STAFF`, `ADMIN`. Dispense / manage stock: `PHARMACIST`, `ADMIN` (`canManageStock`).

## 14. Audit requirements

`pharmacy_dispense_records` is itself a real transaction log (who dispensed what, when, from which batch) — this is a stronger audit trail than a generic `audit_logs` row for this specific action.

## 15. Concurrency considerations

Two pharmacists dispensing from the same batch simultaneously must not oversell stock — `FOR UPDATE` row locking is used across billing/dispense paths per `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §56-58 ("`FOR UPDATE` row locks used throughout billing... and pharmacy dispense" per this session's own concurrency test additions); `tests/test_concurrency_hardening.py` covers this.

## 16. Idempotency requirements

A retried dispense request must not double-decrement stock — covered by the same row-lock discipline as #15.

## 17. Tests

`tests/test_pharmacy.py`, `test_concurrency_hardening.py` (pharmacy dispense race coverage).

## 18. Exit conditions

Every prescribed item is either dispensed (fully/partially, with a real stock transaction) or the prescription is cancelled; visible in Patient 360.

## 19. Next workflow

`docs/workflows/BILLING.md` (dispensed medicine becomes a charge).
