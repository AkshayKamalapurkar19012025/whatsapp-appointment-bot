# Billing Ledger Unification (Phase 10)

## Purpose

Documents the Phase 10 migration: unifying the two independent financial mechanisms the architecture audit identified (`HOSPITALOS — OPD ARCHITECTURAL GAP & TARGET ARCHITECTURE AUDIT`, ADR-009) into one authoritative ledger for consultation-fee payments, without deleting historical data, without dual-writing new payments, and without breaking any existing reader.

## Old architecture

Two genuinely separate financial mechanisms existed for one visit, exactly as `migrations/0033_billing_invoices.sql`'s own header documented and deliberately deferred:

- **Ledger 1** — `appointments.payment_status/payment_amount/payment_method/payment_recorded_by/payment_recorded_at/waive_reason/refund_*`. Written only by `record_payment_service`/`waive_consultation_fee_service`/`settle_free_visit_service`/`record_refund_service` (`app/services/appointment_services.py`). Single-shot: one `payment_amount` scalar per appointment, no native partial-payment or multi-refund support. Directly coupled to `generate_queue_token_service` — the actual queue-admission trigger.
- **Ledger 2** — `invoices`/`charges`/`payments` (`migrations/0033`), encounter-scoped. Built for lab/radiology/procedure/pharmacy/package charges and ad-hoc line items. Already supported genuine partial payment (multiple `payments` rows, computed `PARTIALLY_PAID` status), incremental refunds, and void — but never carried the consultation fee.

Getting "what was charged for this visit" required two independent queries; the consultation fee was invisible to Billing History/Payment History and to the Exception Engine's payment-pending detection.

## New architecture

The consultation fee is now recorded as a `charges` row with `source_type = 'CONSULTATION'` (a value the schema already accepted, unused until this phase) on the encounter's ledger-2 invoice, followed by a `payments` row — using the *existing* `add_charge_service`/`record_invoice_payment_service` mechanics, not a new payment implementation.

```
Appointment (payment_recorded via /payment or /bill/payments)
      │
      ▼
appointments.consultation_payment_id ──► payments.id
      │                                       │
      ▼                                       ▼
Encounter ──► Invoice (1:1, encounter_id UNIQUE)
                  │
                  ├── charges (CONSULTATION, LAB, RADIOLOGY, PHARMACY, PROCEDURE, PACKAGE, ...)
                  │     at most ONE active CONSULTATION charge per invoice
                  │     (charges_one_consultation_per_invoice, migrations/0058)
                  └── payments (COMPLETED / DECLINED, supports partial + multiple refunds)
```

### Schema changes (`migrations/0058_billing_ledger_unification.sql`)

- `appointments.consultation_payment_id BIGINT REFERENCES payments(id)` — a plumbing cross-reference, not a financial-truth column. Ledger-2 payments have no charge-level link (a payment ties only to its invoice), so this is what lets `record_payment_service`'s idempotency check and `record_refund_service`'s refund routing find the right payment without guessing from amount/ordering.
- `charges_one_consultation_per_invoice` — a partial unique index (`invoice_id WHERE source_type='CONSULTATION' AND status='ACTIVE'`), the same "prevent double-billing the same source" pattern `charges_source_order_unique`/`charges_source_dispense_unique` already use for LAB/RADIOLOGY/PHARMACY.
- `bill.record_payment` permission, granted to **every** role — see RBAC below.

### Consultation charge amount

Equals `consultation_fee + invoice_line_items total` — the exact same number `record_payment_service` always charged (reuses `get_consultation_charge_service`/`_invoice_extra_charges_total` unchanged). `invoice_line_items` (migrations/0026) is untouched and keeps working exactly as before; its own gate (only addable while unpaid) now also checks the ledger-2 payment state, not just the legacy column (see below).

### The ₹0-fee edge case

`charges.amount` and `payments.amount` both `CHECK (amount > 0)` — a ₹0 consultation fee explicitly recorded "PAID" (as opposed to routed through the dedicated `settle_free_visit_service`) cannot be represented as a ledger-2 event. This case is detected and handled via the legacy columns only, exactly as it worked before this phase — structurally the same treatment a waiver gets (see below), for the same reason.

### Waivers stay on the legacy ledger, permanently

