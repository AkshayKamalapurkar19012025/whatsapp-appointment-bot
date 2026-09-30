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

**Status: ✅ Done for the internal workflow granularity that was scoped; 🟡 the test/study catalog and imaging/PACS gaps remain, out of scope by design.** `migrations/0054_diagnostic_workflow.sql` resumed this phase: LAB orders now go through real sample collection (`lab_samples`, with reject/recollect), and both LAB/RADIOLOGY go through a real draft-result → verify (by a different staff account, ADMIN excepted) → release pipeline before a result is visible as final, instead of completing the instant a result is entered. PROCEDURE/SERVICE/EXTERNAL_REFERRAL orders are completely unaffected — they keep the original one-step lifecycle. See `docs/workflows/LABORATORY.md`/`docs/workflows/RADIOLOGY.md` for the full detail and what's still genuinely missing (a searchable test/study catalog; PACS/DICOM/image storage for Radiology, explicitly deferred).

## Phase 8 — Prescription + Pharmacy

**Status: ✅ Done.** Prescription → pharmacy dispensing connected, inventory-aware (stock/batch/expiry), real transaction log. See `docs/workflows/PHARMACY.md`.

## Phase 9 — Billing + Payment

**Status: ✅ Done.** Clinical services → charges → invoice → payment → receipt, fully connected, and the consultation-fee/itemized-billing ledger split (below) is closed. Gap: no modeled "payment pending/failed" intermediate state beyond the `DECLINED` invoice-payment status added in migration `0050` — verify this is sufficient before assuming a full payment-failure journey exists. See `docs/workflows/BILLING.md`.

**Billing ledger unification** (`docs/architecture/BILLING_LEDGERS.md`, `docs/architecture/BILLING_LEDGER_UNIFICATION.md`): the source-of-truth audit found two independently-maintained billing tables for the same visit (`appointments.payment_status`, the original consultation-fee mechanism, vs. `invoices`/`charges`/`payments`, the newer itemized model) with no connection between them. Three independently-developed pieces of work landed on main and are now reconciled:

- **Option C (read-side merge, `docs/architecture/BILLING_LEDGERS.md`)**: `GET /dashboard/billing` and the Exception Engine's `PAYMENT_PENDING` combine both ledgers at read time.
- **A real write-path unification (`docs/architecture/BILLING_LEDGER_UNIFICATION.md`, confusingly also self-titled "Phase 10" in its own doc — not the same as this file's own Phase 10, Patient 360, below; a real numbering collision between two independently-written docs, flagged rather than silently renumbered)**: `record_payment_service` now records the consultation fee as a real Ledger B charge/payment, linked via `appointments.consultation_payment_id`, with a full historical backfill (`migrations/0059`). This is what this doc used to call "Option A," done. Billing History/Payment History (previously Ledger B only) now see the consultation fee for the first time as a natural consequence, with no separate fix needed.
- **A visit closed with its consultation fee still unpaid is now collectible**, not just visible: `record_payment_service`/`waive_consultation_fee_service`/`settle_free_visit_service`'s shared `_lock_appointment_for_payment` guard accepts `COMPLETED` as well as `CHECKED_IN`, with queue-token issuance correctly skipped for the `COMPLETED` case (a token for a finished visit would be meaningless). See `tests/test_consultation_payments.py`'s `*_allowed_after_visit_completed` tests and `tests/test_exception_engine.py::test_payment_pending_exception_from_unpaid_consultation_fee_alone`'s ending.

**A real bug was found and fixed during reconciliation**: the read-side merge and the write-path unification were built independently and, when combined, double-counted every real consultation-fee payment (and, while an earlier, now-removed additive-mirror mechanism was also briefly in the mix, crashed every real payment outright with a `UniqueViolation`). See `docs/architecture/BILLING_LEDGERS.md`'s "Gap history" section for the full account and the fix.

**What remains, genuinely unstarted**: moving `generate_queue_token_service`'s own trigger off the legacy `outcome == 'PAID'` check — deliberately left alone given it's the most concurrency-sensitive path in the app.

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

## Phase 15 — AI Agent Automation Layer

**Status: 🟡 Phases 1-3 done (Phase 3: background execution -- DB-backed job queue, worker threads, crash recovery; Phase 1: read-only tools + controlled check-in; Phase 2: approval-gated fee settlement -- record payment / waive fee / settle free visit -- which issues the queue token); later phases not started.** A controlled task-automation layer above the existing services: strict Intake/Planner/Executor/Verifier contracts, a deterministic orchestrator, a formal tool registry, server-built authorization context, per-step human approval for high-risk tools, idempotent writes, and a full audit trail (`migrations/0060_agent_layer.sql`, `app/agent/`, `app/api/agent.py`). First slice: "check in today's 10:30 appointment for Ravi". See `docs/architecture/AI_AGENT_LAYER.md`, `docs/decisions/ADR-006-AI-AGENT-LAYER.md`, and the audit `docs/implementation/AI_AGENT_IMPLEMENTATION_AUDIT.md`. **Not done:** the offline Improver, any frontend, background execution, the offline Improver (next), and hardening of the existing endpoints the audit flags.

---

## Phases beyond the original 15 — not yet scoped

**IPD.** No phase number assigned yet — deliberately, since scoping it prematurely risks exactly the "implement future modules prematurely" anti-pattern `CLAUDE.md` names. See `docs/architecture/OPD_TO_IPD.md` for the architectural groundwork already laid (the decision to extend this database rather than stand up a separate service) and what a real phase would need to do.

**Emergency.** Not scoped at all — no schema groundwork exists (unlike IPD's reserved `encounter_type` slot and superseded-but-informative schema sketch).

## Recommended next step

Phase 7 (Diagnostics granularity) was resumed and completed for its scoped items — see its entry above and `docs/workflows/LABORATORY.md`/`docs/workflows/RADIOLOGY.md`. Phase 9's billing-ledger split is now fully closed — read-side merge (Option C), real write-path unification, and the COMPLETED-appointment collectibility gap all done and reconciled — see its entry above and `docs/architecture/BILLING_LEDGERS.md`/`docs/architecture/BILLING_LEDGER_UNIFICATION.md`. Of what remains:

- **Phase 11 (Command Center KPI gaps)** is now the next additive, non-risky phase — it extends screens that already exist and requires no new architectural decision.
- **`generate_queue_token_service`'s own trigger** (currently the legacy `outcome == 'PAID'` check, untouched by the write-path unification above) — moving it onto a ledger-B-native condition would be the remaining piece of a *complete* unification, deliberately not attempted: it is the most concurrency-sensitive path in the app and needs its own explicit scoping and go-ahead before any code is written.

**Do not start IPD or Emergency** until a phase is explicitly scoped for them with the questions in `docs/architecture/OPD_TO_IPD.md`'s Gap section answered first.
