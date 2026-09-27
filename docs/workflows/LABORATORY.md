# Workflow: Laboratory

## 1. Purpose

The Laboratory-specific slice of the generic Order Spine (`docs/architecture/ORDER_SPINE.md`). As of migrations/0054_diagnostic_workflow.sql (Phase 7, resumed), Laboratory has a real, distinct lifecycle from a plain PROCEDURE/SERVICE order and from Radiology's own collection-free path — see section 8.

## 2. Actors

Lab Technician (`order.collect`/`order.result`/`order.verify`/`order.release`), Doctor (orders; may also collect/result/verify/release — no distinct phlebotomist/pathologist role exists, see Gap), Staff/Admin (ADMIN may verify its own entered result — the one deliberate same-person exception, see section 13).

## 3. Entry points

`orders` with `order_type = 'LAB'`, created from `ConsultationWorkspace`'s Orders tab. Sample collection, processing, result entry, verification, and release all happen from `LabRadiologyWorklistPanel.tsx` (or, for result entry only, from `ConsultationWorkspace`'s own Orders tab).

## 4. Preconditions

`LAB_RADIOLOGY` module must be `Available` (Licensed AND Enabled) for an internal LAB order to be created; otherwise the order becomes `EXTERNAL_REFERRAL` (see `docs/architecture/MODULE_ARCHITECTURE.md`). Once created, no later diagnostic-lifecycle action (collect/process/result/verify/release) re-checks module availability — module gating is a creation-time decision, same as every other order type.

## 5. Workflow

```
Doctor places LAB order (ORDERED)
       ↓
Lab tech collects a sample (ORDERED -> COLLECTED)     -- optional, not hard-required
       ↓
Lab tech marks processing started (COLLECTED -> IN_PROGRESS)   -- optional
       ↓
Lab tech (or doctor) enters result (-> RESULT_ENTERED)
       ↓
A DIFFERENT staff account verifies (-> VERIFIED)       -- ADMIN may verify its own entry
       ↓
Verifier releases (-> COMPLETED)                        -- fires LAB_RESULT_AVAILABLE notification
       ↓
Doctor sees the released result (GET .../orders)
```

A sample can be rejected (wrong tube, hemolyzed, insufficient quantity) after collection, which reverts the order to ORDERED for recollection — the rejected `lab_samples` row is kept, never deleted, and a fresh row records the recollection.

## 6. UI pages

`LabRadiologyWorklistPanel.tsx` — filterable by type (LAB/RADIOLOGY) and status; shows the order's current stage, the latest sample (code/type/status) once collected, and the one legal next action (Collect sample / Reject sample / Start processing / Record result / Verify / Release) per row.

## 7. Actions

`POST .../orders/{id}/collect-sample`, `POST .../orders/{id}/samples/{sample_id}/reject`, `POST .../orders/{id}/start-processing`, `POST .../orders/{id}/result` (drafts, doesn't complete), `POST .../orders/{id}/verify`, `POST .../orders/{id}/release`. Cancellation (`POST .../orders/{id}/cancel`) remains available at any pre-COMPLETED stage, unchanged.

## 8. State transitions

`ORDERED → COLLECTED → IN_PROGRESS → RESULT_ENTERED → VERIFIED → COMPLETED` (released), or `CANCELLED` from any non-terminal state. COLLECTED/RESULT_ENTERED/VERIFIED are DB-CHECK-constrained to LAB/RADIOLOGY only (`orders_diagnostic_status_requires_lab_radiology`). Collection and processing are tracked when they happen but are **not hard-required** before result entry — a result can be entered straight from ORDERED (documented, deliberate scope decision, same "optional, not faked" stance `pharmacy_stock` already takes on dispensing without a matching batch).

## 9. Domain objects

- `orders` (`order_type = 'LAB'`) — the request itself, now also carrying `result_entered_by/at`, `verified_by/at`, `released_by/at`.
- `lab_samples` — the specimen: `sample_code` (`LAB-NNNNNN`, generated), `sample_type` (free text — Blood/Urine/Serum/Plasma/Swab/…, no catalog table), `status` (COLLECTED/REJECTED), collection/rejection attribution. One row per collection attempt; history preserved, never overwritten.
- `order_results` — unchanged generic parameter/value/unit/reference-range/abnormal/critical table, one row per result item.

## 10. API requirements

See `docs/workflows/ORDERS.md` for the shared order-creation/listing/cancellation endpoints. Lab-specific: `app/api/orders.py`'s `collect_sample`, `reject_sample`, `start_order_processing`, `verify_order_result`, `release_order_result` — all under `/appointments/{appointment_id}/orders/{order_id}/...`, all reject a PROCEDURE/SERVICE/EXTERNAL_REFERRAL order_id with 422.

## 11. Validation

Sample rejection requires a non-empty `reason`. Every transition is guarded by the order's own current status, checked under a row lock (`SELECT ... FOR UPDATE`) — an invalid transition (e.g. verifying an order still at RESULT_ENTERED's predecessor stage, releasing before verification, double-verify, double-release) returns 409.

