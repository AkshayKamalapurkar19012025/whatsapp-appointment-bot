# ADR-009: Billing Ledger Unification

## Status

Accepted (Option B — coexistence). Implemented for Phase 10B (this branch, `claude/phase-10b-billing-coexistence`). See `docs/architecture/BILLING_LEDGER_COEXISTENCE.md` for the full design and the evidence-based comparison against Option A.

**This is a transition architecture, not the final target.** The final target remains a single ledger — see "Final target architecture" below. Option A (full cutover, no dual-write) was implemented independently on branch `claude/phase-10-billing-unification` (commit `31e87f2`, PR #121) and is preserved there as a shelved, unmerged alternative — read alongside this ADR for the concrete evidence behind choosing Option B over it, but do not build on that branch; it is not part of this system's active history.

## Context

This codebase has always had two billing mechanisms that grew up independently, documented candidly in migrations `0033`'s own header:

- **Ledger A** — `appointments.payment_status`/`payment_method`/`payment_amount`/`payment_recorded_by`/`payment_recorded_at`/`waive_reason`/`refund_amount`/`refund_reason`/`refunded_by`/`refunded_at` (migrations `0018`/`0019`/`0025`/`0026`). Consultation-fee only. This is the OPD workflow's actual queue-entry gate: `generate_queue_token_service` fires directly off a successful payment or waiver here (`app/services/appointment_services.py`'s `record_payment_service`/`waive_consultation_fee_service`). The single most concurrency-sensitive, heavily-tested path in the codebase.
- **Ledger B** — `invoices`/`charges`/`payments` (migration `0033`). A real, general invoice/charge/payment model covering every charge type (`CONSULTATION`/`LAB`/`RADIOLOGY`/`PROCEDURE`/`SERVICE`/`PHARMACY`/`OTHER`) with genuine partial-payment and refund support. Built explicitly *not* to touch Ledger A (`0033`'s own header explains why: rewriting the queue-token trigger's dependency for the benefit of "one ledger" was judged too risky for that migration's scope).

The result: a visit's *true* total collected has always lived split across two places. `GET /dashboard/billing`'s "collected" total, pre-this-phase, only ever summed Ledger A — so a patient who paid ₹500 for their consultation and ₹1200 for a lab order showed as "₹500 collected," silently dropping the ₹1200 that only ever existed in Ledger B.

## Problem

How does the system get one correct, unified financial view (a real "money collected" number that includes every charge type, not just the consultation fee) without touching the queue-token trigger's dependency on Ledger A — the one path in this codebase where a regression means patients silently stop being able to check in?

## Decision

**Option B: dual-write coexistence.** Ledger A remains the untouched operational/workflow gate. Every Ledger A consultation-payment write also mirrors, in the same transaction, into Ledger B (reusing the existing `invoices`/`charges`/`payments` tables — no new parallel ledger). Ledger B becomes the consolidated financial/reporting source going forward.

- **Single chokepoint.** `app/services/appointment_services.py`'s `_write_consultation_payment_status` is the only place `appointments.payment_status` may be written — enforced by an AST-based structural test (`tests/test_ledger_a_chokepoint.py`), not a convention. It calls `billing_services.py`'s `mirror_legacy_consultation_payment_service` on the *same cursor*, inside the *same transaction*, immediately after Ledger A's own UPDATE.
- **Same-transaction guarantee, not eventual consistency.** If the mirror fails for any reason (e.g. the invoice was independently voided via `/bill/void`), the whole transaction rolls back — Ledger A's own UPDATE, even though it runs first and would otherwise have succeeded standalone, is undone too. Proven by `tests/test_billing_ledger_coexistence_concurrency.py::test_invoice_voided_mid_payment_rolls_back_ledger_a_too`.
- **Idempotency via DB constraints, not just application checks.** `charges_one_consultation_per_legacy_appointment` (a partial unique index on `charges.legacy_appointment_id`, migration `0058`) guarantees at most one ACTIVE consultation charge per appointment regardless of how many times the mirror runs. `payments.legacy_appointment_id` (a second, separate correlation column — payments have no `charge_id`) lets the REFUNDED mirror find the exact right payment row to apply a refund to, not just any COMPLETED payment on the invoice.
- **Event mapping** (PAID/FAILED/WAIVED/REFUNDED, matching Ledger A's own vocabulary exactly):
  - PAID/FAILED → an ACTIVE `CONSULTATION` charge (found-or-created) plus a COMPLETED/DECLINED payment.
  - WAIVED → **only when the forgiven amount is nonzero** (`waive_consultation_fee_service`'s real-fee case): a real ACTIVE charge plus a COMPLETED payment with `method = 'WAIVED'` (a new, additive value on `payments.method`'s CHECK constraint, migration `0058`, the same pattern migration `0050` used to add `'DECLINED'` to `payments.status`). `settle_free_visit_service`'s always-₹0 case mirrors *nothing* — `charges.amount`/`payments.amount` both `CHECK (amount > 0)`, so a genuinely zero-cost visit has no possible Ledger B representation, waived or otherwise. This is a resolved "nothing to mirror" case, not a silently-dropped one; the zero-amount guard in `mirror_legacy_consultation_payment_service` applies to any event, not just WAIVED, for the same reason.
  - REFUNDED → applies `refunded_amount`/`refund_reason`/`refunded_by`/`refunded_at` to the one mirrored payment row, a single one-shot application matching Ledger A's own one-shot refund semantics (`record_refund_service` rejects a second refund attempt). Ledger B's own `refund_invoice_payment_service` supports genuine multiple partial refunds; that superset capability is deliberately not exposed through this mirror, since Ledger A — the source of truth this phase mirrors *from* — has no equivalent to expose.
- **RBAC closed as part of this phase, on its own merits.** `bill.record_payment` (migration `0058`) now gates both `/appointments/{id}/payment` and `/appointments/{id}/bill/payments` — previously bare-auth (any authenticated staff session), independent of which ADR-009 option was chosen.
- **Reconciliation invariant**, not a blind sum comparison. WAIVED is deliberately excluded from "must have a mirror": Ledger A's own WAIVED write always records `payment_amount = 0`, for both a real forgiven fee and an always-zero visit — Ledger A alone cannot tell which case a given row is, so a mirror's *absence* is never itself a mismatch; only a mirror's *malformed presence* is. See `tests/test_billing_ledger_reconciliation_gap.py`'s `RECONCILIATION_MISMATCH_SQL` for the exact, documented query.
- **Historical backfill** (`migrations/0059_billing_ledger_coexistence_backfill.sql`) mirrors every pre-migration-`0058` PAID/FAILED/REFUNDED event, idempotently (NOT EXISTS-guarded, not `ON CONFLICT DO NOTHING` — a real duplicate-insert attempt should surface, not be silently swallowed). Explicitly, permanently excludes rather than guesses at: WAIVED/UNPAID (no real amount to backfill from, ever), a row with no `encounter_id` (nowhere in Ledger B to attach to), a row with no `payment_recorded_by` (no real staff attribution), and a row whose `refund_amount` would violate `payments`' own `CHECK (refunded_amount <= amount)`.

## Final target architecture

Coexistence is a transition, not the destination. The final target is unchanged from what Option A already proves is buildable: **Ledger B only**, with Ledger A's payment columns either removed or reduced to a read-only historical projection, and the queue-token trigger reading Ledger B directly. Getting there requires, in order:

1. Every Ledger A consultation-payment event has a correct Ledger B mirror (this phase's own reconciliation invariant, continuously green).
2. Every reader of Ledger A's payment columns is migrated to read Ledger B instead (this phase migrates `GET /dashboard/billing`'s collections/total_collected; outstanding/waivers/refunds remain documented Ledger-A-sourced exceptions — see `docs/architecture/BILLING_LEDGER_COEXISTENCE.md`'s reader inventory for what's left).
3. Only once (1) and (2) both hold for a sustained period does replacing the queue-token trigger's dependency — the one action `migrations/0033`'s own header named as too risky to do in one step — become a reasonably-scoped, well-evidenced change, at which point Option A's already-built cutover (`31e87f2`) becomes the reference implementation to adapt, not a wasted branch.

## Alternatives considered

1. **Option A — full cutover, no dual-write** (built independently, PR #121/`31e87f2`; not merged, kept as a shelved reference). Ledger B becomes authoritative immediately; Ledger A's payment columns become a computed/read-only projection off Ledger B (`EFFECTIVE_PAYMENT_*_SQL`); the queue-token trigger's dependency is rewired to read Ledger B in the same phase. Rejected as *this* phase's approach — not because it doesn't work (it passed 802 tests on its own branch), but because it changes the queue-token trigger's dependency in the same phase that also builds and validates the two-ledger mapping for the first time, compounding the two highest-risk changes in this codebase into one unreviewable step. See `docs/architecture/BILLING_LEDGER_COEXISTENCE.md` for the full, evidence-based diff-size/risk comparison.
2. **A third, brand-new ledger table** (rejected outright, never seriously considered) — Ledger B already models every charge type generically; a third table would just be a second "Ledger A vs Ledger B" problem one level up.
3. **Leave the two ledgers permanently split, only fix the dashboard's arithmetic to add them together at read time** (rejected) — still leaves refunds/voids/partial payments on the consultation fee invisible to Ledger B's own tooling (receipts, reconciliation, future insurance/TPA claim modeling), and doesn't close the RBAC gap on the write side.

## Consequences

- `GET /dashboard/billing`'s `collections_by_method`/`collections_by_doctor`/`total_collected` are now genuinely unified across every charge type — the ₹500 + ₹1200 = ₹1700 case this phase was named for. `outstanding_unpaid`/`waivers`/`refunds` remain Ledger-A-sourced, each independently justified in `app/api/dashboard.py`'s own docstring.
- `GET /appointments/{id}/payment` and `/waive-payment` now have a new failure mode they didn't have before Phase 10B: `InvoiceVoided` (409), if the visit's whole bill was voided independently via `/bill/void` before the consultation fee was ever paid/waived. Handled explicitly in `app/api/appointments.py`.
- A receipt can now, in principle, be printed for a legacy consultation payment via `get_payment_receipt_service` (already Ledger-B-only) using the mirror's `payment_id` — not yet wired to any frontend action in this phase (no UI regression: there was no consultation-fee receipt at all before).
- Every Ledger A write path funnels through one function, verified structurally (not by convention) on every test run.

## Future implications

See `docs/architecture/BILLING_LEDGER_COEXISTENCE.md`'s "Open items" section for what Phase 10B deliberately did not do (outstanding/waivers/refunds unification, the timezone/DST test-authoring bug found but explicitly not touched this phase, wiring a consultation-fee receipt into the frontend) and for the concrete evidence this ADR's "Final target architecture" section points to.
