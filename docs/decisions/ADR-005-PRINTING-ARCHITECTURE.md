# ADR-005: Printing Architecture

## Status

Accepted. Implemented for ~8 of ~16 target document types.

## Context

A hospital needs a range of printed/downloadable documents (token slips, prescriptions, invoices, receipts, requisitions, and more). The two broad architectural choices are a server-side rendering pipeline (generate a PDF on the backend, e.g. via a headless-browser or templating library, and serve it) or a browser-native approach (a real, styled HTML view with dedicated print CSS, using the browser's own print/Save-as-PDF).

## Problem

Which approach gives HospitalOS real, usable documents fastest, with the least new infrastructure, while still meeting the eventual requirements (consistent formatting, backend-generated numbering, an audit trail, and — critically — the rule that reprinting must never create a new clinical or financial transaction)?

## Decision

**Documents are generated from domain records and can be reprinted without creating new transactions.** Concretely: one shared `.print-area` + `@media print` CSS mechanism (`frontend/src/styles.css`), applied per document type in its own React component (`PaymentReceiptModal.tsx`, `PrescriptionPanel.tsx`, `AppointmentSlipModal.tsx`, etc.), rendering the *already-persisted* record — never generating or mutating data at print time. The browser's own print dialog (which every modern browser already offers as "Save as PDF") is the PDF mechanism; no server-side rendering pipeline exists or is currently planned.

Document *identity* (the number printed on the document) is backend-generated and stored, not computed at print time: `patients.uhid`, `appointments.appointment_number`, `invoices.invoice_number`, `payments.receipt_number` are all `GENERATED ALWAYS AS (...) STORED` columns, set once at row-creation time. Printing (or reprinting) reads this same stored value — it can never produce a different number for the same record.

## Alternatives considered

1. **Server-rendered PDF pipeline** (a headless-browser or templating-library-based service) (rejected, for now) — real capability (branding templates, precise paper-size control, programmatic bulk generation) but a genuinely new piece of infrastructure, for a requirement (a user wants a PDF of this one document) the browser's native print-to-PDF already satisfies today. Revisiting this is explicitly not ruled out — see Future implications — but adopting it now would be replacing working architecture without the evidence of an unmet need, which `CLAUDE.md` names as something not to do.
2. **A third-party PDF-generation SaaS/library integrated client-side** (rejected) — adds an external dependency and, for most of the target document list, no capability the browser doesn't already provide.
3. **Store documents as immutable generated files at creation time, serve them unchanged on reprint** (rejected, for now) — would be the right design if programmatic/bulk document generation or an air-gapped-from-the-live-record archival requirement emerges, but adds real storage/lifecycle complexity with no current requirement driving it. The living-record-render approach (option taken) has a real advantage over this for now: printing always reflects the current-and-correct state of a mutable-until-finalized record (e.g. a bill isn't finalized until payment; printing an in-progress bill as a live render, rather than a snapshot, avoids ever showing a stale total).

## Consequences

- No server-rendered-PDF infrastructure to build, deploy, or keep in sync with the frontend's own styling.
- Every document type added so far reused the same mechanism — low marginal cost per new document type (confirmed: 8 document types shipped across several phases without ever needing to touch the underlying print mechanism itself).
- Paper-size control (A4 vs. A5 vs. thermal receipt) is limited to whatever the browser's print dialog and `@media print` CSS can express — genuinely less precise than a dedicated PDF-generation library would offer. This is the real, accepted tradeoff of this decision.
- No document-versioning or print-audit-trail exists yet, since neither was needed to satisfy "print a correct document" — see `docs/printing/DOCUMENT_MANAGEMENT.md`'s Recommended Implementation for how to add this without revisiting this ADR's core decision (a `document_print_log` table, not a rendering-pipeline change).

## Future implications

If a concrete requirement emerges that the browser-native approach genuinely can't satisfy — programmatic/bulk document generation (e.g. printing 50 patients' appointment slips at once without 50 browser print dialogs), or emailing a PDF attachment without the user's browser in the loop — that is the trigger to revisit this decision, not before. When/if that happens, the stored, backend-generated document-identity columns this ADR established stay valid and reusable by a server-side renderer; only the rendering mechanism itself would change.
