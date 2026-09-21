# OPD/HIMS Master Spec — Phase 9: Billing + Payment

Follows `docs/OPD_HIMS_P8_PRESCRIPTION_PHARMACY.md`.

## Scope

The master spec's Phase 9 goal is to finally reconcile everything
Phases 6-8 left unbilled — lab/radiology/procedure orders, pharmacy
dispenses, ad-hoc service charges — into one real, multi-charge invoice
model, with discount/tax and multiple/partial payments. Implemented: a
new, encounter-scoped `invoices`/`charges`/`payments` model; an
"unbilled sources" helper that lists every order and pharmacy dispense
for this visit that doesn't yet have a charge; discount and tax applied
at the invoice level; partial/full/multiple payments with balance
tracking; void/refund correction paths for every layer (charge,
payment, whole invoice).

**Deliberately NOT unified with the existing
`appointments.consultation_fee`/`payment_status`/`payment_amount`
mechanism** (migrations 0018/0019/0025/0026, `record_payment_service`/
`waive_consultation_fee_service`). That mechanism is directly wired to
`generate_queue_token_service` — the actual "patient enters the queue"
trigger — and is the single most concurrency-sensitive, heavily-tested
payment path in this app. Rewriting it to absorb Phase 6-8's charges
was considered and rejected: the risk of destabilizing queue-token
issuance outweighed the benefit of a single billing table this early.
Phase 9's new model coexists alongside it instead, scoped to the same
encounter but tracked independently — verified directly by
`test_billing_independent_of_existing_consultation_payment_flow`.

## What changed

**One invoice per encounter, get-or-create.** `invoices.encounter_id`
is `UNIQUE`; `GET .../bill` creates one on first access if none exists
yet (race-safe via `INSERT ... ON CONFLICT DO NOTHING` + re-SELECT).
Callable **before check-in** — billing is an administrative/financial
function, not clinical documentation, so unlike every other clinical
write in this app it carries no CHECKED_IN gate at all. Verified by
`test_get_invoice_creates_one_even_before_check_in`.

**One generic `charges` table, not one per source** (master spec
Principle 3, same pattern as `orders`/`order_results`). `source_type`
discriminates CONSULTATION/LAB/RADIOLOGY/PROCEDURE/SERVICE/PHARMACY/
OTHER; `source_order_id`/`source_dispense_id` optionally trace a charge
back to the order or pharmacy dispense that generated it, each with a
partial unique index so the same order or dispense can be billed
**exactly once**. Adding a charge validates the source belongs to
*this* encounter, not just any encounter (`InvalidChargeSource`) —
verified by `test_charge_linked_to_order_from_a_different_encounter_
is_rejected`.

**"Aggregate applicable charges" is a real query, not a manual lookup.**
`list_unbilled_sources_service` finds every non-cancelled order and
every pharmacy dispense for this encounter that has no linked charge
yet (`LEFT JOIN charges ... WHERE c.id IS NULL`), so billing staff pick
from an actual list instead of retyping descriptions from memory. No
price catalog exists (same "no catalog yet" stance as Phase 6-8), so
amounts stay staff-entered — the frontend now prompts for one rather
than silently omitting it (see "Errors and fixes" below).

**Totals are computed at read time, never stored.** `_compute_totals`
applies discount before tax (standard invoicing order), derives
`taxable_amount`/`tax_amount`/`net_amount`/`balance`/`payment_status`
fresh from ACTIVE charges and COMPLETED-minus-refunded payments on
every read — the same "computed-not-stored" discipline already used for
`encounters.status` (Phase 3) and dispense status (Phase 8), so there's
no second, driftable copy of "how much is owed." Verified numerically
by `test_discount_and_tax_computed_correctly` (₹1000 gross, ₹100
discount, 18% tax → ₹900 taxable, ₹162 tax, ₹1062 net).

**Duplicate-payment protection, two layers.** A same-amount duplicate
click is naturally rejected because the first payment already zeroes
the balance (`PaymentExceedsBalance`) — verified by
`test_double_click_payment_is_rejected_by_balance_check`. A genuinely
re-submitted external reference (UPI/card/bank transaction ID) is
caught by a DB-level partial unique index on `payments.transaction_id`,
surfaced as a distinct `DuplicateTransactionId` (not conflated with the
balance check — see "Errors and fixes").

**Correction paths mirror the OLD system's existing RBAC tiers.**
Recording a payment and viewing the bill are STAFF-permitted (day-to-day
cashier work, same tier as the old `record-payment`); adding/voiding a
charge, editing discount/tax, voiding/refunding a payment, and voiding
the whole invoice are ADMIN-only — the same tier the OLD system already
uses for `add_appointment_invoice_line_item`/`waive-payment`/
`record-refund`. An invoice can only be voided as a whole *before* any
payment exists (`InvoiceNotVoidable` once one does) — once money has
actually changed hands, individual charges/payments are corrected
instead, never the whole bill wiped away.

