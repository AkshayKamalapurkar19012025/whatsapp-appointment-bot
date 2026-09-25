# Workflow: Patient 360

## 1. Purpose

Give any authorized user a single cross-domain view of everything that's happened to a patient — every encounter's vitals/consultations/orders/results/prescriptions/billing — as the terminal node every other workflow in this system feeds into.

## 2. Actors

All roles (read access; scope may vary — a `LAB_TECH` likely shouldn't see billing detail, `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` for whether `patient_timeline_service.py` filters by the viewer's role or returns everything to anyone who can reach the endpoint).

## 3. Entry points

Patients list ("Timeline" action), `GET /api/patients/{id}/timeline`. **Not** currently reachable inline from inside an active consultation — see Gap.

## 4. Preconditions

Patient exists (a patient with zero encounters shows an appropriately empty timeline, not an error).

## 5. Workflow

```
Patients list → select patient → View Timeline
      ↓
PatientTimelineModal renders every event across every encounter,
chronologically: vitals, consultation, orders, results, prescription,
dispensing, billing
```

## 6. UI pages

`PatientTimelineModal.tsx`.

## 7. Actions

View (read-only) — no mutation happens from this screen.

## 8. State transitions

None — this is a read view over other workflows' state.

## 9. Domain objects

Reads across `encounters`, `vitals`, `consultations`, `orders`, `order_results`, `prescriptions`, `pharmacy_dispense_records`, `charges`, `invoices`, `payments` — all joined by `patient_id`/`encounter_id`, confirming the "connected, not disconnected" claim in `docs/product/PATIENT_JOURNEY.md` is real at the read layer too, not just the write layer.

## 10. API requirements

`GET /api/patients/{id}/timeline` (`app/services/patient_timeline_service.py`).

## 11. Validation

None (read-only).

## 12. Error handling

A patient with no history shows an explanatory empty state, not an error — consistent with the rest of the app's empty-state pattern (`docs/ux/UX_PRINCIPLES.md`).

## 13. Permissions

Read access — exact per-role scoping is a named `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` above.

## 14. Audit requirements

Viewing a patient's full history is itself a PHI-access event worth logging in a strict compliance posture — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` whether timeline *views* (as opposed to mutations) are audit-logged at all; likely not, consistent with most systems not logging reads, but worth a deliberate decision rather than an assumption when this becomes a compliance requirement.

## 15. Concurrency considerations

None — read-only aggregation.

## 16. Idempotency requirements

N/A — GET is naturally idempotent.

## 17. Tests

`tests/test_patient_timeline.py`.

## 18. Exit conditions

N/A — this is a terminal/informational workflow, not one with an exit state.

## 19. Next workflow

None — this is the destination. A historical module-disabled event must still appear here even if the module is currently unavailable (per `docs/architecture/MODULE_ARCHITECTURE.md`'s "never hide historical clinical data" rule) — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` that this is actually true in the current implementation (it should be, since disabling a module only flips `hospital_modules.enabled` and never touches the clinical rows themselves, but this specific screen hasn't been explicitly tested against a disabled-module scenario as far as this audit found).

---

## Target State / Gap

Target: Patient 360 should be viewable **inline, side-by-side, during an active consultation** ("without leaving the workflow," per the master spec). Current: it's a separate modal reached from Patients, requiring a doctor to leave the Consultation tab. See `docs/ux/UX_PRINCIPLES.md` for the same gap noted from the UX angle.

## Recommended Implementation

Add a collapsible timeline panel inside `ConsultationWorkspace.tsx`, reusing `patient_timeline_service.py`/`PatientTimelineModal.tsx`'s data-fetching logic rather than duplicating it. Natural to pair with the `Timeline` shared-component extraction named in `docs/ux/DESIGN_SYSTEM.md`.
