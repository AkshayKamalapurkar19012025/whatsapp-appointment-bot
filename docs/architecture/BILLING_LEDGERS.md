# Billing Ledgers

## Purpose

Record, in one place, that this application has two independently-maintained billing tables for the same patient visit — not a documentation exaggeration, and not (yet) unified. Phase 9's source-of-truth audit named this precisely; this doc is the durable record of that finding and of Option C, the first phase of work against it.

## Current State

**Ledger A — appointment-level, the original mechanism** (migrations `0018`/`0019`/`0025`/`0026`):
- `appointments.payment_status` (`UNPAID`/`PAID`/`FAILED`/`WAIVED`/`REFUNDED`), `payment_method`, `payment_amount`, `payment_recorded_by/at`, `waive_reason`, `refund_amount`/`refund_reason`/`refunded_by/at`.
- `invoice_line_items` — ad-hoc extra charges on top of the consultation fee, frozen once `payment_status` leaves `UNPAID`/`FAILED`.
- Amount charged = `doctor_appointment_types.consultation_fee` + `SUM(invoice_line_items.amount)` (`get_invoice_service`, `app/services/appointment_services.py`).
- Covers: the consultation/registration fee only. Never lab, radiology, procedure, pharmacy, or package charges.
- **Drives queue-token issuance.** `generate_queue_token_service` is only ever called from `record_payment_service`'s `PAID` outcome, `waive_consultation_fee_service`, and `settle_free_visit_service` — all three read/write this ledger exclusively, under a `pg_advisory_xact_lock` + row `FOR UPDATE`. This is the single most concurrency-sensitive write path in the app.
- **Payment is only collectible while `status = 'CHECKED_IN'`** (`_lock_appointment_for_payment`). Once an appointment reaches `COMPLETED`, there is no API path to record, waive, or settle its consultation fee — confirmed directly (`tests/test_exception_engine.py::test_payment_pending_exception_from_unpaid_consultation_fee_alone` asserts the 409). A visit that closes with its consultation fee still unpaid is stuck that way permanently through the existing endpoints.

**Ledger B — encounter-level, the newer model** (migration `0033`, widened by `0038`/`0039`/`0046`/`0050`):
- `invoices` (one per encounter) → `charges` (`source_type` = LAB/RADIOLOGY/PROCEDURE/SERVICE/PHARMACY/PACKAGE/CONSUMABLES/CONSULTATION/OTHER) → `payments`.
- Covers everything except the consultation fee in practice. `CONSULTATION` is a legal `charges.source_type` value, but nothing in the app ever auto-creates one — a billing clerk *could* manually add a charge and hand-pick `CONSULTATION`, creating an ad-hoc, unlinked duplicate of what Ledger A already tracks. Not currently prevented.
- Not gated on appointment status at all — a charge/payment can be recorded before, during, or well after the visit.

**No foreign key or trigger connects the two.** They are separately-maintained tables, not two views over the same data.

### Where each screen reads from (as of Phase 7)

| Screen / endpoint | Reads |
|---|---|
| `GET /dashboard/billing` (`BillingPanel.tsx`) | **Both, combined as of Phase 9 Option C** — see below. |
| Billing History / Payment History (`BillingHistoryPanel.tsx`/`PaymentHistoryPanel.tsx`) | Ledger B only. Not addressed by Option C — see Gap. |
| Exception Engine's `PAYMENT_PENDING` (`app/services/exception_engine.py`) | **Both, combined as of Phase 9 Option C.** |
| `AppointmentBillingPanel.tsx` (ConsultationWorkspace's Billing tab) | Ledger B only. |
| `BookAppointmentPanel.tsx` (check-in payment step) / `AppointmentDetailsModal.tsx` | Ledger A only. |
| Visit Completion checklist (`visit_completion_service.py`) | Both, kept intentionally separate (`billing_completed` from Ledger B, `payment_completed` from Ledger A) — this is a deliberate two-item checklist, not treated as a gap. |

### Secondary asymmetries (not addressed by Option C)

- **Refund semantics differ.** Ledger A: `record_refund_service` sets `payment_status = 'REFUNDED'` unconditionally, even for a partial refund, and refuses a second refund attempt outright. Ledger B: `payments.refunded_amount` is a running numeric total on a payment that stays `COMPLETED`, supporting incremental partial refunds. Unifying the ledgers means picking one of these two models.
- **`FAILED`/`DECLINED`** exist on both (Ledger A since `0018`; Ledger B added `DECLINED` in `0050`, explicitly modeled to mirror Ledger A's `FAILED`), but `AppointmentBillingPanel.tsx` doesn't yet expose the `DECLINED` outcome in its UI (API-only today).
- **A `CONSULTATION`-sourced charge is possible but never auto-created** — see above. Latent double-counting risk if a billing clerk ever manually adds one.

## Target State

One of the three options the audit identified, not yet chosen beyond Option C:

- **Option A — Ledger B absorbs Ledger A.** Auto-create a `CONSULTATION`-sourced charge on check-in, retire Ledger A entirely, move `generate_queue_token_service`'s trigger to "this encounter's invoice balance is 0." Cleanest end state; touches the highest-risk path in the app and needs a historical-data migration.
- **Option B — Ledger A absorbs Ledger B.** Keep token issuance untouched; make Ledger B write through to `appointments.payment_amount`/`payment_status` as a derived total. Avoids the token-issuance risk, but Ledger A's status enum has no `PARTIALLY_PAID` value, which Ledger B already needs.
- **Option C — merge only the read side (this phase).** Leave both write paths exactly as they are; build combined read views for screens that need "the whole picture." Lowest risk, ships incrementally, but leaves two permanent write paths — not a real unification, a mitigation.

## Gap

Option C is done for the two screens where the split was most concretely damaging (§ below). Still split, deliberately left alone this phase:

- **Billing History / Payment History** — still Ledger B only. Unlike the Dashboard/Exception-Engine fixes (summary/aggregate queries), these are paginated listing screens over structurally different row shapes (an "invoice" row has line items, tax, discount; a consultation-fee row has none of that). Folding them into one feed means designing a synthetic row shape for Ledger A entries, which is a real design decision, not a mechanical read-merge — left for a future phase.
- **A visit closed with its consultation fee still unpaid is now visible (Exception Engine, Dashboard) but still not collectible** through any existing endpoint, since `record_payment_service` requires `CHECKED_IN`. This is a genuine, newly-surfaced gap (previously it was invisible *and* uncollectible; it is now visible and still uncollectible) — a write-path change, explicitly out of scope for Option C's read-only merge.
- Options A and B themselves — neither has been started. Full unification remains future work.

## Recommended Implementation

Given the newly-surfaced "visible but uncollectible" gap, the next concretely useful increment is narrower than a full Option A/B unification: allow `record_payment_service` (and its waive/settle-free-visit siblings) to also apply to a `COMPLETED` appointment whose `payment_status` is still `UNPAID`/`FAILED` — a small, well-scoped loosening of one existing status guard, not a ledger merge. That, plus Billing History/Payment History's row-shape design question, are the two next items; full Option A (the real unification) should stay its own, separately-scoped, explicitly-approved phase given the token-issuance risk documented above.