## Files changed

- `migrations/0033_billing_invoices.sql` — new (three tables:
  `invoices`, `charges`, `payments`).
- `app/services/billing_services.py` — new.
- `app/services/exceptions.py` — 12 new exceptions for this phase.
- `app/api/billing.py` — new: `router`, nested under
  `/appointments/{id}/bill...` (see "Errors and fixes" for why `/bill`,
  not `/invoice`).
- `app/main.py` — `billing_router` import + registration.
- `tests/conftest.py` — `invoices`/`charges`/`payments` added to
  `APP_TABLES`.
- `tests/test_billing_invoices.py` — new, 21 tests.
- `frontend/src/types.ts` — `BillSummary`, `BillCharge`, `BillPayment`,
  `UnbilledSources`, and their input types (named `Bill*`, not
  `Invoice*` — see below).
- `frontend/src/api.ts` — `getBill`/`getUnbilledSources`/
  `updateBillTerms`/`voidBill`/`addBillCharge`/`voidBillCharge`/
  `recordBillPayment`/`voidBillPayment`/`refundBillPayment`.
- `frontend/src/admin/AppointmentBillingPanel.tsx` — new: the
  Consultation Workspace's Billing tab (totals, charges, unbilled
  sources with one-click "Bill this", payments, discount/tax edit,
  void-charge/void-payment/void-bill).
- `frontend/src/admin/ConsultationWorkspace.tsx` — new Billing tab,
  reachable both from the normal tab bar (CHECKED_IN visits) and from
  the "not checked in yet" state (billing has no CHECKED_IN gate — see
  "What changed").
- `frontend/src/admin/AdminApp.tsx` — threads `isAdmin` into
  `ConsultationWorkspace` (Billing's admin-only actions need it, the
  same way `DoctorsPanel`/`DepartmentsPanel`/etc. already receive it).

**Naming note:** the new component is `AppointmentBillingPanel.tsx`,
not `BillingPanel.tsx` — that name is already taken by the existing
dashboard billing-reconciliation page (`admin/BillingPanel.tsx`, wired
to `GET /dashboard/billing`, nav entry "Billing"). Caught before commit
by `tsc` failing on the pre-existing `<BillingPanel key={navResetKey} />`
usage in `AdminApp.tsx` after a same-name file was written over it by
mistake during development; the dashboard component was restored from
git history and the new one renamed. Flagging this explicitly since it
is exactly the kind of silent-clobber mistake worth naming so a future
reader trusts the diff rather than wondering why two "billing" panels
exist.

## Database changes

