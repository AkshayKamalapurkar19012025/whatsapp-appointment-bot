# OPD/HIMS Master Spec — Phase 7: Diagnostics

Follows `docs/OPD_HIMS_P6_ORDER_SPINE.md`.

## Scope

The master spec's Phase 7 goal is "implement basic operational lab/
radiology workflows," exit criterion "doctor creates order and later
sees its result from the same encounter." The word doing the real work
in that goal is "basic" — the master spec's own sections 32/33 describe
a 6-state-per-type workflow (lab: Pending Collection → Collected →
Processing → Result Pending → Verified → Released; radiology: Ordered →
Scheduled → In Progress → Reporting → Reported → Verified) with a
two-person technician/verifier sign-off. This phase does not build
that. It builds the part the exit criterion actually asks for: a
structured result gets attached to an order, and the doctor sees it
from the same encounter — deliberately not a full lab-department
operations system with its own staff role, worklist, and compliance
sign-off chain, none of which exist yet in this codebase (no LAB_TECH/
pathologist role — the same gap already flagged for NURSE/DOCTOR in
Phase 5, now a third consistent instance of it).

## What changed

**One `order_results` table, not one per order type.** Same principle
Phase 6 applied to `orders` itself: `parameter` distinguishes a lab
panel's individual values (e.g. "Hemoglobin", with `unit`/
`reference_range` meaningful) from a radiology report's narrative
sections (e.g. "Findings", "Impression", with those columns simply
unused) from a plain procedure/service/external-referral note — not a
`lab_results` table plus a separate `radiology_reports` table.

**Recording a result is a single batch**, not a field built up over
several calls: a lab panel or radiology report arrives complete, so the
result-entry endpoint takes a list of items and, in the same
transaction, transitions the order straight to `COMPLETED`. No
intermediate `IN_PROGRESS` step is set by this phase's code — that
would have meant a "mark in progress" button with no real operational
process behind it, the same discipline Phase 6's report already
committed to.

**Third point on the CHECKED_IN-gating spectrum**, and this one settles
the pattern rather than complicating it: consultation/vitals writes
(Phase 5) are gated on CHECKED_IN because documentation belongs to the
live visit; order creation (Phase 6) is gated the same way for the same
reason; order cancellation (Phase 6) and now result-recording are both
*not* gated, because both are actions that legitimately, routinely
happen after the visit has closed — a result especially, since a lab
send-out test can come back hours or days later (the master spec's own
Patient 360 timeline example shows exactly this: a result released well
after check-in). Verified by
`test_recording_a_result_is_not_gated_on_checked_in`.

**Results are embedded on every order the API returns**, not fetched
separately — `list_orders_service` now attaches each order's `results`
(empty list if none) in the same round trip, and `create_order_service`/
`cancel_order_service` both include an empty `results: []` for shape
consistency, so the frontend never has to special-case a missing key
after an optimistic local update. This is what lets a doctor see a
result "from inside consultation" (master spec section 34) without a
second navigation.

## Files changed

- `migrations/0031_order_results.sql` — new.
- `app/services/order_services.py` — `record_order_result_service`,
  `list_order_results_service`; `list_orders_service` now embeds
  results; `create_order_service`/`cancel_order_service` now include
  `results: []` for shape consistency; updated module docstring.
- `app/services/exceptions.py` — `OrderNotResultable`.
- `app/api/orders.py` — `POST .../orders/{order_id}/result`,
  `OrderResultItem`/`OrderResultCreate` request models.
- `tests/conftest.py` — `order_results` added to `APP_TABLES`.
- `tests/test_order_results.py` — new, 7 tests.
- `frontend/src/types.ts` — `OrderResultItem`, `OrderResultItemInput`;
  `ClinicalOrder.results`.
- `frontend/src/api.ts` — `recordOrderResult`.
- `frontend/src/admin/ConsultationWorkspace.tsx` — the Orders tab now
  has a per-order "Record result" action (a dynamic multi-parameter
  entry form) and displays recorded results in a nested table, with a
  danger-red "Critical" / warning "Abnormal" badge.

## Database changes

`order_results` only (see migration for full column-level reasoning).
Zero changes to any existing table.

## API changes

New: `POST /api/appointments/{id}/orders/{order_id}/result`, taking
`{"items": [{"parameter", "result_value", "unit"?, "reference_range"?,
"is_abnormal"?, "is_critical"?}]}` (at least one item required). The
existing `GET .../orders` now returns a `results` array on every order.

## Tests

7 new (`tests/test_order_results.py`): recording a result completes the
order and returns it with the items attached; at least one item is
required (422); recording is *not* blocked by the visit having closed
(the deliberate exception, mirrored from Phase 6's cancel test);
recording is blocked for a cancelled order and for an order that
already has a result (409 in both cases); auth is enforced; and listing
orders embeds each one's results correctly (including an order with no
results yet, returning `[]` rather than a missing key).

Full suite: 459 passed (452 existing + 7 new), 1 skipped, 1
pre-existing failure unrelated to this change (same one flagged in
every prior phase report).

**Browser-verified**: ran the app locally, opened a checked-in patient's
Consultation workspace's Orders tab, clicked "Record result" on an
existing lab order, added two parameters (Hemoglobin — normal; Platelets
— flagged both Abnormal and Critical), saved, and confirmed the order
flipped to `COMPLETED`, the Actions column correctly emptied out, and
the results table rendered underneath with a "CRITICAL" badge on the
Platelets row (and, correctly, no separate "Abnormal" badge alongside
it — critical takes visual precedence over abnormal rather than
stacking two badges for the same underlying signal).

## Known gaps / deliberately out of scope

- **No lab/radiology worklist screen.** There's no "Laboratory work
  queue" or "Radiology worklist" listing `ORDERED` orders across
  patients for a technician to work through — `orders_open_by_type_idx`
  (added in Phase 6, unused until now) exists for exactly that query,
  but the screen itself needs a real LAB_TECH/radiology role to be
  worth building, which doesn't exist.
- **No technician/verifier two-step sign-off.** `recorded_by` captures
  who entered the result; there is no separate verification/release
  step. Real future work once a role model supports it, not simulated
  here.
- **No amendment workflow.** Once an order has a result and is
  `COMPLETED`, nothing can be added or corrected (`OrderNotResultable`).
  Consistent with every other "no amendment yet" stance in this codebase
  (Phase 5's consultations).
- **No critical-result alerting.** A critical flag is shown visually on
  the result row; there's no notification/exception-engine push (master
  spec's Exception Engine is Phase 11 territory, not this one).

## Next phase

Phase 8 (Prescription + Pharmacy) per the master spec's own sequencing.
Diagnostics (Phase 7) and the order spine (Phase 6) together now give
the Consultation workspace's "PLAN" section two of its three real
actions (Lab/Radiology/Procedure orders, External Referral); Phase 8
adds the third (Prescription), and pharmacy dispensing downstream of it.
