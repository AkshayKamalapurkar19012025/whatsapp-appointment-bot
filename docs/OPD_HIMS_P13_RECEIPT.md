# OPD/HIMS Master Spec — Phase 13: Receipt

Follows `docs/OPD_HIMS_P12_PACKAGES_INSURANCE.md`.

## Scope

With every gap `docs/OPD_HIMS_P0_AUDIT.md` section 5 originally flagged
now either built or deliberately deferred by the master spec's own
text (Phase 12's own "Next phase" note), this phase closes the one
concrete, previously-flagged known gap left over from the billing work
itself: Phase 9's report listed "No printable receipt/invoice PDF" as
a known gap, and `migrations/0033_billing_invoices.sql`'s own header
called a print/PDF view "a reasonable follow-up." Master spec section
42 (Receipt) is exactly that follow-up, with a precise field list:
Hospital / Receipt number / Patient / UHID / Encounter / Invoice /
Services / Gross / Discount / Tax / Net / Paid / Payment method /
Transaction ID / Cashier / Date/time, and three actions: Print /
Download / Send to patient.

**One receipt per payment, not per invoice.** The field list's
singular "Payment method"/"Transaction ID" only make sense for one
transaction — a multi-payment bill (a ₹400 cash part-payment followed
by a ₹600 card payment) has two receipts, not one. `GET .../bill/
payments/{id}/receipt` returns the invoice's own Gross/Discount/Tax/Net
(the whole bill's context — what this payment was made against)
alongside that specific payment's own amount/method/transaction ID
(what was actually collected in this transaction), never the invoice's
cumulative paid-to-date, which a second receipt would otherwise
misreport as "how much this transaction paid."

**Print and Download are the same action.** The browser's own print
dialog already offers "Save as PDF" — a second, server-rendered PDF
pipeline would just reimplement what `window.print()` gives for free,
the same minimalism stance Phase 9 already took on the bill itself
being screen-only. A `.receipt-print-area`/`@media print` rule hides
everything else on the page for the print, so what comes out is the
receipt alone, not a screenshot of the whole app around it.

**Send to patient reuses existing infrastructure, not a new one.**
`app/services/notifications.py`'s mock-notification system already has
a precedent for exactly this shape — `KIND_CHECK_IN`/`KIND_QUEUE_TOKEN`
are both "staff clicks a button at the front desk, patient gets a
message" actions, the same as "send this receipt." `KIND_RECEIPT` is
one more entry in that same family, not a new subsystem.

## What changed

**`get_payment_receipt_service`** (`app/services/billing_services.py`)
— one payment's receipt, assembled from the same tables `get_invoice_
summary_service` already reads (no new joins beyond `hospitals`/
`staff` for the hospital name and cashier).

**`GET /appointments/{id}/bill/payments/{payment_id}/receipt`** and
**`POST .../receipt/send`** (`app/api/billing.py`) — bare-staff, same
tier as viewing the bill and recording a payment: reading or
(re-)sending a receipt changes no financial state, unlike every
ADMIN-gated write already on this router.

**`mock_sms_outbox.kind` gains `'RECEIPT'`**
(`migrations/0040_receipt_notification_kind.sql`), same drop/re-add
CHECK-constraint pattern every prior `kind` addition
(migrations/0007/0012/0017/0021) already used.

**`PaymentReceiptModal.tsx`** — a new "Receipt" button on every row of
the Billing tab's Payments table (visible to any staff session, not
`isAdmin`-gated, matching the endpoint's own bare-staff tier) opens a
printable receipt with Print/Download and Send to patient actions.

## Files changed

- `migrations/0040_receipt_notification_kind.sql` — new.
- `app/services/billing_services.py` — `get_payment_receipt_service`.
- `app/services/notifications.py` — `KIND_RECEIPT`.
- `app/api/billing.py` — the two new receipt endpoints.
- `frontend/src/admin/PaymentReceiptModal.tsx` — new.
- `frontend/src/admin/AppointmentBillingPanel.tsx` — "Receipt" action per payment row.
- `frontend/src/types.ts`, `frontend/src/api.ts` — `PaymentReceipt`, `getPaymentReceipt`/`sendPaymentReceipt`.
- `frontend/src/styles.css` — `.receipt-modal`/`.receipt-meta-grid`/`.receipt-actions`, `@media print`.
- `tests/test_receipts.py` — new, 6 tests.

## Database changes

`mock_sms_outbox.kind`'s CHECK constraint gains `'RECEIPT'`. No new
tables or columns — the receipt is assembled entirely from data Phase
9's `invoices`/`charges`/`payments` and earlier `hospitals`/`patients`/
`staff` already hold.

## API changes

New: `GET /api/appointments/{appointment_id}/bill/payments/{payment_id}/receipt`,
`POST /api/appointments/{appointment_id}/bill/payments/{payment_id}/receipt/send`
(both bare authenticated staff, any role).

## Tests

6 new (`tests/test_receipts.py`): a receipt's full field set is
correct for a simple one-payment bill; a second, partial payment's own
receipt reports only what that transaction collected (not the
invoice's running total) while still showing the invoice's own
Gross/Net; a refund reduces the receipt's reported payment amount;
404 for a nonexistent payment; 401 unauthenticated; sending a receipt
records exactly one `RECEIPT`-kind mock notification containing the
payment amount and method.

Full suite: 575 passed (569 + 6 new), 1 skipped, 2 pre-existing
failures — `test_date_first_lists_multiple_doctors_with_their_own_
slot_counts` and `test_queue_visited_at_is_doctor_local_time_not_utc`,
the same real-wall-clock-time class of flake flagged in every phase
report since Phase 7; neither touches code this phase's diff changes.

**Browser-verified end-to-end**: ran the app locally, booked a walk-in
visit through to the queue, opened its Consultation Workspace's Billing
tab, added a ₹500 charge and recorded a UPI payment against it,
clicked the new "Receipt" button and confirmed every field rendered
correctly (hospital name, receipt/bill numbers, patient, date/time,
cashier, the service line, Gross/Discount/Tax/Net, "Paid this
transaction," method + transaction ID, status), then clicked "Send to
patient" and confirmed the modal's "Sent to patient." confirmation
appeared. `npx tsc -b --force`, `npm run lint`, and `npm run build` all
pass clean (only pre-existing warnings elsewhere in the app, none
newly introduced).

## Known gaps / deliberately out of scope

- **No server-rendered PDF file.** "Print"/"Download" both trigger the
  browser's native print dialog (which itself offers "Save as PDF") —
  see "What changed" above for why a second, server-side PDF pipeline
  wasn't built.
- **No bill-level receipt for a fully-paid multi-payment invoice.**
  Each payment gets its own receipt; there's no single "everything
  this invoice ever collected" combined receipt. A real hospital's
  front desk hands over one receipt per transaction, which is what
  this phase builds.
- **No receipt for a VOIDED payment beyond what its own `payment_status`
  field already shows.** A voided payment's receipt is still viewable
  (for the audit trail) and shows `VOIDED`, but nothing special is
  built around printing a "this was voided" notice.

## Next phase

No further phase was specified beyond this point in the master spec
sequencing this session has followed (Phases 3, 5-13). Every concrete,
previously-flagged known gap from Phases 9 through 12 is now closed.
Remaining unimplemented master spec sections are either cross-cutting
non-functional requirements expected to already be honored throughout
(sections 48-71: loading/empty/error states, accessibility, security,
concurrency, API design, etc. — not standalone feature phases) or
later-program-phase features with their own significant scope (IPD/
beds/OT compatibility, a real insurance claims workflow, multilingual
patient documents, module licensing) that don't follow as a natural
next increment on top of what exists today.