`waive_consultation_fee_service`/`settle_free_visit_service` are **unchanged writers** — a waiver is never money changing hands, and the `amount > 0` constraint above means it structurally cannot be a ledger-2 charge. `appointments.waive_reason`/`payment_status='WAIVED'`/`payment_recorded_by`/`payment_recorded_at` remain the permanent, authoritative record for every waiver, past and future. Their conflict guards were extended to also recognize an already-completed ledger-2 payment (so a waiver/free-settle attempt after a real payment still correctly refuses), but their own write path never touches ledger 2.

### Refunds — the one deliberate mirror

`record_refund_service` now delegates to `billing_services.refund_invoice_payment_service` (reusing its remaining-balance math, not re-implementing it) when `consultation_payment_id` is linked. It also still updates the legacy `payment_status='REFUNDED'`/`refund_amount`/`refund_reason`/`refunded_at` columns — the one deliberate exception to "ledger 2 only" this phase makes, because several existing readers (`app/api/dashboard.py`'s refund summary/list) key off those columns directly, and a refund is a rare, staff-initiated, already-audited correction, not the high-frequency payment-collection path this phase targets.

**A genuinely new semantic question this surfaces**: ledger 1 never supported partial refunds (one-shot only), so "refunded" and "not fully paid any more" were the same fact. Ledger 2 does support a second partial refund. The response's `payment_status` for a *partially* refunded payment reads **"PAID"** (something is still genuinely paid) from the ledger-2-derived value, while the legacy mirror is set to `'REFUNDED'` unconditionally (any refund activity at all) so the dashboard's refund report still catches it. These answer two different questions — "is anything still owed" vs. "did a refund ever happen" — that shared one column name only because partial refunds were never possible before. A second full refund attempt (nothing left to refund) is still rejected with the original 409 `PaymentStateConflict`, not a 422 — preserving the exact pre-existing API contract for that case.

### Queue-token trigger — preserved exactly

`generate_queue_token_service` is called from the same place, under the same conditions (`outcome == "PAID"`), using the same `pg_advisory_xact_lock`/idempotency mechanics — completely untouched. Verified under real concurrent load (see Concurrency below) and via live API scenarios.

### Readers updated (so the API response shape never changes)

Every place that used to read `appointments.payment_status`/`payment_amount`/`payment_method`/`payment_recorded_at` directly now uses the shared `EFFECTIVE_PAYMENT_*_SQL` fragments (`app/services/appointment_services.py`), which prefer the linked ledger-2 payment and fall back to the legacy columns only when nothing is linked (WAIVED, or genuinely never-attempted):

| Reader | Change |
|---|---|
| `GET /appointments` (list) | Uses `EFFECTIVE_PAYMENT_*_SQL` — `AppointmentsPanel.tsx`/`format.ts` need zero frontend changes, same response field names. |
| `GET /dashboard/billing` | Collections-by-method/doctor and the outstanding-unpaid list use the effective fragments; waivers/refunds sections are untouched (correctly still legacy-column-based, per the mirror above). Option C (merged after this phase branched, before it merged) separately combines these effective-fragment queries with its own generic Ledger B query for non-consultation billing — that combination double-counted every real consultation-fee payment until reconciled (`docs/architecture/BILLING_LEDGERS.md` has the full account); `_ledger_b_collections_by_method`/`_by_doctor`/`_outstanding` now explicitly exclude the consultation charge/payment. |
| `visit_completion_service.py` | "Payment completed" check uses the effective status. |
| `app/services/exception_engine.py` | `_billing_not_started` is ledger-2-native and needed no change. `_payment_pending` was true at the time this phase was branched (a single ledger-2-only query), but Phase 9's Option C (merged to main after this phase branched, before it merged) added a *second*, legacy-`payment_status` query to the same function to catch a consultation-fee-only outstanding balance — a real doc/code drift this merge produced. Both of `_payment_pending`'s queries now correctly use `EFFECTIVE_PAYMENT_STATUS_SQL`/exclude the consultation charge from the ledger-2 side, so a real consultation-fee payment is recognized as settled and never double-counted (see `docs/architecture/BILLING_LEDGERS.md`'s own account of the merge conflict this caused). |
| `app/services/billing_history_service.py` | **Already ledger-2-native** — no change; Billing History/Payment History screens now show consultation payments for the first time. |
| `app/api/billing.py` (receipt) | Reads `payments`/`charges` directly already — unaffected. |

## Coexistence

Both `POST /appointments/{id}/payment` (legacy) and `POST /appointments/{id}/bill/payments` (ledger-2-native) remain live indefinitely for this phase. The legacy endpoint is now a thin wrapper — `record_payment_service` computes the amount and delegates to `billing_services.record_consultation_fee_payment_service`, which itself delegates to `record_invoice_payment_service` for the actual payment insert. No payment business logic is duplicated between the two paths.

## Historical data backfill (`migrations/0059_billing_ledger_backfill.sql`)

For every existing appointment:

| Legacy `payment_status` | Backfilled to ledger 2 |
|---|---|
| `PAID` | invoice + CONSULTATION charge + a `COMPLETED` payment |
| `FAILED` | invoice + CONSULTATION charge + a `DECLINED` payment (doesn't count toward paid total) |
| `REFUNDED` | invoice + CONSULTATION charge + a `COMPLETED` payment carrying the original amount, with `refunded_amount`/`refund_reason`/`refunded_by`/`refunded_at` copied across |
| `WAIVED` | **nothing** — see "Waivers" above |
| `UNPAID` | **nothing** — never charged or collected in the old model either |

Every step is guarded (`NOT EXISTS`, `payment_recorded_by IS NOT NULL`) so the file is safe to reason about even though migrations only run once. Verified idempotent by direct re-run (zero duplicates on a second pass) and reconciled against synthetic PAID/FAILED/REFUNDED/WAIVED/UNPAID data with zero unmatched rows, zero duplicate invoices/charges, zero orphans — see the Phase 10 completion report for the full reconciliation output.

## RBAC — `bill.record_payment`

Both payment-recording endpoints were previously gated only by bare authentication (`get_current_staff`) — the one billing-mutating action left that way, per `migrations/0031_rbac_decomposition.sql`'s own explicit, documented design choice grouping "routine payment collection" with vitals/orders/prescriptions as intentionally bare-auth. This phase makes that access **explicit and revocable** rather than implicit: `bill.record_payment` is granted to **every** existing role (not narrowed to ADMIN/BILLING the way `bill.add_charge`/`void`/`refund_payment` are), because today literally any authenticated staff account can record a payment — narrowing that grant is a real product decision for a later phase, not something to guess here. Both endpoints now also write an audit-log entry (`bill.record_payment`), where before neither did.

## Concurrency

`_lock_appointment_for_payment`'s `FOR UPDATE OF a` (unchanged, pre-existing) already serializes two concurrent consultation-payment attempts for the same appointment. `_ensure_invoice`'s `ON CONFLICT (encounter_id) DO NOTHING` plus `invoices.encounter_id`'s own `UNIQUE` constraint prevent duplicate invoices; `charges_one_consultation_per_invoice` backs up the row lock at the DB level for the charge itself. Four new tests in `tests/test_concurrency_hardening.py` exercise this under real threads: same-appointment concurrent payment (one charge, one payment, one token), concurrent first-invoice creation, and a direct DB-level proof that the unique index rejects a duplicate consultation charge.

## Rollback

Nothing about this phase requires a destructive rollback. The legacy `appointments.payment_*`/`refund_*`/`waive_reason` columns are never dropped and are never mirrored-into for new PAID payments, so rolling back is: revert the service-layer code so `record_payment_service` writes to ledger 1 again. Every ledger-2 row created by this phase (fresh payments plus the backfill) is purely additive and stays intact and harmless if that revert happens — no data is lost, no schema change needs undoing.

## Future removal of legacy columns

Only once: every reader is confirmed migrated (grep-verified — done in this phase for every reader that existed at the time), the coexistence period has run long enough to build confidence, and a decision is made on whether to also unify the legacy `/payment` endpoint away entirely or keep it as a permanent alias. Not attempted in this phase.

## Out of scope for this phase

Internal referral, IPD, Emergency, `encounter_type` widening, Insurance/TPA/corporate billing, ABDM/FHIR/SMART/OAuth2/HL7/DICOM/IHE/NHCX, and any UI redesign — all explicitly deferred per the architecture audit's own ADRs and this phase's hard scope boundary.
