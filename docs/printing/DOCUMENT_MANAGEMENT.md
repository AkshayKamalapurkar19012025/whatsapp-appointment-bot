# Printing & Document Management

## Purpose

Printing is a later implementation phase, but every workflow must already be designed with document generation in mind — the domain data a printed document needs must already exist on the record, not be reconstructed at print time. This doc states what's real today and what a full document subsystem would still need.

## Current State

**Architecture**: one shared mechanism, `.print-area` + `@media print` CSS rules in `frontend/src/styles.css`, combined with the browser's native `window.print()` / Save-as-PDF. There is no server-rendered PDF pipeline — a deliberate choice (see `docs/decisions/ADR-005-PRINTING-ARCHITECTURE.md`): the browser's own print dialog already offers "Save as PDF," so a second, server-side rendering path would reimplement that for no functional gain at this scale.

**Documents with a real, dedicated print layout today**:

| Document | Component | Notes |
|---|---|---|
| Token slip | `BookAppointmentPanel.tsx` (print action) | scoped print CSS |
| Prescription | `PrescriptionPanel.tsx` | scoped print CSS |
| Itemized bill / invoice | `AppointmentBillingPanel.tsx` | scoped print CSS |
| Lab/radiology requisition | (part of the orders/billing print pass, commit `a9dcbb5`) | requisition only — **no report/result print layout** |
| Payment receipt | `PaymentReceiptModal.tsx` | full field set per master spec §42: Hospital/Receipt number/Patient/UHID/Encounter/Invoice/Services/Gross/Discount/Tax/Net/Paid/Payment method/Transaction ID/Cashier/Date-time; Print/Download/Send to patient all present, browser-verified |
| Appointment slip | `AppointmentSlipModal.tsx` | added in the phase documented at `docs/OPD_HIMS_P15_REGISTRATION_APPOINTMENT_PRINTS.md` |
| Patient registration summary | `PatientRegistrationSummaryModal.tsx` | added in the same phase |
| Consultation-related print | `ConsultationWorkspace.tsx` | `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` exactly which sub-document (consultation summary vs. just the prescription) this covers — the file appears in the print-CSS grep but its exact scope wasn't re-verified line-by-line for this pass |

**Document identifiers**: backend-generated, `GENERATED ALWAYS AS (...) STORED` columns — `patients.uhid` (migration `0024`), `appointments.appointment_number` (migration `0048`), `appointments.invoice_number` (migration `0026`), `invoices.invoice_number`/`payments.receipt_number` (migration `0033`). This satisfies "document identifiers must be backend-generated" wherever it's been applied; it has not been applied to every document type in the Target State list below (no identifier scheme exists yet for a UHID card, consultation summary, or lab/radiology report, since those documents don't exist yet).

**Explicitly not built**: UHID/patient ID card (with barcode), visit slip (distinct from the appointment slip), consultation summary, lab/radiology **report** print (requisition exists, the result-side document doesn't), pharmacy dispensing receipt, estimate, follow-up document, discharge-related documents (IPD-dependent). Hospital branding configuration (logo/address/GST/footer) doesn't exist — `hospitals` has only `name`/`timezone` today. No paper-size configuration (A4/A5/thermal) — everything relies on the browser's own print-dialog paper handling. No document versioning/amendment tracking for any of these print types. No document-history or print-audit-trail view.

## Target State

Full target document list (from the originating brief, recorded here for future scoping):

Patient Registration Summary, UHID Card, Appointment Slip, OPD Visit Slip, Token Slip, Prescription, Consultation Summary, Lab Requisition, Lab Report, Radiology Requisition, Radiology Report, Pharmacy Receipt, Invoice, Payment Receipt, Estimate, Follow-up Document, Discharge-related documents (later, IPD-dependent).

Target capabilities: Print Preview, PDF, A4/A5/thermal-receipt paper sizing, hospital branding, backend-generated document numbering (already real for the documents that exist), versioning/amendment handling, reprinting, an audit trail, a document-history view, privacy/security controls, multi-page document support, print templates, printer-friendly layouts.

**Non-negotiable rule, already honored by every document built so far and to be preserved by every future one: reprinting a document must NOT create a new clinical or financial transaction.** Every current print view reads from an already-persisted record (the invoice, the payment, the prescription) rather than generating/mutating anything at print time — printing is a pure read/render operation. Any future document type must follow this same rule.

## Gap

Roughly 8 of ~16 named document types exist. The infrastructure gaps (no branding config, no paper-size config, no versioning, no document-history view, no dedicated print-audit-trail beyond whatever generic request logging already exists) apply equally to every future document type and are worth solving once, not per-document.

## Recommended Implementation

When a real Printing phase is scoped (`docs/implementation/PHASES.md` Phase 13):

1. **Do not switch to server-rendered PDF** unless a concrete requirement emerges that the browser's print-to-PDF genuinely can't satisfy (e.g. programmatic bulk generation, emailing a PDF without the user's browser in the loop) — the existing `.print-area` mechanism is working, tested, and simple; replacing it is a large, unforced architectural change per `CLAUDE.md`'s "don't replace working architecture without evidence."
2. Add `hospitals` branding columns (logo URL, address, GST/tax ID, footer text) additively — every print layout would read from these instead of hardcoding hospital name.
3. Build remaining document types (UHID card, consultation summary, lab/radiology report, dispensing receipt, estimate, follow-up document) one at a time, each reusing the existing `.print-area`/`@media print` pattern and an existing backend-generated-identifier column (adding a new `GENERATED ALWAYS AS` column per document type that needs one, following the `appointment_number`/`invoice_number` precedent).
4. Versioning/reprint-audit: the simplest approach consistent with this codebase's existing patterns is a `document_print_log` table (document type, document id, printed_by, printed_at) written on each print/download/send action — additive, no change to the documents themselves, and gives the document-history view its data source without touching the clinical/financial record it's printing from (preserving the reprint-must-not-create-a-transaction rule).
