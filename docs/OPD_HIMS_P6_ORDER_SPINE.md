# OPD/HIMS Master Spec — Phase 6: Order Spine

Follows `docs/OPD_HIMS_P5_TRIAGE_CONSULTATION.md`.

## Scope

The master spec's Phase 6 goal is "connect clinical consultation to
investigations/services," exit criterion "doctor creates an order and
downstream workspace sees the SAME order; no duplicated order records."
Implemented: one generic `orders` table and a creation/list/cancel
lifecycle, reachable from a new "Orders" tab in `ConsultationWorkspace`.

Explicitly **not** in this phase (Phase 7's job per the master spec's own
plan): the actual lab/radiology processing workflow (collection →
processing → result → verification → release), structured lab
results/radiology reports, and a test/service catalog to pick orders
from by search rather than free text. This phase builds the spine an
order travels on, not the departments at the far end of it.

## What changed

**One table, not one per order type.** `orders.order_type` (`LAB`,
`RADIOLOGY`, `PROCEDURE`, `SERVICE`, `EXTERNAL_REFERRAL`) distinguishes
them, matching the master spec's Principle 3 verbatim ("Do NOT create
disconnected modules... the encounter determines whether it originated
from OPD/IPD/Emergency") applied one level down: the *order type*
distinguishes a lab test from a radiology study, not a separate table.
`external_destination` is required (checked in the service layer, with
a DB CHECK as backstop) only when `order_type = 'EXTERNAL_REFERRAL'` —
this is how master spec §31 ("the patient journey must not break
because an internal module is disabled") gets satisfied structurally: a
doctor can always order an external referral, lab/radiology internal or
not.

**Two different write gates, deliberately.** Creating an order requires
`CHECKED_IN`, same discipline as vitals/consultation (Phase 5). But
**cancelling** an order is *not* gated on appointment status — a wrong
order needs to stay cancellable even after the front desk closes the
visit, since it may not have reached a lab yet. This is a real,
considered difference from Phase 5's uniform gate, not an inconsistency:
Phase 5's gate protects *documentation* (which should be finished before
the visit closes), this one protects the ability to *correct a mistake*
(which has no such deadline). Covered by
`test_cancel_order_after_visit_completed_still_works`.

**Status lifecycle**: only `ORDERED` (on create) and `CANCELLED`
(explicit action, reason required — master spec §90) are ever set by
this phase's code. `IN_PROGRESS`/`COMPLETED` exist in the schema for
Phase 7's real lab/radiology workflow to use, but nothing here sets
them — no placeholder "mark in progress" button with no workflow behind
it.

## Files changed

- `migrations/0030_orders.sql` — new.
- `app/services/order_services.py` — new.
- `app/services/clinical_services.py` — `get_appointment_status_and_doctor`/
  `get_encounter_id_for_appointment` un-prefixed (were module-private,
  now shared with `order_services.py`) — a pure rename, not a behavior
  change.
- `app/services/exceptions.py` — `ExternalReferralDestinationRequired`,
  `OrderNotFound`, `OrderNotCancellable`.
- `app/api/orders.py` — new router; registered in `app/main.py`.
- `tests/conftest.py` — `orders` added to `APP_TABLES`.
- `tests/test_orders.py` — new, 11 tests.
- `frontend/src/types.ts` — `OrderType`, `OrderPriority`, `OrderStatus`,
  `ClinicalOrder`, `OrderInput`.
- `frontend/src/api.ts` — `listOrders`, `createOrder`, `cancelOrder`.
- `frontend/src/admin/ConsultationWorkspace.tsx` — new "Orders" tab:
  create form + list with cancel (reusing the same required-reason
  inline-form pattern `QueueSection.tsx`'s priority flag already uses).
- `frontend/src/styles.css` — `.pill.status-ordered`/`.status-in_progress`
  (`.status-completed`/`.status-cancelled` already existed and are
  reused as-is).

## Database changes

`orders` only (see migration for full column-level reasoning). Zero
changes to any existing table.

## API changes

New, under the existing `/appointments/{id}/...` prefix:
`GET/POST .../orders`, `POST .../orders/{order_id}/cancel`. Same staff
auth requirement as every other clinical endpoint.

## Tests

11 new (`tests/test_orders.py`): creation gated on CHECKED_IN, a lab
order's fields round-trip correctly, external referral requires (and
accepts) a destination, list returns newest-first, orders remain
listable after the visit completes while *new* orders are then blocked
(mirroring Phase 5's own test for consultations), cancel requires a
reason, cancel succeeds and records who/why/when, cancel still works
after the visit completes (the deliberate exception to the CHECKED_IN
gate), double-cancel is rejected, and auth is enforced.

Full suite: 452 passed (441 existing + 11 new), 1 skipped, 1
pre-existing failure unrelated to this change (same one flagged in
every prior phase report).

**Browser-verified**: ran the app locally, opened a checked-in patient's
Consultation workspace, added a LAB order and an EXTERNAL_REFERRAL order
(with destination), confirmed both appear with correct type/priority/
status badges, cancelled one with a reason, confirmed the cancelled
row shows its reason inline and loses its Cancel action while the other
order's Cancel button stays available.

## Known gaps / deliberately out of scope

- **No test/service catalog.** `description` is free text (see "Scope"
  above) — master spec §30's "[Search test/service]" UI needs a real
  catalog behind it, which doesn't exist yet.
- **No downstream workflow.** An order sits at `ORDERED` until manually
  cancelled; nothing moves it to `IN_PROGRESS`/`COMPLETED` or attaches a
  result yet (Phase 7).
- **No printable external-referral requisition.** Master spec §31/§54
  wants a print view for it; not built here (printing generally is
  listed as a Phase 12 hardening concern in the master spec, not Phase 6).
- **Orders tab lives inside `ConsultationWorkspace`, not a standalone
  "Orders" full workspace** (master spec §9 lists it as one). A
  dedicated cross-encounter Orders workspace makes more sense once
  Phase 7's worklists exist to route into — building it now would have
  nothing real to link to.

## Next phase

Phase 7 (Diagnostics) — the first phase that gives `orders` a real
downstream: a laboratory/radiology work queue that lists `ORDERED`
orders by type (the `orders_open_by_type_idx` index this phase already
added exists for exactly that query), moves them through
`IN_PROGRESS`/`COMPLETED`, and attaches results the doctor can see from
the same encounter.
