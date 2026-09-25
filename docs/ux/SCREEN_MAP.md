# Screen Map

## Purpose

Map every important screen to its upstream/downstream neighbors, so no future screen gets built as an island. This is the concrete, screen-level companion to `docs/product/PATIENT_JOURNEY.md`.

## Current State — the OPD golden path

```
Patient Search
      ↓
Patient Registration
      ↓
New OPD Visit
      ↓
Appointment / Walk-in
      ↓
Review
      ↓
Check-in
      ↓
Queue
      ↓
Triage
      ↓
Consultation
      ↓
Orders
 ┌────┼───────┐
 ↓    ↓       ↓
Lab  Radio   Prescription
 ↓    ↓       ↓
Result Result Pharmacy
 └────┼───────┘
      ↓
Billing
      ↓
Payment
      ↓
Receipt
      ↓
Follow-up
      ↓
Patient 360
```

This matches the target diagram exactly — confirmed live via browser walkthrough of the full walk-in-to-payment path (per `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §73-76). No dead ends were found anywhere on this path.

## Screen-by-screen map

| Screen | Component | Entry point | Patient context | Encounter context | Primary action | Next screen | Return path | Role | Module dependency |
|---|---|---|---|---|---|---|---|---|---|
| Patient Search | `PatientsPanel.tsx` / `GlobalSearchBar.tsx` | Sidebar "Patients", or search bar from anywhere | search input | — | Select or Register | Patient Registration or Patient 360 | Sidebar | all roles (read) | none |
| Patient Registration | `PatientFormModal.tsx` | "+ Register new patient" from Patient Search or Book Appointment's walk-in step | new/editing patient | — | Save | back to caller (search results or booking flow) | Cancel/close | RECEPTIONIST, ADMIN, STAFF | none |
| New OPD Visit | `BookAppointmentPanel.tsx` (dropdown: Appointment / Walk-in / Register New Patient) | "+ New OPD Visit" on Appointments | patient search step | created on confirm | proceed to doctor/slot selection | Review | back to Appointments | RECEPTIONIST, ADMIN, STAFF | none |
| Appointment / Walk-in (doctor + slot) | `BookAppointmentPanel.tsx` | from New OPD Visit | ✓ | ✓ (created) | pick slot | Review | back | same | none |
| Review & Confirm | `BookAppointmentPanel.tsx` (review step) | after slot pick | ✓ | ✓ | Confirm booking | Check-in (if walk-in, immediate) or Appointments list | back to slot picker | same | none |
| Check-in | `AppointmentActions.tsx` (`onCollectPayment`/check-in action), `AppointmentDetailsModal.tsx` | Appointments list row action | ✓ | ✓ | Collect payment / Waive charge | Queue (token issued) | Appointments list | RECEPTIONIST, ADMIN, STAFF, BILLING | none |
| Queue | `QueuePanel.tsx` / `QueueSection.tsx` / `DepartmentQueuePanel.tsx` | Sidebar (standalone) or "Queue" button from Appointments | per-row | per-row | Recall / Priority / Hold / Mark Completed / View Consultation | Consultation (`onOpenConsultation`) | "Back to Appointments" | RECEPTIONIST, DOCTOR, NURSE, ADMIN, STAFF | none |
| Triage / Vitals | `ConsultationWorkspace.tsx` (Triage tab) | opened from Queue or Appointments | ✓ | ✓ | Save vitals | Consultation tab | "Back to queue" | NURSE, DOCTOR, STAFF, ADMIN | none |
| Consultation | `ConsultationWorkspace.tsx` (Consultation tab) | same workspace, tab switch | ✓ | ✓ | Save Draft / Complete Consultation | Orders / Prescription / Billing tabs | tab switch or "Back to queue" | DOCTOR, STAFF, ADMIN (amend: DOCTOR/ADMIN) | none |
| Orders | `ConsultationWorkspace.tsx` (Orders tab) | same workspace | ✓ | ✓ | Create order (LAB/RADIOLOGY/PROCEDURE/SERVICE/EXTERNAL_REFERRAL) | Lab Worklist (for LAB_TECH) or stays in workspace | tab switch | DOCTOR, STAFF, LAB_TECH (results), ADMIN | `LAB_RADIOLOGY` gates internal LAB/RADIOLOGY; `EXTERNAL_REFERRAL` always available |
| Lab/Radiology Worklist | `LabRadiologyWorklistPanel.tsx` | Sidebar "Lab Worklist" | per-row | per-row | Record result | back to worklist list | Sidebar | LAB_TECH, ADMIN | `LAB_RADIOLOGY` |
| Prescription | `ConsultationWorkspace.tsx` (Prescription tab) / `PrescriptionPanel.tsx` | Consultation workspace | ✓ | ✓ | Add medicine, Prescribe | Pharmacy (queue) | tab switch | DOCTOR, STAFF, ADMIN | none (prescribing is not module-gated; dispensing is) |
| Pharmacy | `PharmacyPanel.tsx` | Sidebar "Pharmacy" | per-prescription | per-prescription | Dispense (partial/full) | back to pharmacy queue | Sidebar | PHARMACIST, ADMIN | `PHARMACY` |
| Billing | `ConsultationWorkspace.tsx` (Billing tab) / `AppointmentBillingPanel.tsx` / `BillingPanel.tsx` | Consultation workspace, or Appointments row action, or Sidebar "Billing" | ✓ | ✓ | Add charge, Generate invoice | Payment | tab switch / close panel | BILLING, ADMIN, STAFF (front-desk collect) | `PACKAGES` gates package-billing specifically |
| Payment | `AppointmentBillingPanel.tsx` / `PaymentReceiptModal.tsx` | from Billing | ✓ | ✓ | Record payment / Refund | Receipt | back to Billing | BILLING, ADMIN, STAFF | none |
| Receipt | `PaymentReceiptModal.tsx` | after payment recorded | ✓ | ✓ | Print / Send to patient | close (back to Billing/Appointments) | close | same as Payment | none |
| Follow-up | `ConsultationWorkspace.tsx` (Consultation tab's follow-up fields) | during consultation, not a separate screen | ✓ | ✓ | Set follow-up date/reason | stays in Consultation tab | — | DOCTOR, STAFF, ADMIN | none |
| Visit Completion | `VisitCompletionDialog.tsx` | "Mark completed" from Queue/Appointments/DoctorWorkspace | ✓ | ✓ | Complete Visit (non-gating) / Review pending items* | Patient 360 (visit now closed) | "Not yet" (cancel) | RECEPTIONIST, ADMIN, STAFF, DOCTOR | none |
| Patient 360 | `PatientTimelineModal.tsx` | from Patients list, or (target) inline from Consultation | ✓ | reads all encounters | none (read-only) | close | close | all roles (read) | none |
| Dashboard | `DashboardPanel.tsx` | login landing (ADMIN/STAFF) | — | — | drill into a KPI | Appointments / Doctors / Patients | Sidebar | ADMIN, STAFF | none |
| Module Licensing | `ModuleLicensingPanel.tsx` | Sidebar "Module Licensing" | — | — | Toggle Licensed/Enabled | — | Sidebar | ADMIN only | none (this *is* the module gate) |
| Audit Log | `AuditLogPanel.tsx` | Sidebar "Audit Log" | — | — | Filter/view | — | Sidebar | ADMIN only | none |

\* "Review pending items" — see `docs/product/PRODUCT_VISION.md`'s note: this exists on an unmerged branch as of this writing; verify current `main` before relying on it.

## Target State / Gap

Screens the target diagram implies but that don't exist as their own dedicated views today (they're covered by existing screens in a less specific form): a standalone Department Queue *screen* exists (`DepartmentQueuePanel.tsx`) but no standalone cross-visit Billing History/Payment History screens beyond the existing `BillingHistoryPanel.tsx`/`PaymentHistoryPanel.tsx` (these do exist, so this is not actually a gap — corrects an earlier, now-stale audit note). No dedicated UHID-card or consultation-summary print screen exists (`docs/printing/DOCUMENT_MANAGEMENT.md`). No inline Patient 360 panel inside `ConsultationWorkspace` (`docs/ux/UX_PRINCIPLES.md`).
