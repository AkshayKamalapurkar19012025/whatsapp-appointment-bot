# Workflow: Radiology

## 1. Purpose

The Radiology-specific slice of the generic Order Spine (`docs/architecture/ORDER_SPINE.md`) — mirrors `docs/workflows/LABORATORY.md`; Radiology and Laboratory share one implementation today.

## 2. Actors

Lab Technician (the same `LAB_TECH` role/permission covers radiology result entry — there is no separate Radiology Technician/Radiologist role), Doctor (orders), Staff/Admin.

## 3. Entry points

`orders` with `order_type = 'RADIOLOGY'`, created from `ConsultationWorkspace`'s Orders tab; results recorded from `LabRadiologyWorklistPanel.tsx`.

## 4. Preconditions

`LAB_RADIOLOGY` module must be `Available`; otherwise the order becomes `EXTERNAL_REFERRAL`.

## 5. Workflow

Identical to `docs/workflows/ORDERS.md` — `order_type = 'RADIOLOGY'` is the only differentiator from Laboratory.

## 6. UI pages

`LabRadiologyWorklistPanel.tsx` (shared with Laboratory).

## 7. Actions

Same generic order/result actions.

## 8. State transitions

`ORDERED → IN_PROGRESS → COMPLETED`/`CANCELLED` — **not** the richer target pipeline (Ordered → Scheduled → In Progress → Reporting → Reported → Verified) a real radiology department would expect. No Findings/Impression/Technique structure — results use the same generic parameter/value table Laboratory uses, which fits a numeric lab value far better than a radiology report's narrative structure.

## 9. Domain objects

`orders` (`order_type = 'RADIOLOGY'`), `order_results` (generic — see Gap below for why this is a worse fit here than for Laboratory).

## 10-16. API / Validation / Error handling / Permissions / Audit / Concurrency / Idempotency

Identical to `docs/workflows/ORDERS.md`.

## 17. Tests

Covered within `tests/test_orders.py`/`test_order_results.py`, not a dedicated file.

## 18. Exit conditions

Same as Orders.

## 19. Next workflow

`docs/workflows/PATIENT_360.md`.

---

## Target State

A real Radiology phase would add (additively, per `docs/architecture/ORDER_SPINE.md`):

- A distinct scheduling step (Ordered → Scheduled, since imaging studies are typically booked, not walked-in-and-processed the way a blood draw is).
- Structured Findings/Impression/Technique fields, likely as a `TEXT`/JSON detail column on `order_results` specific to `order_type = 'RADIOLOGY'` rather than trying to force a narrative report into the existing parameter/value/unit shape.
- Image/attachment support (not modeled anywhere in the current schema) — a genuinely new capability, not an extension of an existing table; needs its own storage/access-control design.

## Gap

The generic `order_results` shape is a materially worse fit for Radiology than for Laboratory: a lab result is naturally parameter/value/unit/reference-range; a radiology report is naturally narrative text plus structured impression, which today gets crammed into the same generic fields (likely as free text in a single "value" — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` for exactly how `LabRadiologyWorklistPanel.tsx`'s result-entry form currently handles a RADIOLOGY order in practice before assuming this is unusable; it may be adequate for now precisely because it's simple).

## Recommended Implementation

Scope as its own phase (`docs/implementation/PHASES.md` Phase 7, alongside Laboratory). Add a nullable structured-report column set to `order_results`, populated only for `order_type = 'RADIOLOGY'`; do not create a `radiology_results` table.
