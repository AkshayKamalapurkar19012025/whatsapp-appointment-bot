# Implementation Phases

## Purpose

The canonical phase sequence for HospitalOS going forward. Each phase below is marked with its actual current status in this repository — most of Phases 0-10 are substantially done already, since this is an evolving system, not a greenfield build. **Do not re-do a phase marked ✅ Done from scratch; extend it.** Follow the phase discipline in `CLAUDE.md` for whichever phase is picked up next.

## Phase 0 — Repository Audit

**Status: ✅ Done, repeatedly.** `docs/OPD_HIMS_P0_AUDIT.md` (initial), `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` (most recent full pass, as of PR #112), and this documentation set itself (as of the session that created it) are all Phase-0-type audits. Re-run a targeted version of this (inspect before modifying) at the start of every future phase — that's phase discipline, not a one-time gate.

## Phase 1 — Design System + Application Shell

**Status: ✅ Done.** `docs/design-system.md`/`docs/ux/DESIGN_SYSTEM.md` (tokens, components), sidebar/topbar shell, `GlobalSearchBar.tsx`, `NotificationBell.tsx`, RBAC (`require_permission()`). Gap: `PatientHeader`/`StatusBadge`/`Timeline` not yet componentized — see `docs/ux/DESIGN_SYSTEM.md`.

## Phase 2 — Patient Identity

**Status: ✅ Done.** UHID, duplicate detection, patient search, registration, merge (goes beyond "readiness" into a working feature). See `docs/workflows/PATIENT_REGISTRATION.md`, `docs/decisions/ADR-001-PATIENT-IDENTITY.md`.

## Phase 3 — Encounter Foundation

**Status: ✅ Done for OPD.** `encounters` table, OPD encounter, patient/encounter relationship. Gap: `encounter_type` CHECK constraint only allows `'OPD'` — see `docs/architecture/ENCOUNTER_MODEL.md`.

## Phase 4 — Check-in + Queue

**Status: ✅ Done.** Arrival, check-in, token, queue, waiting/in-consultation (derived, not stored), completed. Concurrency protections preserved throughout — see `docs/workflows/OPD_CHECKIN_QUEUE.md`.

## Phase 5 — Triage + Consultation

**Status: ✅ Done.** Vitals, clinical notes, diagnosis, assessment, plan, consultation completion + controlled amendment workflow. Gap: no allergy/current-medication surfacing in the consultation UI itself. See `docs/workflows/CONSULTATION.md`.

## Phase 6 — Order Spine

**Status: ✅ Done.** `Encounter → Order → Order lifecycle → Result/Outcome`, one generic table for every order type. See `docs/architecture/ORDER_SPINE.md`.

## Phase 7 — Diagnostics (Laboratory and Radiology)

**Status: 🟡 Partial.** The generic order spine covers both; type-specific workflow granularity (collection tracking, verify-then-release, structured radiology reporting) does not exist. See `docs/workflows/LABORATORY.md`, `docs/workflows/RADIOLOGY.md` for the scoped gap list.

## Phase 8 — Prescription + Pharmacy

**Status: ✅ Done.** Prescription → pharmacy dispensing connected, inventory-aware (stock/batch/expiry), real transaction log. See `docs/workflows/PHARMACY.md`.

## Phase 9 — Billing + Payment

**Status: ✅ Done.** Clinical services → charges → invoice → payment → receipt, fully connected. Gap: no modeled "payment pending/failed" intermediate state beyond the `DECLINED` invoice-payment status added in migration `0050` — verify this is sufficient before assuming a full payment-failure journey exists. See `docs/workflows/BILLING.md`.

## Phase 10 — Patient 360

**Status: ✅ Done as a screen; 🟡 gap on reachability.** Unified cross-domain timeline exists (`PatientTimelineModal.tsx`) but isn't viewable inline during an active consultation. See `docs/workflows/PATIENT_360.md`.

## Phase 11 — Hospital Command Center

**Status: 🟡 Partial.** `DashboardPanel.tsx` has real KPIs (today's/upcoming/pending/confirmed/cancelled/completed counts, a billing-collections chart) and the Exception Engine (six live-computed, actionable operational exceptions) covers "bottlenecks"/"work queues"/"exceptions" in spirit. Missing: Average Waiting Time / Average Consultation Time as Dashboard KPI cards (wait time is computed but shown elsewhere), department/doctor-load breakdown. See `docs/architecture/MODULE_ARCHITECTURE.md`.

## Phase 12 — Production Hardening

**Status: 🟡 Mostly done, in pieces across sessions rather than one dedicated pass.** Security (RBAC, `require_permission()` everywhere), authorization, concurrency (`FOR UPDATE`, advisory locks, exclusion constraints, tested), idempotency (unique constraints, `ON CONFLICT` patterns), pagination (admin list endpoints), indexes, in-process caching, dependency security (`pip-audit`, 2 CVEs fixed), accessibility (0 axe-core violations across 13 screens), responsive (a real bug found and fixed), error handling (`{success, errorCode, message, details}` envelope) are all real. Not yet done as a *complete* pass: observability beyond structured logging, backups/reliability planning, a systematic (not spot-checked) review of every screen against the full "13 production-UX questions" list.

## Phase 13 — Printing & Document Management

**Status: 🟡 Partial.** ~8 of ~16 named document types exist with real print layouts; infrastructure (branding config, paper-size config, versioning, document-history/print-audit view) doesn't exist yet. See `docs/printing/DOCUMENT_MANAGEMENT.md`.

## Phase 14 — Full End-to-End Validation

**Status: 🟡 Exercised piecemeal, never run as one continuous, scripted pass.** Most individual steps of the full patient journey have been verified live in a browser during their own feature phase; the specific 38-step (or equivalent) single-sitting acceptance run described in `docs/implementation/ACCEPTANCE_CRITERIA.md` has not been executed as its own dedicated exercise.

---

## Phases beyond the original 15 — not yet scoped

**IPD.** No phase number assigned yet — deliberately, since scoping it prematurely risks exactly the "implement future modules prematurely" anti-pattern `CLAUDE.md` names. See `docs/architecture/OPD_TO_IPD.md` for the architectural groundwork already laid (the decision to extend this database rather than stand up a separate service) and what a real phase would need to do.

**Emergency.** Not scoped at all — no schema groundwork exists (unlike IPD's reserved `encounter_type` slot and superseded-but-informative schema sketch).

## Recommended next step

Per the audit and this documentation pass: **Phase 11 (Command Center KPI gaps) and Phase 7 (Diagnostics granularity)** are the two most concretely scoped, additive, non-risky next phases if hospital operations feedback calls for them. Neither requires a new architectural decision — both extend tables/screens that already exist. **Do not start IPD or Emergency** until a phase is explicitly scoped for them with the questions in `docs/architecture/OPD_TO_IPD.md`'s Gap section answered first.

This documentation-baselining work itself does not conclude with a "start Phase X" instruction — see the final audit report delivered alongside this doc for the explicit "do not implement the next phase yet" instruction that governs this specific session.
