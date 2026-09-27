# Workflow: Radiology

## 1. Purpose

The Radiology-specific slice of the generic Order Spine (`docs/architecture/ORDER_SPINE.md`). As of migrations/0054_diagnostic_workflow.sql (Phase 7, resumed), Radiology shares the same verify/release lifecycle infrastructure as Laboratory (`docs/workflows/LABORATORY.md`) but has no sample-collection step — its own point of divergence is that "study performed" (IN_PROGRESS) is reachable directly from ORDERED, with no COLLECTED stage in between.

## 2. Actors

Lab Technician (the same `LAB_TECH` role/permission covers radiology result entry — there is still no separate Radiology Technician/Radiologist role in this app), Doctor (orders; a `DOCTOR`-role account is also how a "radiologist" verifies/releases in this app today — see Gap), Staff/Admin.

## 3. Entry points

`orders` with `order_type = 'RADIOLOGY'`, created from `ConsultationWorkspace`'s Orders tab. Marking the study performed, drafting the report, verifying, and releasing all happen from `LabRadiologyWorklistPanel.tsx` (result entry only is also reachable from `ConsultationWorkspace`'s own Orders tab, same as Laboratory).

## 4. Preconditions

`LAB_RADIOLOGY` module must be `Available`; otherwise the order becomes `EXTERNAL_REFERRAL`.

## 5. Workflow

```
Doctor places RADIOLOGY order (ORDERED)
       ↓
Technician marks the study performed (ORDERED -> IN_PROGRESS)   -- optional, no sample step
       ↓
Technician drafts the report as three order_results rows:
   Technique / Findings / Impression                            (-> RESULT_ENTERED)
       ↓
A DIFFERENT staff account (typically a DOCTOR-role "radiologist") verifies (-> VERIFIED)
       ↓
Verifier releases (-> COMPLETED)                                 -- fires LAB_RESULT_AVAILABLE
       ↓
Doctor sees the released report (GET .../orders)
```

## 6. UI pages

`LabRadiologyWorklistPanel.tsx` (shared with Laboratory) — for a RADIOLOGY row, offers "Mark performed" instead of "Collect sample" (no sample form is ever shown for this order_type), then the same Record result / Verify / Release actions.

## 7. Actions

`POST .../orders/{id}/start-processing` ("study performed" — ORDERED or COLLECTED → IN_PROGRESS; COLLECTED is unreachable for RADIOLOGY in practice since nothing sets it for this order_type, but the transition allows it for symmetry with the shared `mark_order_in_progress_service`), `POST .../orders/{id}/result`, `POST .../orders/{id}/verify`, `POST .../orders/{id}/release`. `collect-sample`/`samples/{id}/reject` reject a RADIOLOGY order_id with 422 (`DiagnosticActionNotSupportedForOrderType`) — there is no sample concept here.

## 8. State transitions

`ORDERED → IN_PROGRESS → RESULT_ENTERED → VERIFIED → COMPLETED`, or `CANCELLED` from any non-terminal state — the same shared status vocabulary Laboratory uses (master spec Principle 3: one generic model, discriminated by `order_type`, not a second parallel state machine). "Study performed" tracking (IN_PROGRESS) is optional, not hard-required before result entry — a report can be drafted straight from ORDERED, same documented stance as Laboratory's own collection-optional decision.

## 9. Domain objects

`orders` (`order_type = 'RADIOLOGY'`) — unchanged shape from Laboratory's, minus any `lab_samples` rows (a RADIOLOGY order never has any). `order_results` — the same generic parameter/value table, used for a radiology report's narrative sections: `parameter = 'Technique'` / `'Findings'` / `'Impression'`, `result_value` holding the free-text content of each, `unit`/`reference_range` left null (exactly the shape migrations/0031's own header anticipated for this case). **No new `radiology_reports` table was created** — Technique/Findings/Impression are three ordinary `order_results` rows, not a structured JSON/column set, consistent with master spec Principle 3 and this schema's existing convention.

## 10-16. API / Validation / Error handling / Permissions / Audit / Concurrency / Idempotency

Identical to `docs/workflows/LABORATORY.md` sections 10-16 — genuinely shared infrastructure, not duplicated. The one Radiology-specific RBAC note: verification is enforced as "a different staff account than the one that entered the report," not "a DOCTOR specifically" — in `tests/test_radiology_workflow.py`'s own demonstration, a `DOCTOR`-role account plays the radiologist, but nothing in the permission grants requires that role specifically (`LAB_TECH` could equally verify another `LAB_TECH`'s draft).

## 17. Tests

`tests/test_radiology_workflow.py` (6 tests: full lifecycle including the Technique/Findings/Impression report shape, no-sample-collection confirmation, skip-straight-to-result, cross-role verification, RBAC denial, EXTERNAL_REFERRAL non-affected regression check) — a dedicated file, unlike before this phase.

## 18. Exit conditions

A released (COMPLETED) RADIOLOGY order's report is visible to the doctor via `GET /appointments/{id}/orders`, with its three narrative sections in `results`, in the order they were entered (`sequence`).

## 19. Next workflow

`docs/workflows/PATIENT_360.md`.

---

## Target State

Reached, additively, this phase:

- ✅ A distinct "study performed" step (ORDERED → IN_PROGRESS) — not a full scheduling sub-system (no separate SCHEDULED state, no booking/slot concept for imaging equipment), but the "has this study actually happened yet" signal the target state asked for.
- ✅ Structured Findings/Impression/Technique — as three `order_results` rows (see section 9), not a bespoke JSON/column set. This is a **narrower interpretation than a prior draft of this doc suggested** ("likely as a TEXT/JSON detail column... specific to order_type = 'RADIOLOGY'") — the generic table turned out to fit fine once parameter labels carry the section name, so no schema fork was needed after all.
- ❌ Image/attachment support (PACS/DICOM/RIS) — still not built, still out of scope per the audit's explicit instruction not to implement these without an existing extension point. None exists in this schema today; `order_results.result_value` remains text-only.

## Gap

Image/attachment storage and any PACS/DICOM/RIS integration remain entirely unbuilt — this phase deliberately did not touch that area (out of scope, per the task that resumed Phase 7). A future phase would need its own storage/access-control design; nothing here should be read as having laid groundwork for it.

## Recommended Implementation

Radiology's remaining, real gap (PACS/DICOM/image storage) is a substantially larger, separate piece of work — not a natural next increment of this phase. A shared lab-test/study catalog (see `docs/workflows/LABORATORY.md`'s own Gap) is the smaller, next scoped increment if one is wanted.