## 12. Error handling

Uses the app's existing `{success, errorCode, message, details}` error envelope (`app/error_handling.py`) — no second error format introduced. See `app/api/orders.py`'s `_diagnostic_error_response` for the exact status codes per failure.

## 13. Permissions

`order.collect`/`order.verify`/`order.release` (migrations/0054) — granted to ADMIN/STAFF/DOCTOR/LAB_TECH, the identical set `order.result` (migrations/0051) already uses. **There is no distinct phlebotomist/senior-lab-tech/pathologist role in this app** (confirmed by the source-of-truth audit), so the real safeguard against a result being both entered and verified by the same person is enforced in the service layer, per-record (`verify_order_result_service`: `staff_id == result_entered_by` is refused unless `staff_role == "ADMIN"`), not by a narrower RBAC role split this app has no basis for.

## 14. Audit

`orders.result_entered_by/at`, `verified_by/at`, `released_by/at`, and `lab_samples.collected_by/at`/`rejected_by/reason/at` are the audit trail for this lifecycle — no row is ever silently overwritten; a rejected sample and its replacement both persist. Not additionally written to the generic `audit_logs` table (same pre-existing gap the source-of-truth audit already noted for several other clinical actions, e.g. consultation amendment's own dedicated history table).

## 15. Concurrency

Every transition locks the order row (`SELECT ... FOR UPDATE`) before checking/changing its status — two technicians racing to collect the same sample, or two staff racing to verify the same result, resolve to exactly one winner and one 409 (see `tests/test_lab_workflow.py`'s two concurrency tests).

## 16. Idempotency

Not idempotent by header/key (no `Idempotency-Key` mechanism exists anywhere in this app); idempotent in effect because every transition requires the order to be in one specific prior status — a retried request that already succeeded finds the order no longer in that status and gets a 409, not a duplicate side effect.

## 17. Tests

`tests/test_lab_workflow.py` (21 tests: full lifecycle, sample collect/reject/recollect, invalid transitions, RBAC, concurrency, worklist visibility, billing/cancel interaction) — a dedicated file, unlike before this phase. `tests/test_orders.py`/`test_order_results.py` still cover order creation/cancellation/listing generically (now largely exercised via non-diagnostic `SERVICE`/`PROCEDURE` order types where a test's point was orthogonal to this lifecycle).

## 18. Exit conditions

A released (COMPLETED) LAB order's result is visible to the doctor via `GET /appointments/{id}/orders`, and is billable (`GET .../bill/unbilled`, `POST .../bill/charges`) at any stage from ORDERED onward.

## 19. Next workflow

`docs/workflows/PATIENT_360.md` (result visible in timeline once released), `docs/workflows/BILLING.md` (LAB charge on the encounter's invoice).

---

## Target State

Reached, additively, this phase:

- ✅ Sample collection tracking (collected-by, collected-at, specimen type, rejection/recollection).
- ✅ A distinct verify-then-release step before a result is visible as final (a doctor can still see a RESULT_ENTERED/VERIFIED order's status and drafted values via the same endpoint, deliberately not hidden — see the Gap note below on why field-level hiding wasn't built).
- ❌ A searchable lab-test catalog (name, reference ranges, default units) to replace free-text ordering — still not built, still shared with `docs/architecture/ORDER_SPINE.md`'s own deferred Target State item.

## Gap

**Draft-result visibility is a documented, deliberate scope decision, not an oversight**: `GET /appointments/{id}/orders` returns the full `results` array regardless of whether the order has reached COMPLETED — a RESULT_ENTERED/VERIFIED order's drafted values are visible alongside its own `status` field, which honestly signals "not yet released" rather than hiding the value behind a bespoke field-level ACL this app has no other precedent for. RBAC continues to gate who can *transition* the order (verify/release); it does not additionally hide already-entered field values from a different authenticated staff account. A future phase could add real field-level visibility restriction if real hospital usage calls for it.

The lab-test catalog remains unbuilt, as before this phase — no change.

## Recommended Implementation

Done for this phase. A lab-test catalog (shared with Radiology and every other order type, per `docs/architecture/ORDER_SPINE.md`) is the next scoped increment if needed.
