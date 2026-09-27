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
- Lifecycle for PROCEDURE/SERVICE/EXTERNAL_REFERRAL, unchanged since migration `0030`: `ORDERED → IN_PROGRESS → COMPLETED`, or `CANCELLED` — one simplified, one-step-to-complete pipeline.
- Lifecycle for LAB/RADIOLOGY, resumed by migration `0054_diagnostic_workflow.sql` (Phase 7): `ORDERED → COLLECTED (LAB only) → IN_PROGRESS → RESULT_ENTERED → VERIFIED → COMPLETED (released)`, or `CANCELLED` from any non-terminal state. Still **one shared status vocabulary and one `orders` table** for every order type (a DB CHECK ties the three new values to `order_type IN ('LAB','RADIOLOGY')`) — not separate Lab/Radiology pipelines as distinct tables or enums, just a wider, type-conditional range of the same `status` column.

`order_results` — one generic parameter/value/unit/reference-range/`is_abnormal`/`is_critical` table, used for every order type's result, including a Radiology report's Technique/Findings/Impression narrative sections (three rows, `parameter` carrying the section name — see `docs/workflows/RADIOLOGY.md`). `LAB_TECH`'s `order.result`/`order.collect`/`order.verify`/`order.release` permissions (migrations `0051`/`0054`) and the Lab/Radiology Worklist screen (`LabRadiologyWorklistPanel.tsx`) all operate on this same generic model — there is no separate `lab_results` or `radiology_results` table. A new `lab_samples` table (migration `0054`) holds the one genuinely new entity this phase needed — a specimen is not modelable as an order-level column, since one order can have several collection attempts (reject → recollect) over time.

External referral (`order_type = 'EXTERNAL_REFERRAL'`) is the concrete, already-working instance of module degradation described in `docs/architecture/MODULE_ARCHITECTURE.md`: a doctor can always order a LAB/RADIOLOGY test as an external referral regardless of whether the internal `LAB_RADIOLOGY` module is licensed/enabled, because external referral was built as a first-class `order_type` from the start, not gated behind an internal module flag.

No searchable test/service catalog exists — order descriptions are staff-typed free text (explicit, repeated, documented decision — "no price catalog exists yet").

## Target State

The spine itself (`Encounter → Order → Result`) is already the target shape and should not change structurally. Reached this phase, additively:

- ✅ Type-specific *workflow* granularity where it matters clinically (Lab: collection → processing → verify → release; Radiology: performed → reported → verified → released) — modeled as additional status values on the existing `orders` table (`COLLECTED`/`RESULT_ENTERED`/`VERIFIED`, CHECK-constrained to LAB/RADIOLOGY) plus one new child table (`lab_samples`) for the one entity that genuinely needed its own rows.
- ✅ Radiology-specific structured fields (Findings/Impression/Technique) — modeled as three ordinary `order_results` rows (`parameter` = the section name), **not** a JSON/column-set addition as an earlier draft of this doc anticipated; the existing generic shape turned out to fit once a report is expressed as several labeled narrative rows instead of one blob.

Still not built:

- A searchable test/service catalog, if/when pricing or catalog-driven ordering becomes a requirement — a new `service_catalog`-style table that `orders` optionally references, not a replacement for free-text `description`.
- Image/attachment storage or any PACS/DICOM/RIS integration for Radiology — genuinely new capability, explicitly out of scope for this phase (see `docs/workflows/RADIOLOGY.md`'s own Gap).

## Gap

What's left, in order of how much it would matter to a real lab/radiology department: a searchable test/study catalog (LAB and RADIOLOGY both still take free-text `description`), then image/attachment support for Radiology (a substantially larger, separate piece of work, not a natural next increment).

## Recommended Implementation

The remaining catalog work: extend `orders`/`order_results` additively (a new `order_id`-optional FK to a future catalog table) when pricing/catalog-driven ordering is actually scoped. Do not create `lab_orders`/`radiology_orders` or `lab_results`/`radiology_results` tables; doing so would re-fragment exactly what migration `0030` deliberately unified, and would break the Lab/Radiology Worklist screen's single-query-across-types implementation, which migration `0054` preserved rather than forked.
