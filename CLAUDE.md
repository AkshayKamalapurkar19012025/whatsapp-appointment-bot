# CLAUDE.md — HospitalOS

Primary instruction file for Claude Code sessions working in this repository. Read this first, then the relevant docs under `docs/` before touching code.

## Project

HospitalOS is a production-grade Hospital Information Management System (HIMS) for 100+ bed multi-speciality hospitals. **This repository already contains a substantial, working implementation** — OPD scheduling, walk-in registration, check-in/queue, triage, consultation, orders (lab/radiology/procedure/external-referral), prescriptions, pharmacy dispensing, billing/payments/receipts, patient timeline, role-based access, and module licensing all exist and are exercised by 683 backend tests as of this writing.

**HospitalOS evolves from the existing system. It is not rebuilt.** Every future phase starts by reading `docs/`, inspecting what's already there, and extending it. See `docs/product/PRODUCT_VISION.md` for the full current-state summary and `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` for the most recent line-by-line audit against the master spec (as of PR #112 — read it alongside the "what changed since" notes in `docs/product/PRODUCT_VISION.md`, since several of its gaps have since closed).

## Core product principle — one connected hospital workflow

HospitalOS is one connected hospital workflow, not a collection of independent pages. The fundamental journey:

```
Patient → UHID → Encounter → Appointment/Walk-in/Follow-up → Check-in → Queue/Token
  → Triage/Vitals → Consultation → Diagnosis → Orders → Results/Outcomes → Prescription
  → Pharmacy → Billing → Payment → Follow-up → Visit Completion → Patient 360
```

This is not strictly linear. After consultation, the workflow branches (Prescription / Laboratory / Radiology / Procedure / Pharmacy / Billing / Admission→IPD), but every branch stays connected to the same patient and the same encounter. See `docs/product/HIMS_WORKFLOW.md`.

Different hospital staff get different workspaces over the *same* underlying data (receptionist → OPD/Patient/Encounter; doctor → Queue/Patient/Encounter/Consultation; lab tech → Worklist/Order/Patient/Encounter; pharmacist → Pharmacy Queue/Prescription/Patient/Encounter; cashier → Billing Queue/Invoice/Patient/Encounter; admin → Command Center). All converge on the same source of truth. This repo already does this for OPD (`AdminApp.tsx`'s per-role sidebar/landing screen, `ROLE_LANDING_SECTION`) — extend it, don't fork it.

## Core domain principles (mandatory)

- **Patient is the permanent identity.** `patients.uhid` (format `HOS-NNNNNNN`, DB-generated) is the permanent key. Mobile number is a searchable/contact attribute, never the identity. Duplicate detection (`app/services/patient_duplicate_detection.py`) and patient merge (`app/services/patient_merge.py`) already exist — use them, don't build a second identity path. See `docs/decisions/ADR-001-PATIENT-IDENTITY.md`.
- **Encounter is the clinical context.** `encounters` (migration `0028`) connects appointment/check-in/queue/triage/consultation/diagnosis/orders/results/prescriptions/billing/follow-up. Every new clinical or financial table FKs to `encounter_id`, not to the appointment directly. See `docs/architecture/ENCOUNTER_MODEL.md` and `docs/decisions/ADR-002-ENCOUNTER-CENTRIC-DESIGN.md`.
- **One generic Order Spine, not per-department order tables.** `orders` (migration `0030`) with an `order_type` discriminator (`LAB`/`RADIOLOGY`/`PROCEDURE`/`SERVICE`/`EXTERNAL_REFERRAL`) and a generic `order_results` table. Laboratory/Radiology/Procedure workflows extend this model — they do not get their own disconnected order/result tables. See `docs/architecture/ORDER_SPINE.md`.
- **Module Licensing has three separate concepts: Licensed, Enabled, Available.** Already implemented (migrations `0052`/`0053`, `app/services/module_services.py`, `ModuleLicensingPanel.tsx`): Licensed is a platform entitlement, Enabled is the hospital admin's own switch (only reachable once licensed), Available is derived (`licensed AND enabled`, never stored). A hospital admin cannot self-grant a paid module — enforced by a DB `CHECK` constraint, not just application code. See `docs/decisions/ADR-003-MODULE-ENABLEMENT.md`.
- **Module degradation is one of HIDDEN / EXTERNAL / BLOCKED, never silent failure or deletion.** The `MODULES` dict in `app/services/module_services.py` already assigns each module a degradation mode (`LAB_RADIOLOGY` → `EXTERNAL`, `PHARMACY` → `BLOCKED`, `PACKAGES` → `HIDDEN`). Disabling a module never deletes clinical history, orders, or results, and is always reversible.

## Documentation structure

```
docs/
├── product/        — what HospitalOS is, the workflow, OPD scope, patient journey
├── architecture/   — domain model, encounter model, order spine, modules, OPD→IPD
├── ux/             — design system, screen map, navigation, UX principles
├── workflows/      — one detailed doc per workflow (registration → billing → 360)
├── printing/        — document/print subsystem, current vs target
├── implementation/ — phases, acceptance criteria, testing strategy
└── decisions/      — ADRs for the major architectural decisions
```

Every doc distinguishes **Current State** (what's actually in the repo, verified by reading code — not assumed) from **Target State** (what HospitalOS should become) from **Gap** (what needs to change) and, where useful, **Recommended Implementation**. Do not claim something is implemented merely because a spec says it should exist. Mark genuinely unverified areas `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` rather than guessing.

The pre-existing `docs/OPD_HIMS_P*.md` and `docs/WEB_P*.md` files are phase reports from earlier work — keep them as historical record, don't delete them. The new `docs/product/`, `docs/architecture/`, `docs/ux/`, `docs/workflows/`, `docs/printing/`, `docs/implementation/`, `docs/decisions/` structure is the durable, living source of truth going forward; update it as the system changes instead of writing a new one-off phase report for every change.

## What not to do

Do not: rebuild the application from scratch; replace the existing React/Vite admin frontend or the FastAPI/Postgres backend; rewrite working scheduling, queue/token, or booking-concurrency logic; create a second patient, appointment, encounter, or auth model; remove existing tests; silently change business behavior or database semantics; introduce microservices or complexity the current single-tenant, single-Postgres-instance scale doesn't need; implement future modules (IPD, Emergency) prematurely or as stubs presented as complete; hide historical clinical data when a module is disabled; make mobile number the patient identity; create separate patient identities per care setting (OPD/IPD/Emergency); create billing, lab/radiology, or pharmacy transactions disconnected from their encounter/order/prescription; add a navigation item merely because a page exists somewhere.

## Phase discipline

For every implementation phase: (1) read this file, (2) read the relevant `docs/` files, (3) inspect the existing implementation for that area, (4) identify what already exists vs. the gap, (5) update the design doc if the plan changes, (6) implement only the current phase, (7) run the existing test suite, (8) add new tests, (9) validate backward compatibility, (10) update the relevant docs, (11) report what was completed and what remains, (12) name the next phase. Never jump between phases. See `docs/implementation/PHASES.md`.

## Working agreements (git/GitHub)

- **Before pushing any commit to a branch tied to an existing PR, verify the PR isn't already merged/closed** (check via the GitHub API, not just local git state). A branch's PR can merge while work is still in flight; pushing more commits to it afterward strands those commits on a closed PR instead of landing them. If the PR did merge, don't reuse that branch for new work — rebase any not-yet-merged commits onto the latest default branch and push them under a new branch name (or a force-pushed reset of the old one, only with explicit approval, since that's a destructive git operation).
