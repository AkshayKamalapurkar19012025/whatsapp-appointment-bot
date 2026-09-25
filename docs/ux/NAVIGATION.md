# Navigation

## Purpose

State how navigation is structured today, how it should be structured, and the rule that prevents navigation sprawl: **a nav item exists because a distinct workflow needs it, never merely because a page exists.**

## Current State

Single-level sidebar (`AdminSidebar.tsx`) grouped into `MAIN` / `MANAGE` / `ADMIN` (ADMIN role only) / `REPORTS`, rendered from `AdminApp.tsx`'s `Section` union type and gated per role (`ROLE_LANDING_SECTION` decides where each role lands after login; individual sidebar items are further gated by permission/module-availability checks — e.g. `Pharmacy` only shows when the `PHARMACY` module is available, `Lab Worklist` only for roles with `order.result` or equivalent).

Full navigation map: see `docs/architecture/MODULE_ARCHITECTURE.md`'s "Current State — navigation" table.

Sub-navigation within a workspace (tabs) is used in exactly the places the target UX calls for: `ConsultationWorkspace`'s Triage/Consultation/Orders/Prescription/Billing tabs, `DoctorWorkspace`'s Overview/Appointments/Schedule/Blocks/Departments/Types/Profile tabs, `AppointmentsPanel`'s status-filter tabs (All/Waiting/In Consultation/Completed/Cancelled/No Show/More).

Return paths exist for every drill-down screen reached this session's own testing touched (`onBack`/`onClose` callbacks consistently wired): closing `ConsultationWorkspace` returns to the queue/appointments list it was opened from; closing a modal (`AppointmentDetailsModal`, `PatientTimelineModal`) returns to the underlying list without a full navigation.

## Target State

Same grouped-sidebar shape, reorganized under the module-oriented groups in `docs/architecture/MODULE_ARCHITECTURE.md`'s target navigation (`PATIENT CARE` / `CLINICAL` / `DIAGNOSTICS` / `MEDICATION` / `REVENUE` / `OPERATIONS` / `ADMINISTRATION`), once IPD/Emergency/Operations modules exist to justify the extra groups.

## Gap

The grouping labels differ (`MANAGE`/`REPORTS` vs. the target's domain-oriented groups), and several target groups (`DIAGNOSTICS`, `OPERATIONS`) don't exist as their own sidebar sections because the underlying screens (a split Laboratory/Radiology view, Beds & Wards/OT/ICU) don't exist. This is consistent with — not a violation of — the "don't add navigation for a page that doesn't exist" rule.

## Rule for future navigation changes

Before adding any sidebar entry, tab, or nav link:

1. Confirm there's a real screen/workflow behind it (never a placeholder).
2. Confirm it's gated the same way every comparable entry already is (role permission + module availability, both server-enforced, never a frontend-only check).
3. Prefer adding a tab inside an existing workspace over a new top-level sidebar entry, unless the new thing is genuinely a distinct workspace a specific role lands on (matching the role→workspace mapping in `docs/product/HIMS_WORKFLOW.md`).
