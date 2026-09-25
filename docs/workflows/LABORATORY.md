# Workflow: Laboratory

## 1. Purpose

The Laboratory-specific slice of the generic Order Spine (`docs/architecture/ORDER_SPINE.md`) — this doc exists so a future Laboratory-specific phase has one place to scope against, even though today there is no separate Laboratory table or screen from Radiology.

## 2. Actors

Lab Technician, Doctor (orders), Staff/Admin.

## 3. Entry points

`orders` with `order_type = 'LAB'`, created from `ConsultationWorkspace`'s Orders tab; results recorded from `LabRadiologyWorklistPanel.tsx`.

## 4. Preconditions

`LAB_RADIOLOGY` module must be `Available` (Licensed AND Enabled) for an internal LAB order; otherwise the order becomes `EXTERNAL_REFERRAL` (see `docs/architecture/MODULE_ARCHITECTURE.md`).

## 5. Workflow

Same as `docs/workflows/ORDERS.md` — Laboratory does not currently have a distinct workflow from Radiology. `order_type = 'LAB'` is the only differentiator.

## 6. UI pages

`LabRadiologyWorklistPanel.tsx` — filterable by type (LAB/RADIOLOGY) and status, one combined screen.

## 7. Actions

Same generic order/result actions as `docs/workflows/ORDERS.md`.

## 8. State transitions

`ORDERED → IN_PROGRESS → COMPLETED`/`CANCELLED` — **not** the richer target pipeline (Pending Collection → Collected → Processing → Result Pending → Verified → Released) a real laboratory department would expect. No sample-collection tracking exists.

## 9. Domain objects

`orders` (`order_type = 'LAB'`), `order_results` (generic parameter/value/unit/reference-range/abnormal/critical — same table Radiology uses).

## 10. API requirements

Shared with Radiology — see `docs/workflows/ORDERS.md`.

## 11-16. Validation / Error handling / Permissions / Audit / Concurrency / Idempotency

Identical to `docs/workflows/ORDERS.md` — Laboratory has no additional rules today.

## 17. Tests

`tests/test_orders.py`, `test_order_results.py` (cover LAB alongside every other `order_type`, not in a dedicated `test_laboratory.py`).

## 18. Exit conditions

Same as Orders.

## 19. Next workflow

`docs/workflows/PATIENT_360.md` (result visible in timeline).

---

## Target State

A real Laboratory phase would add, additively to `orders`/`order_results` (never a new table — see `docs/architecture/ORDER_SPINE.md`'s explicit rule):

- Sample collection tracking (collected-by, collected-at, specimen type).
- A distinct verify-then-release step before a result is visible to the ordering doctor (separating "entered" from "clinically verified").
- A searchable lab-test catalog (name, reference ranges, default units) to replace free-text ordering — this would also need a decision on whether it's Laboratory-specific or the same catalog `docs/architecture/ORDER_SPINE.md`'s Target State names for all order types.

## Gap

Everything above. None of it is built. This is explicitly out of scope for the current phase — recorded here so a future Laboratory phase starts from this list instead of rediscovering it.

## Recommended Implementation

Scope as its own phase per `docs/implementation/PHASES.md`'s Phase 7 (Diagnostics). Add nullable columns to `orders`/`order_results` for collection/verification tracking; do not fork the table.
