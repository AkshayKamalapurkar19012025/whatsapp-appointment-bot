# Billing Ledgers

## Purpose

Record, in one place, that this application has two independently-maintained billing tables for the same patient visit. Phase 9's source-of-truth audit named this precisely; this doc is the durable record of that finding and of Option C, the first phase of work against it.

**Superseded in large part by Phase 10** (`docs/architecture/BILLING_LEDGER_UNIFICATION.md`, migrations `0058_billing_ledger_unification.sql`/`0059_billing_ledger_backfill.sql`): what this doc calls "Option A" below — Ledger B absorbing Ledger A for the consultation fee — is now actually implemented. Read that doc for the current write-path mechanism; this doc's remaining value is Option C's read-side combination (still live, see below) and the historical record of how the two phases collided and were reconciled.

## Current State

**Ledger A — appointment-level, the original mechanism** (migrations `0018`/`0019`/`0025`/`0026`):
- `appointments.payment_status` (`UNPAID`/`PAID`/`FAILED`/`WAIVED`/`REFUNDED`), `payment_method`, `payment_amount`, `payment_recorded_by/at`, `waive_reason`, `refund_amount`/`refund_reason`/`refunded_by/at`.
- `invoice_line_items` — ad-hoc extra charges on top of the consultation fee, frozen once `payment_status` leaves `UNPAID`/`FAILED`.
- Amount charged = `doctor_appointment_types.consultation_fee` + `SUM(invoice_line_items.amount)` (`get_invoice_service`, `app/services/appointment_services.py`).
- Covers: the consultation/registration fee only. Never lab, radiology, procedure, pharmacy, or package charges.
- **Drives queue-token issuance.** `generate_queue_token_service` is only ever called from `record_payment_service`'s `PAID` outcome, `waive_consultation_fee_service`, and `settle_free_visit_service` — all three under a `pg_advisory_xact_lock` + row `FOR UPDATE`. This is the single most concurrency-sensitive write path in the app, and Phase 10 (below) deliberately left it untouched.
- **Payment is collectible while `status = 'CHECKED_IN'` or `COMPLETED`** (`_lock_appointment_for_payment`) — widened from CHECKED_IN-only after Option C's own work made a COMPLETED-with-unpaid-fee visit visible via the Dashboard/Exception Engine but not yet collectible (see `tests/test_consultation_payments.py`'s `*_allowed_after_visit_completed` tests). Queue-token issuance is correctly skipped when the appointment is already COMPLETED — a token for a finished visit would be a meaningless artifact in that doctor's live queue view.

