# Workflow: Orders

## 1. Purpose

Let a doctor order a lab test, radiology study, procedure, service, or external referral against the current encounter, and track it to a result — the generic spine `docs/architecture/ORDER_SPINE.md` describes in detail.

## 2. Actors

Doctor (creates orders), Lab Technician (records LAB/RADIOLOGY results), Staff/Admin.

## 3. Entry points

`ConsultationWorkspace.tsx`'s Orders tab, during or after a consultation.

## 4. Preconditions

An open encounter exists (the consultation need not be `COMPLETED` first — orders can be created alongside an in-progress consultation).

## 5. Workflow

```
Orders tab
      ↓
Create order: type (LAB/RADIOLOGY/PROCEDURE/SERVICE/EXTERNAL_REFERRAL),
              priority, clinical indication, description (free text)
      ↓
LAB_RADIOLOGY module available?
 ┌────┴────┐
YES        NO
 │          │
Internal    Forced to EXTERNAL_REFERRAL
order       (degradation: EXTERNAL — see MODULE_ARCHITECTURE.md)
 │          │
 └────┬─────┘
      ↓
ORDERED → IN_PROGRESS → COMPLETED (or CANCELLED)
      ↓
Result recorded (order_results) — via Lab/Radiology Worklist for LAB/RADIOLOGY,
                                    inline for PROCEDURE/SERVICE
```

## 6. UI pages

`ConsultationWorkspace.tsx` (Orders tab, create + view), `LabRadiologyWorklistPanel.tsx` (cross-patient worklist + result entry, LAB/RADIOLOGY only).

## 7. Actions

Create order, Cancel order, Record result, Mark critical/abnormal.

## 8. State transitions

`orders.status`: `ORDERED → IN_PROGRESS → COMPLETED`, or `CANCELLED` — one pipeline for every `order_type` (see Gap in `docs/architecture/ORDER_SPINE.md` for why this is simpler than a real hospital's lab/radiology pipelines).

## 9. Domain objects

`orders`, `order_results`.

## 10. API requirements

Order CRUD and result-recording endpoints under `app/api/orders.py`, scoped by `encounter_id`; the worklist reads across encounters filtered by `order_type`/`status`.

## 11. Validation

`EXTERNAL_REFERRAL` requires a destination. No test/service catalog validation — description is free text (deliberate, see `docs/architecture/ORDER_SPINE.md`).

## 12. Error handling

Attempting to create an internal LAB/RADIOLOGY order when the module is unavailable should route to `EXTERNAL_REFERRAL` per the degradation contract — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` for the exact frontend UX of this fallback (does the UI silently offer only External Referral, or show an explanatory message first?) before assuming either.

## 13. Permissions

Create order: `DOCTOR`, `STAFF`, `ADMIN` (`canCreateOrders`). Record result: `DOCTOR`, `STAFF`, `LAB_TECH`, `ADMIN` (`canRecordOrderResults`, gated by the `order.result` permission from migration `0051`).

## 14. Audit requirements

Order cancellation and critical-result flagging are named in the master spec's audit-example list; current coverage: `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` against `audit_logs` usage in `app/api/orders.py` specifically.

## 15. Concurrency considerations

Two staff recording a result for the same order simultaneously — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` for row-level locking on `order_results` writes; not confirmed either way by this pass.

## 16. Idempotency requirements

Result recording should not create duplicate `order_results` rows for the same order on a retried request — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION`.

## 17. Tests

`tests/test_orders.py`, `test_order_results.py`.

## 18. Exit conditions

Every order created reaches `COMPLETED` or `CANCELLED` with a visible result (or an external-referral record), reflected in Patient 360.

## 19. Next workflow

`docs/workflows/LABORATORY.md` / `docs/workflows/RADIOLOGY.md` (result recording detail), and `docs/workflows/PHARMACY.md` (the prescription branch, created in parallel from the same consultation).
