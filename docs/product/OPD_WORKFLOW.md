# OPD Workflow

## Purpose

Clarify a distinction the master spec calls out explicitly and that this codebase already gets right in its data model but not fully in its navigation: **Appointments are a capability inside the OPD workflow, not the entire OPD module.**

- **OPD** is the umbrella workflow/workspace for a hospital's outpatient operations today.
- **Queue** is an OPD *operational state* — who's waiting, who's being seen, who's done — not a separate system.
- **Booking Appointment** is one *action* a receptionist takes.
- **Walk-in Registration** is one *visit type* (as opposed to a pre-booked appointment).
- **Register New Patient** is a *patient-master* capability, reachable from inside the OPD visit flow, not a destination of its own.

Treating these as independent top-level nav items (`Appointments`, `Book Appointment`, `Queue` as three unrelated systems) is the anti-pattern this doc exists to head off.

## Current State

The current sidebar (`AdminSidebar.tsx`, driven by `AdminApp.tsx`'s `Section` union) has `Appointments`, `Doctors`, `Department Queue`, `Patients`, `Departments`, `Appointment Types`, `Pharmacy`, `Packages`, `Lab Worklist`, plus admin-only `Staff Accounts`/`Audit Log`/`Module Licensing`, and reports (`Billing`, `Billing History`, `Payment History`, `Waiting-Time Analytics`). `Queue` and `Book Appointment` are reached *from inside* the Appointments workspace (a header button and a "+ New OPD Visit" action), not as separate always-visible sidebar entries — so the current navigation is already closer to the target conceptual structure below than a literal reading of "Appointments / Book Appointment / Queue as top-level items" would suggest. It is not, however, organized under one explicit "OPD" umbrella with the sub-views the target state names (Today/Waiting/In Consultation/Completed/Cancelled/No Show as one filterable list) — those exist as status-filter tabs on the Appointments page (`All/Waiting/In Consultation/Completed/Cancelled/No Show` tabs, confirmed live), which is functionally the same thing under a different label ("Appointments" instead of "OPD").

## Target State

```
OPD
 ├── Today
 ├── Appointments
 ├── Waiting
 ├── In Consultation
 ├── Completed
 ├── Cancelled
 └── No Show

+ New OPD Visit
 ├── Appointment
 ├── Walk-in
 └── Follow-up
```

## Gap

The functional pieces (status-filtered lists, walk-in vs. appointment vs. follow-up visit creation, queue as an in-context view) already exist. The gap is purely naming/framing: the sidebar entry is labeled "Appointments" rather than "OPD," and "Today" is the page's default date filter rather than an explicit first sub-tab. This is a low-risk, cosmetic relabeling if ever picked up — **do not treat it as urgent or as blocking other work**; it does not change any data model or API.

## Recommended Implementation

If/when this is picked up: rename the sidebar entry and reframe the existing status tabs under it — do not build a second "OPD" page alongside the existing `AppointmentsPanel.tsx`. "+ New OPD Visit" already exists as a dropdown with Appointment/Walk-in/Register-New-Patient entries on the Appointments page; confirm it matches the target's Appointment/Walk-in/Follow-up split (follow-up booking today is a consultation-time action, not a top-level "+ New OPD Visit" entry — verify this against current code before assuming it needs to move) before changing it. `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION`: whether "Follow-up" as a visit-creation entry point (as opposed to a field set during consultation) exists anywhere in the current UI.
