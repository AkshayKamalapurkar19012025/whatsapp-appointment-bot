# Billing Ledger Coexistence (Phase 10B, ADR-009 Option B)

See `docs/decisions/ADR-009-BILLING-LEDGER-UNIFICATION.md` for the decision itself and why Option B (this document) was chosen over Option A. This document is the implementation-level detail: what exists, what reads from where, and what's still open.

## Current state (as of this phase)

### The two ledgers

| | Ledger A | Ledger B |
|---|---|---|
| Tables | `appointments.payment_status` and 9 sibling columns | `invoices`/`charges`/`payments` |
| Scope | Consultation fee only | Every charge type (`CONSULTATION`/`LAB`/`RADIOLOGY`/`PROCEDURE`/`SERVICE`/`PHARMACY`/`OTHER`) |
| Role | Operational gate — `generate_queue_token_service` fires off a successful PAID/WAIVED here | Consolidated financial/reporting ledger |
| Write path | `app/services/appointment_services.py`'s `_write_consultation_payment_status` (the only place; see below) | `app/services/billing_services.py` — both from the mirror (below) and independently, from every ad-hoc charge/invoice-payment call (`add_charge_service`, `record_invoice_payment_service`, etc.) |

### The chokepoint and the mirror

`_write_consultation_payment_status(cur, appointment_id, *, status, ...)` is the only function allowed to `UPDATE appointments SET payment_status = ...`. Enforced by `tests/test_ledger_a_chokepoint.py`, an AST-based structural test that scans every `.py` file under `app/` for a `cur.execute(...)` call whose SQL text matches `UPDATE ... APPOINTMENTS ... SET ... PAYMENT_STATUS` outside that one function — not a documentation convention, a test that fails the build if violated.

Immediately after Ledger A's own UPDATE, on the *same cursor* (same transaction), the chokepoint calls `billing_services.py`'s `mirror_legacy_consultation_payment_service(cur, appointment_id, event=..., ...)`. If the mirror raises, the whole transaction — Ledger A's UPDATE included — rolls back (`app/db/connection.py`'s `get_connection()` context manager commits on clean exit, rolls back on exception). There is no window where Ledger A says PAID and Ledger B has nothing, or vice versa, for a request that returned success.

### Event mapping

