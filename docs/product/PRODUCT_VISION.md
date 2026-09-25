# Product Vision

## What HospitalOS is

HospitalOS is a production-grade Hospital Information Management System (HIMS) for 100+ bed multi-speciality hospitals, built around one connected patient journey rather than a set of independent departmental tools. It began as a WhatsApp appointment-booking bot and has grown, phase by phase, into a full OPD (outpatient department) system with staff/admin web UI, patient web UI, and the original WhatsApp conversational flow all sitting on top of the same Postgres database and the same booking/availability engine.

This document is the entry point. It states what exists today, in outline, and points to the detailed docs for each area. It does not restate the full audit — see `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` for the most recent line-by-line pass against the ~100-section master spec, and the "What changed since that audit" section below for what's moved since.

## Current State

### What's real and working today

- **Patient identity & registration**: permanent UHID (`HOS-NNNNNNN`), duplicate detection, patient merge, allergy tracking (migration `0042`).
- **OPD scheduling & booking**: doctor schedules with date ranges, one-off blocks, an availability engine shared across the REST API and the WhatsApp conversational flow, advisory-lock + DB exclusion-constraint concurrency protection.
- **Walk-in registration, check-in, and queue/token**: real arrival timestamps, token issuance, a live per-doctor queue view.
- **Encounter-centric clinical flow**: triage/vitals → consultation (with amendment workflow) → orders (lab/radiology/procedure/external-referral) → results → prescription → pharmacy dispensing (inventory/batch/expiry-aware) → billing (charges, packages, tax, discounts) → payment → receipt.
- **Visit Completion checklist**: a non-gating precondition summary shown before "Complete Visit" (see `docs/workflows/OPD_CHECKIN_QUEUE.md` and master spec section 43).
- **Patient 360**: a cross-domain timeline (vitals/consultations/orders/results/prescriptions/billing) per patient, reached from Patients/search.
- **Role-based access control**: `ADMIN`/`STAFF` plus differentiated `DOCTOR`/`NURSE`/`RECEPTIONIST`/`LAB_TECH`/`PHARMACIST`/`BILLING` roles, each with its own sidebar, landing screen, and gated capabilities (`AdminApp.tsx`'s `ROLE_LANDING_SECTION`, permission checks server-side via `require_permission()`).
- **Module Licensing/Enablement**: Licensed/Enabled/Available split for `LAB_RADIOLOGY`/`PHARMACY`/`PACKAGES`, with `HIDDEN`/`EXTERNAL`/`BLOCKED` degradation, DB-enforced ("hospital admin cannot self-grant a paid module").
- **Lab/Radiology Worklist**: a cross-patient, filterable worklist screen for `LAB_TECH`.
- **Exception Engine**: six live-computed operational exceptions (billing not started, payment pending, etc.) with click-through actions.
- **Global search**: cross-entity (patients + appointments) search bar (`GlobalSearchBar.tsx`).
- **Notification center**: a bell + notification list (`NotificationBell.tsx`, `notification_center_service.py`).
- **Audit log**: backend + admin-facing panel (`AuditLogPanel.tsx`).
- **Printing**: token slip, prescription, itemized bill, lab/radiology requisition, payment receipt, appointment slip, and patient registration summary all have dedicated print layouts via one shared `.print-area`/`@media print` mechanism and the browser's native print/Save-as-PDF — see `docs/printing/DOCUMENT_MANAGEMENT.md`.
- **API error envelope**: `{success, errorCode, message, details}` on every error response, centralized via `app/error_handling.py`, alongside the pre-existing `detail` field for backward compatibility.
- **Pagination**: `GET /api/patients/admin` and comparable admin list endpoints now take `limit`/`offset`.
- **Testing**: 683 backend test functions; frontend build/typecheck/lint clean as a standing bar.

### What does not exist yet

- **IPD (in-patient) and Emergency workflows.** `encounters.encounter_type` is reserved for `'OPD'` only today (CHECK constraint). A separate `ipd-service/schema/0001_baseline_ipd_schema.sql` sketch exists but is explicitly marked **SUPERSEDED** — the decision was to extend the OPD app's own database when IPD is built, not stand up a separate service. See `docs/architecture/OPD_TO_IPD.md` and `docs/decisions/ADR-004-OPD-IPD-CONTINUITY.md`.
- **Lab/Radiology-specific result workflows.** Results use one generic `ORDERED → IN_PROGRESS → COMPLETED` pipeline and one generic parameter/value/unit/reference-range table for every order type — not the richer, type-specific pipelines (sample collection, verify-then-release, radiology Findings/Impression structure) a dedicated Lab/Radiology module would eventually want.
- **A shared `PatientHeader` / `StatusBadge` / `Timeline` component.** Patient context is shown on every relevant screen, but each screen currently reimplements the markup locally rather than sharing one component.
- **i18n / multilingual documents.** Every string is hard-coded English.
- **Insurance/TPA claim workflow** beyond the `invoices.bill_type` classification (payer/policy/pre-auth/co-pay/claim fields are explicitly deferred, not faked).
- **A server-rendered/PDF-generated document pipeline, document versioning, or a document-history/reprint-audit view.** Printing today is "browser print a real layout," which is a deliberate, documented choice — see `docs/decisions/ADR-005-PRINTING-ARCHITECTURE.md`.

### What changed since `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md`

That audit (as of PR #112) is the most recent full pass and is mostly still accurate, but PR #114 (merged after it) closed several of the gaps it names:

| Audit gap (PR #112) | Status after PR #114 |
|---|---|
| "Role-based work is aspirational, not real" (Principle 5) | ✅ Closed — real per-role sidebar/landing/capabilities now exist |
| "Module licensing/enablement not implemented" | ✅ Closed — Licensed/Enabled/Available + degradation now exist |
| "No global search" | ✅ Closed — `GlobalSearchBar.tsx` |
| "No notification center" | ✅ Closed — `NotificationBell.tsx` + `notification_center_service.py` |
| "No allergy data model" | ✅ Closed — `patient_allergies` (migration `0042`) |
| "No pagination on `GET /patients`/`GET /appointments`" | ✅ Closed for the admin list endpoints (`limit`/`offset`) |
| "No frontend page to view the audit log" | ✅ Closed — `AuditLogPanel.tsx` |
| "API error format does not match spec's `{success, errorCode, message, details}`" | ✅ Closed — `app/error_handling.py` |
| "No Laboratory Worklist screen" | ✅ Closed — `LabRadiologyWorklistPanel.tsx` |
| Accessibility / responsive / print bugs (§52-54) | ✅ Addressed in a dedicated hardening pass (0 axe-core violations across 13 screens; a real phone-width topbar bug and 2 print bugs fixed) |
| "Visit Completion checklist" (§43) | 🟡 The non-gating checklist now exists (`VisitCompletionDialog.tsx`, `visit_completion_service.py`); a "jump to the pending item's tab" navigation enhancement exists on an unmerged branch as of this writing — **verify current `main` state before relying on it.** |

Everything else in that audit (lab/radiology result-model granularity, no dedicated Department Queue/Billing-History/Payment-History screens as their own top-level views vs. per-visit lists, no allergy-aware clinical-safety warnings in the consultation UI, no i18n) should be treated as still accurate unless a specific doc below says otherwise.

## Target State

HospitalOS should read, end to end, as **one patient → one permanent UHID → multiple encounters → connected clinical events → connected orders/results → connected medications → connected financial events → one complete patient journey**, with different roles seeing different workspaces over that same underlying data. See `docs/product/HIMS_WORKFLOW.md` for the full journey and branch diagram, and `docs/implementation/PHASES.md` for how the remaining gaps above should be sequenced.

## Gap

The single largest structural gap is that IPD and Emergency do not exist as encounter types yet, and the module/degradation framework that would make "OPD without IPD" a coherent, licensable configuration is only proven for `LAB_RADIOLOGY`/`PHARMACY`/`PACKAGES` — not yet for a future `IPD` module. Everything else is either a UI/screen gap (a dedicated worklist or history screen not yet built) or a documented, deliberate scope decision (i18n, insurance claims, PDF pipeline) rather than an oversight.
