# Workflow: Billing

## 1. Purpose

Aggregate every billable event on an encounter (consultation fee, lab/radiology, procedures, pharmacy, packages) into an invoice, take payment, and issue a receipt — without ever letting a charge exist disconnected from its encounter.

## 2. Actors

Billing/Cashier, Receptionist (front-desk collection at check-in), Admin, Staff.

## 3. Entry points

`ConsultationWorkspace.tsx`'s Billing tab (in-visit), `AppointmentBillingPanel.tsx` (from an Appointments row action), `BillingPanel.tsx` (sidebar "Billing," cross-visit report view), check-in payment collection (`docs/workflows/OPD_CHECKIN_QUEUE.md`).

## 4. Preconditions

An encounter with at least one chargeable event, or a manually added charge.

## 5. Workflow

```
Charges accumulate (source_type: Consultation/Lab/Radiology/Procedure/Pharmacy/Package/Other)
      ↓
Generate/update Invoice (Gross/Discount/Tax/Net computed server-side, per-invoice tax rate)
      ↓
Record Payment (Cash/UPI/Card/Bank Transfer/Insurance/Other)
      ↓
payment_status derived: UNPAID / PAID / FAILED / WAIVED / REFUNDED
      ↓
Receipt (print/download/send)
```

## 6. UI pages

`ConsultationWorkspace.tsx` (Billing tab), `AppointmentBillingPanel.tsx`, `BillingPanel.tsx`, `BillingHistoryPanel.tsx`, `PaymentHistoryPanel.tsx`, `PaymentReceiptModal.tsx`.

## 7. Actions

Add charge, Void charge (reason required), Generate invoice, Void invoice/bill (reason required), Record payment, Refund, Void payment (reason required), Print/Send receipt.

## 8. State transitions

`invoices.payment_status` (derived from summed `payments`): `UNPAID → PAID` / `PARTIALLY PAID` / `REFUNDED`. `payments.status` itself is only `COMPLETED`/`VOIDED` — **there is no "Pending"/"Failed" payment state modeled**; a payment row is only ever created already-completed, so a failed payment *attempt* (e.g. a declined card) has nowhere to live as an in-flight state. A `DECLINED` invoice-payment status was added (migration `0050`) specifically to give a declined-card attempt an audit trail — confirm this fully closes the gap before assuming a generic "payment failure journey" is modeled: `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` against `app/services/billing_services.py`'s current payment-status handling.

## 9. Domain objects

`charges` (source_type discriminator), `invoices` (bill_type: 6-way classification including insurance/TPA/corporate/government-scheme), `payments` (method, receipt_number, transaction_id, refunded_amount).

## 10. API requirements

Billing endpoints under `app/api/billing.py` (prefixed `/appointments`) and `app/api/billing_history.py`.

## 11. Validation

`PaymentExceedsBalance` guards overpayment; a partial unique index on `transaction_id` per invoice prevents duplicate-transaction double-recording; package price `CHECK (price > 0)` at the DB layer (not just the API model) — confirmed example of DB-level, not just application-level, validation.

## 12. Error handling

Void actions require a typed reason (AlertDialog-style prompt), never a bare confirm — verified across every billing endpoint touched in recent phases.

## 13. Permissions

`BILLING`, `ADMIN` for void/refund actions (`canManageBilling`); front-desk payment collection also available to `RECEPTIONIST`/`STAFF` at check-in.

## 14. Audit requirements

Void bill/void charge/void payment/refund are all named in the master spec's example audit list; verified: these use VOID-with-reason (never DELETE) — the row stays, with a reason and a voided-by/at pair, which is itself a real audit trail even where a generic `audit_logs` row isn't also written.

## 15. Concurrency considerations

`FOR UPDATE` row locks used throughout invoice/payment mutations (confirmed, `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §56-58) — two simultaneous payment recordings against the same invoice must not both succeed past the balance.

## 16. Idempotency requirements

Duplicate `transaction_id` is rejected by a DB constraint, not just an application check — genuine idempotency guarantee at the data layer.

## 17. Tests

`tests/test_billing_invoices.py`, `test_billing_history.py`, `test_billing_report.py`, `test_packages.py`, `test_consultation_payments.py`.

## 18. Exit conditions

Invoice `payment_status` reflects reality (paid/partially paid/waived/refunded), a receipt exists, and the visit can proceed to Visit Completion.

## 19. Next workflow

Visit Completion (`docs/workflows/OPD_CHECKIN_QUEUE.md`'s state-transitions section) → `docs/workflows/PATIENT_360.md`.
