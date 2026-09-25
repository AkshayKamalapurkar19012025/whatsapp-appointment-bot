# Order Spine

## Purpose

Define the one generic order/result model that Laboratory, Radiology, Procedures, and any future diagnostic/service workflow must extend — never fork into a per-department table.

```
Patient
   ↓
Encounter
   ↓
Order
   ↓
Result / Outcome
```

## Current State

`orders` (migration `0030_orders.sql`), one table for every order type, discriminated by `order_type`:

- `order_type`: `LAB` / `RADIOLOGY` / `PROCEDURE` / `SERVICE` / `EXTERNAL_REFERRAL`
- Fields common to all types: `encounter_id`, ordering doctor, priority, clinical indication, `status`, plus (for `EXTERNAL_REFERRAL`) a required destination.
- Lifecycle: `ORDERED → IN_PROGRESS → COMPLETED`, or `CANCELLED` — **one simplified pipeline for every order type**, not separate Lab/Radiology pipelines.

`order_results` — one generic parameter/value/unit/reference-range/`is_abnormal`/`is_critical` table, used for every order type's result. `LAB_TECH`'s `order.result` permission (migration `0051`) and the Lab/Radiology Worklist screen (`LabRadiologyWorklistPanel.tsx`) both operate on this same generic model — there is no separate `lab_results` or `radiology_results` table.

External referral (`order_type = 'EXTERNAL_REFERRAL'`) is the concrete, already-working instance of module degradation described in `docs/architecture/MODULE_ARCHITECTURE.md`: a doctor can always order a LAB/RADIOLOGY test as an external referral regardless of whether the internal `LAB_RADIOLOGY` module is licensed/enabled, because external referral was built as a first-class `order_type` from the start, not gated behind an internal module flag.

No searchable test/service catalog exists — order descriptions are staff-typed free text (explicit, repeated, documented decision — "no price catalog exists yet").

## Target State

The spine itself (`Encounter → Order → Result`) is already the target shape and should not change structurally. What the target state adds, without forking the table:

- Type-specific *workflow* granularity where it matters clinically (e.g. Lab: collection → processing → verify → release; Radiology: scheduled → in-progress → reported → verified) — modeled as **additional status values or a sub-status field on the existing `orders`/`order_results` tables**, not new tables per type.
- Radiology-specific structured fields (Findings/Impression/Technique) — modeled as **optional, type-specific columns or a JSON detail column on `order_results`**, populated only when `order_type = 'RADIOLOGY'`, not a parallel `radiology_results` table.
- A searchable test/service catalog, if/when pricing or catalog-driven ordering becomes a requirement — a new `service_catalog`-style table that `orders` optionally references, not a replacement for free-text `description`.

## Gap

The generic pipeline satisfies the spec's functional exit criterion ("a doctor creates an order and later sees a result from the same encounter") but not the richer, type-specific granularity a dedicated Lab or Radiology department would expect (sample collection tracking, a distinct verify-then-release step, structured radiology reporting).

## Recommended Implementation

Extend `orders.status`/`order_results` additively (new allowed status values, new nullable columns) when a Laboratory or Radiology phase is actually scoped — see `docs/workflows/LABORATORY.md` and `docs/workflows/RADIOLOGY.md` for what each would need. Do not create `lab_orders`/`radiology_orders` or `lab_results`/`radiology_results` tables; doing so would re-fragment exactly what migration `0030` deliberately unified, and would break the Lab/Radiology Worklist screen's current single-query-across-types implementation.
