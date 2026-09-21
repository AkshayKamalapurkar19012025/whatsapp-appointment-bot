# OPD/HIMS Master Spec — Phase 8: Prescription + Pharmacy

Follows `docs/OPD_HIMS_P7_DIAGNOSTICS.md`.

## Scope

The master spec's Phase 8 goal is "connect clinical prescription to
dispensing," exit criterion "Doctor → Prescription → Pharmacy → Dispense
→ Billing works without duplicate medication records." Implemented:
prescription authoring (draft → sign & send to pharmacy → cancel), a
cross-patient pharmacy queue, and inventory-aware dispensing (stock
batches, decremented via a real transaction log, "where available" per
master spec section 37). "→ Billing" is deliberately **not** wired into
the existing `invoice_line_items`/`payment_status` mechanism — see
"Billing" below for why, and what Phase 9 needs to do instead.

## What changed

**Two new clinical entities, same shape as before.** `prescriptions`
(one per encounter, DRAFT/PRESCRIBED/CANCELLED — mirrors `consultations`
exactly) and `prescription_items` (many per prescription — mirrors how
`order_results` relates to `orders`). "Partially Dispensed"/"Dispensed"
(master spec section 35's prescription-level lifecycle) are **computed**
from `quantity_dispensed`/`quantity`, never stored — the same choice
made for `encounters.status` in Phase 3: a derived value can't drift out
of sync with the facts it's derived from.

**Inventory is real, not a status flip.** `pharmacy_stock` (batches,
expiry, a denormalized running `quantity_on_hand`) and
`pharmacy_dispense_records` (the transaction/movement log that keeps it
correct) are the master spec's own suggested schema shape (section 59:
"pharmacy_dispensing, pharmacy_stock"). "Do not simply mark medication
dispensed without a transaction" (section 37) is exactly what
`record_dispense_service` does: every dispense is a row, in the same
transaction as the stock decrement and the item's running-total update.
Stock is optional per dispense ("stock-aware... where available") — a
dispense with no matching batch still records a real transaction, just
without an inventory decrement behind it.

**"No unauthorized substitution" is a checked rule, not an absent
feature.** There's no substitution UI at all, but that alone doesn't
prevent a pharmacist from picking the wrong stock row by accident —
`record_dispense_service` compares the chosen batch's `medicine_name`
against the prescription item's and refuses a mismatch
(`MedicineMismatch`). Verified by `test_dispense_rejects_mismatched_
medicine`.

**The CHECKED_IN-gating pattern extends cleanly to a third clinical
entity.** Writing a DRAFT prescription (add/remove item, sign & send)
requires CHECKED_IN — live-visit documentation, same as consultations
and orders. Cancelling a sent prescription is not gated (same as
cancelling an order) but *is* blocked once anything has been dispensed
against it — money/medicine already handed over can't be undone by
cancelling the prescription record. Dispensing itself is not gated on
CHECKED_IN either: the master spec's own Visit Completion checklist
(section 43) requires only "Prescription created", not "dispensed" —
a patient can legitimately still be at the pharmacy counter after
front-desk has closed out the visit. Verified by
`test_dispense_is_not_gated_on_checked_in`.

**Billing.** `pharmacy_dispense_records.amount` is a real, persisted
financial fact (quantity × unit price, from the stock batch when linked)
— but it is **not** pushed into `invoice_line_items` the way earlier
phases might have. `add_invoice_line_item_service` only accepts a new
charge while `payment_status` is UNPAID/FAILED (migrations/0026's own
stated invariant: the bill freezes once paid). In the common OPD flow,
the consultation fee is paid at check-in — before the doctor is even
seen — so by the time pharmacy dispenses (the last stop), the bill has
almost always already frozen. Forcing pharmacy charges through that
mechanism would 409 in the ordinary case, not the exception. Reconciling
per-dispense amounts into one real, multi-charge invoice that isn't
frozen on first payment is explicitly master spec Phase 9's job
("service charges, invoice, discounts... payments"), not something to
force prematurely onto today's single-fee model.

## Files changed

- `migrations/0032_prescriptions_and_pharmacy.sql` — new (four tables:
  `prescriptions`, `prescription_items`, `pharmacy_stock`,
  `pharmacy_dispense_records`).
- `app/services/pharmacy_services.py` — new.
- `app/services/exceptions.py` — 9 new exceptions for this phase.
- `app/api/pharmacy.py` — new: `prescription_router` (nested under
  `/appointments/{id}/prescription...`, same convention as clinical/
  orders) and `pharmacy_router` (`/pharmacy/...`, cross-patient).
- `tests/conftest.py` — 4 new tables added to `APP_TABLES`.
- `tests/test_pharmacy.py` — new, 21 tests.
- `frontend/src/types.ts` — `Prescription`, `PrescriptionItem`,
  `PharmacyQueueEntry`, `PharmacyStockBatch`, and their input types.
- `frontend/src/api.ts` — prescription + pharmacy client functions.
- `frontend/src/admin/PrescriptionPanel.tsx` — new: the Consultation
  workspace's Prescription tab (add/remove medicine, send to pharmacy,
  cancel).
- `frontend/src/admin/PharmacyPanel.tsx` — new: a standalone, top-level
  "Pharmacy" workspace (Queue + Stock tabs), reached directly from the
  sidebar like Billing — unlike Queue/Consultation, which are reached
  only as actions.
- `frontend/src/admin/AdminApp.tsx` — new `pharmacy` section + sidebar
  entry.
