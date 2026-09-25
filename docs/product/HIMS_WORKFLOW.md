# HIMS Workflow — One Connected Hospital Workflow

## Purpose

State, once, in one place, the principle every other doc and every future phase must follow: **HospitalOS is one connected hospital workflow, not independent pages that get wired together afterward.** Design the domain relationships and the patient journey first; build pages around those relationships, never the reverse.

## Target State — the canonical journey

```
Patient
  ↓
UHID
  ↓
Encounter
  ↓
Appointment / Walk-in / Follow-up
  ↓
Check-in
  ↓
Queue / Token
  ↓
Triage / Vitals
  ↓
Consultation
  ↓
Diagnosis
  ↓
Orders
  ↓
Results / Outcomes
  ↓
Prescription
  ↓
Pharmacy
  ↓
Billing
  ↓
Payment
  ↓
Follow-up
  ↓
Visit Completion
  ↓
Patient 360
```

This is **not** a strict linear pipeline. After consultation, the workflow branches, and every branch stays attached to the same patient and the same encounter:

```
Consultation
   │
   ├── Prescription
   ├── Laboratory
   ├── Radiology
   ├── Procedure
   ├── Pharmacy
   ├── Billing
   └── Admission → IPD   (target state — not built yet, see docs/architecture/OPD_TO_IPD.md)
```

And at the system level, every module is a spoke off the same hub:

```
                    PATIENT
                       │
                     UHID
                       │
                    ENCOUNTER
                       │
       ┌───────────────┼────────────────┐
       │               │                │
     OPD             IPD            Emergency
       │               │                │
       └───────────────┼────────────────┘
                       │
                 Clinical Events
                       │
       ┌───────────────┼─────────────────┐
       │               │                 │
     Orders          Results         Prescription
       │               │                 │
       └───────────────┼─────────────────┘
                       │
                 Financial Events
                       │
                Billing / Payment
                       │
                  Patient 360
```

## Current State

The OPD spoke of this diagram is real: `encounters.id` is the FK every downstream table (vitals, consultations, orders, order_results, prescriptions, charges, invoices, payments) actually points to — confirmed by reading the relevant migrations, not assumed. This is the load-bearing fact behind every workflow doc in `docs/workflows/`: nothing there is describing a future design, it's describing what the FKs already enforce.

IPD and Emergency are not built (see Target State note above and `docs/architecture/OPD_TO_IPD.md`). "Clinical Events" and "Financial Events" as depicted are real relationships in the OPD spoke, not yet abstracted into a formal cross-cutting concept — there's no `clinical_events` or `financial_events` table; the diagram describes the *shape* of the relationships (orders/results/prescriptions all hang off one encounter; charges/invoices/payments all hang off the same encounter's billing), which is exactly what makes them "connected" rather than "an event bus."

## Different roles, same source of truth

Each of these is a distinct workspace (different sidebar, different landing screen, different default filters) over the *same* `patients`/`encounters`/`orders`/`invoices` rows — never a separate copy of the data:

| Role | Workspace path | Current state |
|---|---|---|
| Receptionist | OPD → Patient Search → Patient → Encounter | ✅ real (`RECEPTIONIST` role, `AppointmentsPanel`/`PatientsPanel`) |
| Doctor | OPD Queue → Patient → Encounter → Consultation | ✅ real (`DOCTOR` role, `QueueSection`/`ConsultationWorkspace`) |
| Nurse | Triage/Vitals workspace | ✅ real (`NURSE` role, vitals-recording capability) |
| Lab Technician | Lab Worklist → Order → Patient → Encounter | ✅ real (`LAB_TECH` role, `LabRadiologyWorklistPanel.tsx`) |
| Pharmacist | Pharmacy Queue → Prescription → Patient → Encounter | ✅ real (`PHARMACIST` role, `PharmacyPanel.tsx`) |
| Cashier | Billing Queue → Invoice → Patient → Encounter | 🟡 `BILLING` role exists and is gated correctly; there is no distinct "Billing Queue" list view separate from the per-visit billing panel — see `docs/workflows/BILLING.md` |
| Hospital Administrator | Command Center → Patient/Encounter/Department/Operations | 🟡 `DashboardPanel.tsx` exists with real KPIs; it is not yet the full "Command Center" (waiting-time/consultation-time/department-load breakdown) the target state describes — see `docs/architecture/MODULE_ARCHITECTURE.md` |

## Gap

The role-workspace mapping is real for six of seven roles. The Cashier's "Billing Queue" and the Administrator's full "Command Center" are the two named gaps — both are additive screen work on top of data that already exists (invoices already have status; the Dashboard already computes most of the KPIs it's missing), not new domain modeling.

## Recommended Implementation

When either gap above is picked up as a phase: build the new screen as a filtered view over the existing `invoices`/`appointments` data (a Billing Queue is `invoices` filtered to unpaid/partially-paid across all encounters, not a new table), and extend `DashboardPanel.tsx`'s existing KPI-card pattern rather than building a second dashboard. See `docs/implementation/PHASES.md`.