`invoices`, `charges`, `payments` (see migration header for full
column-level reasoning, including the invoice/receipt numbering scheme
using `BILL-`/`RCPT2-` prefixes, visually distinct from the existing
system's `INV-` numbers). Zero changes to any existing table.

## API changes

New, under `/appointments/{id}/bill...`: `GET`, `PATCH` (discount/tax,
ADMIN), `POST void` (ADMIN), `POST charges` (ADMIN), `POST
charges/{id}/void` (ADMIN), `POST payments` (STAFF), `POST
payments/{id}/void` (ADMIN), `POST payments/{id}/refund` (ADMIN), `GET
unbilled` (STAFF).

## Errors and fixes

- **Route collision, caught by this phase's own tests before commit.**
  The endpoints were first written under `/{appointment_id}/invoice...`,
  which exactly collides with the pre-existing `GET
  /{appointment_id}/invoice` in `app/api/appointments.py` (the OLD
  single-fee billing system). Since `appointments_router` is registered
  in `main.py` before the new billing router, FastAPI silently matched
  the older, already-mounted route every time — the new endpoint was
  never reached at all. Symptom: 3 of 21 new tests failed with
  `KeyError: 'gross_amount'` because responses were coming back in the
  OLD system's shape. Fixed by renaming the whole new resource path to
  `/bill`, not `/invoice`, throughout the API, the frontend, and the
  tests, and documenting the collision explicitly in `billing.py`'s
  module docstring so it reads as a deliberate naming choice, not an
  accident.
- **Frontend filename collision** (see "Files changed" above) — an
  existing `admin/BillingPanel.tsx` was overwritten during development
  by the new component sharing the same name; caught by `tsc -b`
  failing against `AdminApp.tsx`'s pre-existing usage, restored from
  git history, new component renamed to `AppointmentBillingPanel.tsx`.
- **"Bill this" quick-action 422.** The unbilled-orders list has no
  catalog price to prefill (orders, unlike pharmacy dispenses, carry no
  persisted amount), and the first version of the "Bill this" button
  silently sent `amount: undefined` whenever the adjacent add-charge
  form's amount field was empty — a real, reachable bug, not just a
  theoretical one, hit live during browser verification (see below).
  Fixed by prompting for an amount before submitting and validating it
  client-side (`> 0`), instead of forwarding a request the backend
  would 422 on.
- **Payment-exceeds vs. duplicate-transaction conflation.** The first
  draft of `record_invoice_payment_service` caught the `transaction_id`
  unique-index violation and re-raised `PaymentExceedsBalance` —
  semantically wrong (a duplicate reference isn't "too much money").
  Added a distinct `DuplicateTransactionId` exception and a matching
  409 in the API instead of a 422.

## Tests

21 new (`tests/test_billing_invoices.py`): invoice get-or-create works
before check-in and is idempotent; adding a charge requires ADMIN (403
for STAFF) and updates gross/balance; a charge linked to another
encounter's order is rejected (422); billing the same order twice is
rejected (409); unbilled sources lists orders and drops them once
billed; voiding a charge removes it from the gross; discount/tax
computed correctly (numeric check); updating terms requires ADMIN;
partial payment then full settlement transitions PARTIALLY_PAID → PAID
correctly; a payment exceeding the balance is rejected (422); a
double-click duplicate payment is rejected by the same balance check
(422); a duplicate transaction ID is rejected (409); recording a
payment is allowed for plain STAFF (not 403); a partial refund reduces
paid/increases balance correctly; a refund exceeding the payment is
rejected (422); voiding a payment excludes it from paid; voiding the
whole invoice is blocked once any payment exists (409) but succeeds
before one, and a charge-add attempt against a VOID invoice returns
409; and the key coexistence test verifies the OLD `settle-free-visit`
flow and the NEW billing model operate completely independently on the
same appointment.

Full suite: 502 passed (480 + 21 new + 1 previously-uncounted; see note
below), 1 failed. The failure,
`test_scheduling_flow.py::test_booking_uses_doctor_specific_timezone`,
is pre-existing and unrelated to this phase's diff (nothing in `git
diff` for Phase 9 touches scheduling, availability, or timezone
handling). Root-caused rather than dismissed: it fails deterministically
when run alone, and the container's real wall-clock time at the point
of the run was 09:40 EDT — the test asserts a brand-new doctor's *first
available slot* lands at 9am America/New_York, but by 9:40am that
day's 9am slot has already passed, so the scheduler correctly rolls to
the next slot (10am) instead. This is the same category of
environmental, real-time-dependent fragility flagged in every prior
phase's report (Phase 7's day-of-week case, Phase 8's two cases), just
triggered by a different clock condition each time; none of them share
a root cause with each other or with this phase's actual diff.

**Browser-verified end-to-end**: ran the app locally (`uvicorn` +
`vite`), logged in as `demoadmin`, opened a CHECKED_IN patient's
Consultation Workspace, and exercised the new Billing tab directly:
added a ₹500 charge (gross/balance updated live), recorded a ₹200
partial payment (status flipped to PARTIALLY_PAID), recorded the
remaining ₹300 (status flipped to PAID), used "Bill this" against an
unbilled LAB order from the same visit's Orders tab (confirmed it
disappeared from "Unbilled from this visit" once billed), and confirmed
the "Void bill" action correctly disappears once any payment exists.
Also confirmed via direct API calls (same code path the UI buttons
call) that voiding a charge and voiding a payment both correctly
recompute gross/paid/balance. Also confirmed via `curl` that `GET
.../bill` succeeds and auto-creates an invoice for a appointment that
has never been checked in, matching the "not gated on CHECKED_IN"
design. `npx tsc -b`, `npm run lint`, and `npm run build` all pass
clean (only pre-existing warnings, none newly introduced).

## Known gaps / deliberately out of scope

- **Not unified with `appointments.consultation_fee`.** See "Scope"
  above — a deliberate boundary, not an oversight. A future phase could
  migrate the consultation fee itself into a `CONSULTATION`-type charge
  on this new model, but that requires touching queue-token issuance
  and was out of scope here.
- **No price catalog.** Charge amounts (including the "Bill this"
  quick-add for an unbilled order) remain staff-entered — same stance
  as orders' test/service descriptions since Phase 6.
- **No packages or insurance/TPA claims** (master spec sections 39-40)
  — explicitly not built; this phase covers single-patient, cash/direct
  billing only.
- **No printable receipt/invoice PDF.** The bill summary is
  screen-only; a print/PDF view is a reasonable follow-up, not required
  by this phase's exit criteria.

## Next phase

Phase 10 (Patient 360 + Timeline) per the master spec's own sequencing
— pulling encounters, vitals, consultations, orders, prescriptions, and
now billing into one longitudinal patient view.