**Ledger B — encounter-level, the newer model** (migration `0033`, widened by `0038`/`0039`/`0046`/`0050`, and by Phase 10's `0058`/`0059` below):
- `invoices` (one per encounter) → `charges` (`source_type` = LAB/RADIOLOGY/PROCEDURE/SERVICE/PHARMACY/PACKAGE/CONSUMABLES/CONSULTATION/OTHER) → `payments`.
- **As of Phase 10, this now genuinely includes the consultation fee**: `record_payment_service` records it as a real `CONSULTATION`-sourced charge + payment here (not just Ledger A), linked back via `appointments.consultation_payment_id`. See `docs/architecture/BILLING_LEDGER_UNIFICATION.md` for the full mechanism — this is real absorption, not a mirror.
- Not gated on appointment status at all — a charge/payment can be recorded before, during, or well after the visit.

**A foreign key now connects the two**, for the consultation fee specifically: `appointments.consultation_payment_id → payments.id` (migration `0058_billing_ledger_unification.sql`). Everything else (lab, radiology, pharmacy, package) remains Ledger-B-only, as it always was — there was never a Ledger A equivalent for those.

### Where each screen reads from

| Screen / endpoint | Reads |
|---|---|
| `GET /dashboard/billing` (`BillingPanel.tsx`) | Ledger A (via `EFFECTIVE_PAYMENT_*_SQL`, which itself reads through to the real Ledger B consultation payment) combined with Ledger B's non-consultation charges — Phase 9 Option C, reconciled with Phase 10 below. |
| Billing History / Payment History (`BillingHistoryPanel.tsx`/`PaymentHistoryPanel.tsx`) | Ledger B, unconditionally — now genuinely complete, since Phase 10 made the consultation fee a real Ledger B row these screens' existing, unmodified queries already see. |
| Exception Engine's `PAYMENT_PENDING` (`app/services/exception_engine.py`) | Both, combined — reconciled with Phase 10's `EFFECTIVE_PAYMENT_STATUS_SQL` (see Gap history below). |
| `AppointmentBillingPanel.tsx` (ConsultationWorkspace's Billing tab) | Ledger B only. |
| `BookAppointmentPanel.tsx` (check-in payment step) / `AppointmentDetailsModal.tsx` | Ledger A (via the effective fragments). |
| Visit Completion checklist (`visit_completion_service.py`) | Both, via the effective fragments — kept as a deliberate two-item checklist (`billing_completed` from Ledger B, `payment_completed` from the effective consultation status), not treated as a gap. |

### Secondary asymmetries

- **Refund semantics differ.** Ledger A: `record_refund_service` sets `payment_status = 'REFUNDED'` unconditionally, even for a partial refund, and refuses a second refund attempt outright (still true for a not-yet-linked appointment). Ledger B: `payments.refunded_amount` is a running numeric total on a payment that stays `COMPLETED`, supporting incremental partial refunds. Phase 10 picked Ledger B's model going forward — `record_refund_service` delegates to it when `consultation_payment_id` is linked (true for every payment recorded after Phase 10, and every historical row `migrations/0059` backfilled), while still updating the legacy columns too (see `BILLING_LEDGER_UNIFICATION.md`'s "Refunds" section for why).
- **`FAILED`/`DECLINED`** exist on both (Ledger A since `0018`; Ledger B added `DECLINED` in `0050`), but `AppointmentBillingPanel.tsx` doesn't yet expose the `DECLINED` outcome in its UI (API-only today).
- **A `CONSULTATION`-sourced charge could always be manually added via `POST /bill/charges`** — Phase 10 didn't close this off; `charges_one_consultation_per_invoice` (one ACTIVE consultation charge per invoice) means a manual add now competes with the real one for the same slot rather than creating a silent duplicate, which is a real improvement, but a clerk manually adding one before a real payment is recorded is still an accepted, unaddressed edge case.

## Target State

- **Option A — Ledger B absorbs Ledger A.** **Implemented as of Phase 10** for the consultation fee's payment/refund path (`docs/architecture/BILLING_LEDGER_UNIFICATION.md`). `generate_queue_token_service`'s trigger itself was deliberately left untouched (still `outcome == 'PAID'` on the legacy call), not moved to "this encounter's invoice balance is 0" — a narrower, lower-risk absorption than this doc originally scoped Option A to be.
- **Option B — Ledger A absorbs Ledger B.** Not attempted, and superseded by Option A's implementation — there is no remaining reason to pursue this direction for the consultation fee. (Note: ADR-009 in `docs/OPD_HIMS_ARCHITECTURE_AUDIT.md` used "Option B" for a different, since-superseded mechanism — an additive mirror, not this doc's "Ledger A absorbs Ledger B" — a real vocabulary collision between two independently-written docs, flagged rather than quietly fixed.)
- **Option C — merge only the read side.** Done for Dashboard and the Exception Engine (see above); still the live mechanism for `GET /dashboard/billing`'s non-consultation collections, since Ledger B was never unified for lab/radiology/pharmacy/package charges (there is no Ledger A equivalent to absorb).

## Gap history (resolved)

Two gaps this doc originally named here are now closed:

- **Billing History / Payment History** — no longer Ledger B only. Phase 10's real Ledger B absorption means these screens' existing, unmodified queries now see the consultation fee as an ordinary row — no synthetic row-shape design was needed after all, since the underlying data model itself now speaks Ledger B's language.
- **A visit closed with its consultation fee still unpaid is now collectible**, not just visible: `record_payment_service`/`waive_consultation_fee_service`/`settle_free_visit_service`'s shared `_lock_appointment_for_payment` guard accepts `COMPLETED` as well as `CHECKED_IN`, with queue-token issuance correctly skipped for the `COMPLETED` case.

**A third gap was introduced, then found and fixed, by the collision between Phase 10 and Option C landing on main independently**: Phase 10's `EFFECTIVE_PAYMENT_*_SQL` fragments make Ledger A's own queries already read through to the real Ledger B consultation payment, but Option C's Dashboard/Exception-Engine helpers were written assuming Ledger A and B were still disjoint — combining them double-counted every real consultation-fee payment (once via the effective fragment, once via Option C's unfiltered Ledger B query), to the point of a live 500 error when an earlier, now-removed ADR-009 mirror mechanism was also in the mix (two competing writers to the same `charges_one_consultation_per_invoice` slot). Fixed by excluding the consultation charge/payment (`source_type <> 'CONSULTATION'`, or the specific payment linked via `consultation_payment_id`) from every generic Ledger B query in `app/api/dashboard.py` and `app/services/exception_engine.py`, and by updating `_payment_pending`'s own Ledger A query to use `EFFECTIVE_PAYMENT_STATUS_SQL` instead of the raw `payment_status` column (see `docs/architecture/BILLING_LEDGER_UNIFICATION.md`'s reader table for the corrected account). `tests/test_billing_report.py`, `tests/test_exception_engine.py`, and `tests/test_billing_ledger_reconciliation_gap.py` all exercise this.

**Still genuinely unstarted**: moving `generate_queue_token_service`'s own trigger off the legacy `outcome == 'PAID'` check (e.g. onto "this encounter's invoice balance is 0") — Phase 10 explicitly left this alone given it's the single most concurrency-sensitive path in the app. Not currently scoped.
