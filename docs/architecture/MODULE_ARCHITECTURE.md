# Module Architecture

## Purpose

Document the target high-level navigation, compare it against what actually exists, and state the Licensed/Enabled/Available + degradation framework that governs how a module can be turned off without breaking the patient journey.

## Target State — navigation

```
Dashboard

PATIENT CARE
  Patients
  OPD
  IPD
  Emergency

CLINICAL
  Doctors
  Consultations
  Orders

DIAGNOSTICS
  Laboratory
  Radiology

MEDICATION
  Pharmacy

REVENUE
  Billing
  Insurance / TPA

OPERATIONS
  Beds & Wards
  OT
  ICU

ADMINISTRATION
  Staff
  Departments
  Services
  Settings
```

## Current State — navigation

The actual sidebar (`AdminSidebar.tsx`/`AdminApp.tsx`), grouped as it renders today:

```
MAIN
  Dashboard
  Appointments      (includes Queue, Book Appointment as in-context actions)

MANAGE
  Doctors
  Department Queue
  Patients
  Departments
  Appointment Types
  Pharmacy           (module-gated: PHARMACY)
  Packages           (module-gated: PACKAGES)
  Lab Worklist        (module-gated: LAB_RADIOLOGY, LAB_TECH role)

ADMIN (ADMIN role only)
  Staff Accounts
  Audit Log
  Module Licensing

REPORTS
  Billing
  Billing History
  Payment History
  Waiting-Time Analytics
```

## Comparison / differences

| Target group | Current equivalent | Difference |
|---|---|---|
| PATIENT CARE → OPD | `Appointments` (+ `Department Queue`) | Same content, different label/grouping — see `docs/product/OPD_WORKFLOW.md` |
| PATIENT CARE → IPD | — | Does not exist — see `docs/architecture/OPD_TO_IPD.md` |
| PATIENT CARE → Emergency | — | Does not exist, not scoped |
| CLINICAL → Consultations / Orders | Reached only from inside `ConsultationWorkspace`, not a standalone list screen | An orders-across-patients view exists only for Lab/Radiology (`Lab Worklist`); there's no general cross-patient Consultations or Orders list |
| DIAGNOSTICS → Laboratory / Radiology | `Lab Worklist` (one combined screen for both) | Combined rather than split, matching the single generic `orders` table (`docs/architecture/ORDER_SPINE.md`) — this is a deliberate simplification, not an oversight |
| MEDICATION → Pharmacy | `Pharmacy` | Same |
| REVENUE → Billing | `Billing` / `Billing History` / `Payment History` | Same data, split across three report screens rather than one `Billing` + `Insurance/TPA` pair |
| REVENUE → Insurance/TPA | `invoices.bill_type` classification only | No dedicated screen — see `docs/product/PRODUCT_VISION.md` |
| OPERATIONS (Beds & Wards / OT / ICU) | — | Does not exist — IPD-dependent, not scoped |
| ADMINISTRATION → Staff / Departments / Services / Settings | `Staff Accounts` / `Departments` / `Appointment Types` (closest analog to "Services") / no `Settings` | `Services` and `Settings` don't exist as named screens; `Appointment Types` covers part of what "Services" implies for OPD scheduling, not a general service/price catalog |

## Migration implications

Do not implement the target navigation wholesale. Per `CLAUDE.md`, changes here are additive and screen-by-screen: renaming/regrouping existing entries (OPD relabeling) is low-risk; adding entirely new top-level groups (IPD, OPERATIONS) is meaningless until the underlying module exists, and premature navigation for a non-existent module is explicitly listed as a thing not to do.

## Module Licensing / Enablement (real, current)

Three separate concepts, per `docs/decisions/ADR-003-MODULE-ENABLEMENT.md`:

- **Licensed** — a platform-level entitlement (`hospital_modules.licensed`). Only a platform administrator should grant this; today it's on the same ADMIN-only screen as Enabled because this app has no separate platform-owner role yet — the two underlying permissions (`module.manage_license` / `module.manage_enablement`) are already distinct, so adding a real platform role later is a `role_permissions` change, not a new screen.
- **Enabled** — the hospital admin's own on/off switch, reachable only once Licensed (DB `CHECK (enabled = FALSE OR licensed = TRUE)` — a hospital admin cannot self-grant a paid module even by manipulating the API directly).
- **Available** — derived, never stored: `licensed AND enabled`.

Covers three modules today: `LAB_RADIOLOGY`, `PHARMACY`, `PACKAGES` (`app/services/module_services.py`'s `MODULES` dict).

### Degradation (real, current)

Every module has exactly one degradation mode, defined in the same `MODULES` dict:

| Module | Degradation | What happens when unavailable |
|---|---|---|
| `LAB_RADIOLOGY` | `EXTERNAL` | Doctors route LAB/RADIOLOGY orders through `EXTERNAL_REFERRAL` instead; existing orders/results stay visible |
| `PHARMACY` | `BLOCKED` | New prescribing/dispensing is blocked; existing prescriptions/stock history stay visible |
| `PACKAGES` | `HIDDEN` | Creating packages / billing via a package is blocked; existing package-based charges/invoices stay untouched |

This exactly matches the target semantics (`HIDDEN`/`EXTERNAL`/`BLOCKED` as defined in `CLAUDE.md`): `HIDDEN` removes the UI capability, `EXTERNAL` keeps the clinical action available via an external path, `BLOCKED` genuinely prevents the action with an explanation. Toggling never deletes clinical history, orders, or results, and is always reversible — enforced by the fact that disabling only flips `hospital_modules.enabled`, never touches `orders`/`charges`/`prescriptions` rows.

## Gap

No module exists yet for a future `IPD` capability, so the framework above is proven for three OPD-adjacent modules but not yet exercised for the kind of module (an entire care setting, not a single capability) IPD would represent. `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` before building an `IPD` module entry: whether "degradation" even makes sense for a whole care setting the way it does for a single capability like Pharmacy, or whether IPD needs a different enablement shape (e.g. per-ward licensing) — this needs a design decision, not an assumption that it fits the existing three-module pattern unchanged.