| Ledger A event | Ledger B mirror |
|---|---|
| PAID | ACTIVE `CONSULTATION` charge (found via `legacy_appointment_id`, created if absent) + COMPLETED payment, `method` = whatever was recorded |
| FAILED | Same charge lookup/creation + DECLINED payment (records the *attempted* amount, same convention `payments.status`'s own DECLINED value already uses for Ledger-B-native declined attempts) |
| WAIVED, nonzero amount (`waive_consultation_fee_service`) | Same charge lookup/creation + COMPLETED payment, `method = 'WAIVED'` |
| WAIVED, zero amount (`settle_free_visit_service`) | **Nothing** — `charges.amount`/`payments.amount` both `CHECK (amount > 0)`; there is no possible representation, resolved as "nothing to mirror," not silently dropped |
| REFUNDED | Finds the one payment row where `legacy_appointment_id` matches and `status = 'COMPLETED'`, applies `refunded_amount`/`refund_reason`/`refunded_by`/`refunded_at` — a single one-shot application, matching Ledger A's own one-shot refund rule |

### Schema additions (migration `0058`)

- `charges.legacy_appointment_id` / `payments.legacy_appointment_id` — nullable `BIGINT REFERENCES appointments(id)`, the correlation columns the mirror and the reconciliation query both key off. Two separate columns (not one shared FK) because payments have no `charge_id` of their own — a REFUNDED mirror needs to find the *one* payment row for this appointment directly, not "any COMPLETED payment on the invoice" (which could also include a paid, unrelated lab charge).
- `charges_one_consultation_per_legacy_appointment` — partial unique index (`WHERE source_type = 'CONSULTATION' AND status = 'ACTIVE'`) on `charges.legacy_appointment_id`. The DB-enforced "at most one" guarantee; the mirror's own charge-creation uses `ON CONFLICT (legacy_appointment_id) WHERE source_type = 'CONSULTATION' AND status = 'ACTIVE' DO NOTHING` against exactly this index, not a caught `UniqueViolation` (see "A real concurrency bug found and fixed" below for why that distinction matters).
- `payments.method`'s CHECK widened to add `'WAIVED'` — the same additive-CHECK-widening pattern migration `0050` used to add `'DECLINED'` to `payments.status` for the structurally identical problem.
- `bill.record_payment` permission, granted to every existing role — closes the RBAC gap on `POST /appointments/{id}/payment` and `POST /appointments/{id}/bill/payments`, both previously bare-auth (`get_current_staff` only). Independent of the ADR-009 option; included here on its own merits.

### Reader migration status

| Reader | Status | Notes |
|---|---|---|
| `GET /dashboard/billing` — `collections_by_method`, `collections_by_doctor`, `total_collected` | **Ledger B, unified** | Reads `payments` (joined to `invoices`/`encounters`/`doctors`), across every charge type — the actual gap this phase closes. Uses "effective" amount (`amount - refunded_amount`) for `status = 'COMPLETED'` rows, the same convention `_compute_totals`/`get_invoice_summary_service` already use. |
| `GET /dashboard/billing` — `outstanding_unpaid` | Ledger A (documented exception) | A discrete UNPAID/FAILED *status word* with no Ledger B equivalent (an invoice has a balance, not a status word); also specifically about the consultation-fee gate ("who hasn't paid to be seen"), not the visit's full invoice balance. Follow-up work, not this phase's. |
| `GET /dashboard/billing` — `waivers` | Ledger A (documented exception) | No dollar total either way — Ledger A's own WAIVED write always records `payment_amount = 0` regardless of what was actually forgiven, unchanged by this phase. (Ledger B's own WAIVED-method mirror *does* have the real amount for a nonzero waiver; a future phase could read a real waived total from there — not done here, to keep this phase's diff to what was asked.) |
| `GET /dashboard/billing` — `refunds` | Ledger A (documented exception) | Consultation-fee refunds only. Ledger B has its own, separately-designed void/refund mechanism (`void_invoice_payment_service`) for invoice payments generally, not necessarily the same shape (void vs. partial refund) — merging the two risks misrepresenting what happened; left as a documented single-source view rather than guessing at an undefined unified semantic. |
| `get_payment_receipt_service` (printing) | Already Ledger B, unaffected | Takes a `payment_id` directly, already reads `invoices`/`charges`/`payments` exclusively. A legacy consultation payment now *has* a real `payment_id` it could print a receipt against (the mirror's own return value) — not yet wired to any frontend action in this phase; no regression (there was no consultation-fee receipt at all before). |
| `frontend/src/admin/BillingPanel.tsx` | No change needed | Its "Collected (last N days)" label was already generic, not "Consultation fees collected" — already correct for the now-unified total. Deliberately **not** copied from the sibling investigation branch's label rename (which renamed it *to* "Consultation fees collected" — the right call under an approach where the dashboard total stays consultation-only, the opposite of what this phase actually built). |
| `PaymentHistoryPanel.tsx` / `BillingHistoryPanel.tsx` | No change needed | Already Ledger-B-native (`GET /api/billing/payments` / invoices), already correctly worded ("every payment across every invoice"), unaffected by this phase. |

## A real concurrency bug found and fixed during this phase

The first implementation of the mirror's charge-creation caught `psycopg.errors.UniqueViolation` in Python and continued querying on the same cursor to find the existing charge. Under a genuine concurrent race (`tests/test_billing_ledger_coexistence_concurrency.py`'s direct-mirror-call test, which deliberately bypasses the appointment-row lock to exercise this path at all), this produced `psycopg.errors.InFailedSqlTransaction: current transaction is aborted, commands ignored until end of transaction block` — Postgres aborts the *whole transaction* on a constraint violation; a caught exception in application code doesn't undo that, and every subsequent statement on the same cursor fails until an explicit `ROLLBACK` (or `SAVEPOINT`/`ROLLBACK TO`). Left as originally written, this would have taken Ledger A's own just-executed UPDATE down with it on commit, under real concurrent load, for a caller the appointment-row lock doesn't currently protect against.

Fixed by switching to `ON CONFLICT (legacy_appointment_id) WHERE source_type = 'CONSULTATION' AND status = 'ACTIVE' DO NOTHING` — the same idiom `_ensure_invoice` (a few lines earlier in the same file) already uses for its own analogous race, which never raises in the first place. Verified by the same test, which passes cleanly after the fix.

In practice, every current call site (`record_payment_service`, `waive_consultation_fee_service`) locks the appointment row (`_lock_appointment_for_payment`, `FOR UPDATE`) before ever reaching the chokepoint, so this race cannot occur through the real API today — but the fix is defense-in-depth for the one case (a direct service-layer call, or a future caller that doesn't route through that lock) where it could, and the test that caught it is the *only* coverage this fallback branch has at all, since the API's own serialization means it's otherwise unreachable.

## Backfill

`migrations/0059_billing_ledger_coexistence_backfill.sql` mirrors every PAID/FAILED/REFUNDED Ledger A event that predates migration `0058`. Idempotent by `NOT EXISTS` guards (not `ON CONFLICT DO NOTHING` — a genuine duplicate-insert attempt should surface as a bug, not be silently absorbed); verified by `tests/test_billing_ledger_coexistence_backfill.py` running the migration's SQL twice and asserting 0 new rows the second time.

Permanently, deliberately excludes (see the migration's own header for the full reasoning) rather than inventing a value for:
- WAIVED/UNPAID rows — no real forgiven amount ever existed to backfill from (Ledger A's own WAIVED write has never recorded one), and waiving is a one-shot terminal action, so this is not a "will be picked up later" gap.
- A row with `encounter_id IS NULL` — nowhere in Ledger B to attach to (`invoices.encounter_id` is `NOT NULL UNIQUE`).
- A row with `payment_recorded_by IS NULL` — no real staff attribution to backfill `charges.created_by`/`payments.recorded_by` (both `NOT NULL`) from.
- A row with `payment_method IS NULL` on a PAID/FAILED event — `payments.method` is `NOT NULL`.
- A REFUNDED row whose `refund_amount` would exceed `payment_amount` — would violate `payments`' own `CHECK (refunded_amount <= amount)`; not expected to match any real row (the application never allows recording a refund larger than the payment), excluded defensively rather than assumed impossible.

Each exclusion is independently auditable via the query in the migration's own header comment.

## Reconciliation invariant

`tests/test_billing_ledger_reconciliation_gap.py`'s `RECONCILIATION_MISMATCH_SQL` (read-only, safe to run against production) — for every appointment:
- PAID with a nonzero amount must have a matching COMPLETED mirror payment (same method, same amount, `refunded_amount = 0`).
- FAILED with a nonzero amount must have a matching DECLINED mirror payment.
- REFUNDED must have a COMPLETED mirror payment whose `refunded_amount` matches Ledger A's `refund_amount`.
- WAIVED is checked only the other direction: *if* a `method = 'WAIVED'` mirror payment exists, it must be well-formed (`amount > 0`, `status = 'COMPLETED'`) — its *absence* is never flagged, since Ledger A's own WAIVED write cannot distinguish a real forgiven fee from an always-zero visit (both record `payment_amount = 0`), so there is no way to know from Ledger A alone whether a mirror was ever expected.

Exercised against all four states, including a FAILED-then-PAID retry (proving the retry correctly reuses the same mirrored charge rather than creating a second one), by `tests/test_billing_ledger_reconciliation_gap.py::test_reconciliation_invariant_holds_across_paid_failed_waived_refunded`, and against backfilled (not just live) data by `tests/test_billing_ledger_coexistence_backfill.py::test_backfill_reconciliation_is_clean_across_mixed_historical_states`.

## Evidence-based Option A vs. Option B comparison

| | Option A (`31e87f2`, PR #121, shelved) | Option B (this branch) |
|---|---|---|
| Queue-token trigger dependency | Rewired to read Ledger B (`EFFECTIVE_PAYMENT_*_SQL`) in the same phase | Untouched — still reads `appointments.payment_status` directly, exactly as before this phase |
| Files touched by the core mechanism | `appointment_services.py`, `billing_services.py`, `billing.py`, `appointments.py`, `dashboard.py`, `visit_completion_service.py` (6 files; the effective-payment SQL fragment threads through every consultation-fee reader) | `appointment_services.py`, `billing_services.py`, `appointments.py`, `dashboard.py` (4 files; `dashboard.py`'s change is additive — new queries, same response shape) |
| New concept introduced | `consultation_payment_id` FK on `appointments`, plus an "effective payment" SQL fragment repeated at every read site | Two correlation columns (`legacy_appointment_id` on `charges`/`payments`) plus one chokepoint function; no new column on `appointments` at all |
| What a regression in the mirror/cutover breaks | Every consultation-fee read *and* the queue-token trigger itself, in the same change | Only Ledger B's own view of consultation fees (Ledger A, and the queue-token trigger, are structurally untouched and cannot regress from a mirror bug — proven directly by `test_invoice_voided_mid_payment_rolls_back_ledger_a_too`, where a forced mirror failure correctly rolls back cleanly rather than leaving Ledger A in a bad state) |
| Test suite result (own branch, full run) | 802 passed, 1 pre-existing unrelated failure | 803 passed, 2 pre-existing unrelated failures (both reproduced identically against pristine `a63f2dc`, both scheduling/timezone, neither touching billing code — see "Pre-existing failures" below) |
| Concurrency bug surfaced during implementation | Three test failures fixed during implementation (settle-free-visit/refund/report-window edge cases — see PR #121's own report) | One real bug (`InFailedSqlTransaction` from a caught `UniqueViolation` mid-transaction) — found by a concurrency test written specifically because the alternative (no dual-write, Option A) can't have this class of bug at all; fixed before merge, see above |
| Reversibility if something is wrong post-ship | Reverting means un-rewiring the queue-token trigger's dependency — the highest-blast-radius single action in this codebase | Reverting means dropping two nullable columns and no longer calling one function — Ledger A was never touched |

**Reading this table honestly**: Option A is not worse engineering — it passed its own full suite and is a real, working cutover. The evidence above is about *risk shape*, not correctness: Option B trades a larger total surface area (two ledgers to keep in sync, forever, until the final cutover) for never putting the queue-token trigger's dependency and the two-ledger mapping's first real-world validation in the same change. That trade is what ADR-009 actually decided, and it is why Option A is preserved on its own branch rather than discarded — it is the reference implementation for the eventual final cutover once Option B's coexistence has run long enough to prove the mapping.

## Pre-existing failures (reproduced against `a63f2dc`, not touched this phase)

Two test failures observed in this phase's full-suite runs are **not** caused by Phase 10B — reproduced identically, at the same wall-clock time, against a clean checkout of `a63f2dc` (the pre-Phase-10B baseline, via `git worktree add`) with none of this phase's code present:

- `tests/test_date_first_scheduling.py::test_date_first_lists_multiple_doctors_with_their_own_slot_counts` — asserts `slot_count == 16`, got `8`.
- `tests/test_queue_tokens.py::test_queue_visited_at_is_doctor_local_time_not_utc` — a `KeyError: 'id'` from an upstream `409 Conflict` on appointment creation.

Both are in pure scheduling/slot-availability code with no billing/payment involvement, and both are time-of-day-sensitive (reproduced consistently at the same point in the day, both runs) — almost certainly the same class of bug as the DST/"already past 9:30am" test-authoring issue found during this phase's own investigation of `test_booking_uses_doctor_specific_timezone` (a hardcoded expected hour that breaks once "today" has passed a certain wall-clock time in the test's target timezone), just manifesting in two different tests. Per this phase's explicit scope (**do not modify scheduling in this phase**), recorded here as a follow-up finding, not fixed.

## Open items (explicitly out of this phase's scope)

- `outstanding_unpaid`/`waivers`/`refunds` staying Ledger-A-sourced is a deliberate, documented choice for this phase, not a final answer — a future phase could design a genuine unified "amount waived" (reading Ledger B's own WAIVED-method payments) and a genuine unified "outstanding balance" (reading invoice balances directly) if the front-desk questions they answer turn out to need that.
- No frontend action currently prints a receipt for a legacy consultation payment, even though one is now representable (`get_payment_receipt_service` against the mirror's `payment_id`). Not a regression (nothing did this before); a real, low-risk follow-up.
- The two pre-existing scheduling test failures above (not billing-related; recorded, not fixed, per this phase's explicit scope).
- The final cutover to "Ledger B only" (ADR-009's stated final target) — deliberately not attempted here; see the ADR's own "Final target architecture" section for the preconditions.