- `frontend/src/admin/ConsultationWorkspace.tsx` — new Prescription tab.
- `frontend/src/styles.css` — `.pill.status-partially_dispensed`/
  `.status-dispensed`.

A note on `ConsultationWorkspace.tsx`'s size: it was already over 1,000
lines before this phase (Triage/Consultation/Orders all inlined
directly). Rather than add a fourth tab's worth of markup into that same
file, `PrescriptionPanel` is a self-contained component that fetches its
own data when its tab is opened, instead of being threaded through the
parent's combined initial load. This is a deliberate, one-off deviation
from the prior tabs' pattern, not an oversight — the file was already
large enough to make growing it further the wrong default, and
`PrescriptionPanel`'s state genuinely doesn't need to be shared with the
other tabs. Splitting Triage/Orders out the same way is a reasonable
future cleanup, not required by anything in this phase.

## Database changes

`prescriptions`, `prescription_items`, `pharmacy_stock`,
`pharmacy_dispense_records` (see migration for full column-level
reasoning). Zero changes to any existing table.

One local-only correction during development: the first draft of
`prescriptions`' CHECK constraint required `prescribed_at IS NULL`
whenever `status <> 'PRESCRIBED'`, which broke cancelling a prescription
(status moves to CANCELLED, but `prescribed_at` correctly stays set as
the historical record of when it was sent). Caught immediately when the
first cancel test hit a `CheckViolation` locally; fixed in place before
this migration was ever committed or pushed (per this repo's own rule,
only edited in place because it had never left this machine — see the
migration's own comment for the corrected invariant).

## API changes

New, under `/appointments/{id}/prescription...`: `GET`/`POST items`/
`DELETE items/{id}`/`POST prescribe`/`POST cancel`. New, under
`/pharmacy/...`: `GET queue`, `GET`/`POST stock` (POST is ADMIN-only,
same tier as recurring schedule management), `POST items/{id}/dispense`.

## Tests

21 new (`tests/test_pharmacy.py`): draft creation gated on CHECKED_IN;
add/remove items; item set frozen once PRESCRIBED; prescribing requires
at least one item (422); cancel requires PRESCRIBED and blocks once
anything is dispensed; dispensing requires PRESCRIBED, computes
PENDING/PARTIALLY_DISPENSED/DISPENSED correctly across a partial-then-
full sequence, refuses to over-dispense (422), and is *not* blocked by
the visit having closed (mirroring Phase 6/7's own cancel/result tests);
stock creation is ADMIN-only, decrements correctly on dispense, rejects
a mismatched medicine and insufficient stock, and rejects a duplicate
batch (409); the pharmacy queue lists prescribed-with-pending-items and
drops a prescription the moment it's fully dispensed, and never lists a
still-DRAFT one.

Full suite: 480 passed (459 existing + 21 new). Two failures, both
pre-existing and unrelated to this phase's diff (neither touches
pharmacy/prescription/order/clinical code at all):
`test_date_first_lists_multiple_doctors_with_their_own_slot_counts`
(flagged in every prior phase report) and, newly observed in this run,
`test_scheduling_flow.py::test_reschedule_flow_cancels_old_and_books_new`.
Root-caused before writing this off: it fails deterministically when run
alone, with the WhatsApp reschedule flow stuck on `RESCHEDULE_SLOT`
instead of advancing after picking the 3rd offered slot — consistent
with the doctor's seeded 09:00-17:00 window having fewer than 3 slots
left for "today" in Asia/Kolkata by the time this long session reached
this point in real wall-clock time (this session ran many real hours
across all 8 phases). This is the same category of environmental,
clock-dependent fragility as the already-known failure, just triggered
by time-of-day rather than day-of-week; nothing in `git diff` for this
phase touches scheduling, availability, or the WhatsApp booking flow.

**Browser-verified end-to-end**: ran the app locally, opened a
checked-in patient's Prescription tab, added Paracetamol (500mg,
1-0-1, 5 days, qty 10), sent it to pharmacy, confirmed it appeared
correctly in the new Pharmacy workspace's queue. Added a stock batch
(50 units, ₹2.50), dispensed 4 units against it (confirmed the item
flipped to "Partially Dispensed" and stock correctly decremented to
46 via a direct API check), then dispensed the remaining 6 with no
stock batch (untracked path) and confirmed the prescription dropped
off the queue entirely once fully dispensed.

## Known gaps / deliberately out of scope

- **No PHARMACIST role.** Same gap already flagged for NURSE/DOCTOR
  (Phase 5) and implicitly for LAB_TECH (Phase 7) — any authenticated
  staff session can dispense today.
- **Billing not unified.** See "What changed" above — Phase 9's job.
- **No medicine catalog.** `medicine_name` is free text on both
  prescription items and stock batches (matched by exact
  case-insensitive string, not a foreign key) — same "no catalog yet"
  stance as orders' test/service descriptions (Phase 6).
- **No Preparing/Ready pharmacy sub-states.** A prescription is either
  in the queue (something pending) or not — no intermediate workflow
  stages, matching the same discipline Phase 7 applied to the lab
  worklist (no fake stages with nothing driving them).

## Next phase

Phase 9 (Billing + Payment) per the master spec's own sequencing — the
phase that finally needs to reconcile consultation fees, order charges
(none billed yet either — Phase 6/7 didn't touch billing at all), and
now pharmacy dispense amounts into one real, multi-charge invoice model
that survives an early payment rather than freezing on it.
