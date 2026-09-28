# HospitalOS — OPD Architectural Gap & Target Architecture Audit

**Scope discipline**: this document is audit + architecture decisions + migration planning only. No migration, API, backend, or frontend code was changed to produce it. Per the master prompt's stop condition, implementation waits for explicit approval of the decisions below.

**Method**: every claim below was checked against the actual code on this branch (migrations, services, API routes, frontend components, tests), not recalled from the existing `docs/` set alone. Where an existing doc's claim was re-verified and matched, it's cited as corroboration. Where an existing doc's claim was found stale or wrong, that is called out explicitly — source code wins, per this task's authority hierarchy — and both the doc and the correction are recorded so the doc can be fixed later.

---

## 1. Executive Summary

**Verdict: safe to continue, with conditions.** The existing OPD implementation is not a fragile prototype that needs rework before it can carry more weight — the encounter-centric spine, the generic order model, and the concurrency discipline are all genuinely sound and should not be touched. But three specific architectural decisions are currently unmade, and building IPD, Emergency, internal referral, or real insurance billing directly on top of the current state — without making those decisions first — would compound problems that are currently small and contained into ones that are expensive to unwind later.

**The one finding that changes the shape of "what's safe to build next"**: this system currently has **two separate, unreconciled financial ledgers** for the same visit — an old appointment-level consultation-fee mechanism (`appointments.payment_status`, migrations `0018`/`0019`/`0025`) and a newer encounter-level invoice/charge/payment model (`invoices`/`charges`/`payments`, migration `0033`). Migration `0033`'s own header documents this was a deliberate, load-bearing decision at the time ("deliberately separate from — and non-invasive to — the existing mechanism... rewriting it to absorb orders/pharmacy charges was considered and rejected"), and it was the right call for shipping Phase 9 without touching the highest-concurrency code path in the app. But it means there is no single canonical invoice per visit today, and every future billing-adjacent feature (packages, insurance/TPA, IPD billing, refund reconciliation) either has to pick one ledger and ignore the other, or inherit the ambiguity. This is the one P0 decision in this audit — not because the current OPD system is broken by it (it isn't; both ledgers work correctly on their own terms), but because every subsequent phase that touches money makes the eventual unification more expensive if it's deferred again.

**Two more decisions are P1 — they don't block continuing OPD work, but they must be made before the specific next phases that depend on them start**: (1) `encounters.encounter_type` widening and a real answer to "can a patient have two simultaneous OPEN encounters" (today: yes, nothing prevents it — fine for OPD-only, a real question the moment IPD/Emergency can coexist with an open OPD encounter); (2) pharmacy's dispense-only model has no medication-administration (MAR) concept at all, which IPD will need and OPD never has needed.

**Everything else found is P2/P3**: real, worth fixing, not blocking. An inconsistent audit-log coverage gap (billing voids/refunds and consultation amendments are permission-gated but never write to the generic `audit_logs` table, even though some have their own stronger dedicated history tables). A frontend/backend mismatch where the Pharmacy/Packages/Lab Worklist sidebar entries are not actually gated by module availability on the frontend for ADMIN/STAFF sessions, contradicting `docs/ux/NAVIGATION.md`'s own claim — a real doc-vs-code gap, but not a security hole, since the backend service layer (confirmed: `create_order_service`) enforces `is_module_available()` independently. Master-data gaps (no Ward/Bed/Insurance-Provider model) that are simply absent because nothing has needed them yet — correct, not a defect.

**What must not change**: the `patients → encounters → {orders, prescriptions, charges, invoices, vitals, consultations}` FK spine, the one-generic-`orders`-table pattern, the advisory-lock + exclusion-constraint concurrency architecture, the Licensed/Enabled/Available module model, and the newly-verified-real RBAC layer. These are this codebase's actual competitive strengths relative to a "generic modern HIMS," and the recommendation in this audit is to extend them, not replace them.

---

## 2. Repository Evidence

**Migrations inspected** (of 65 total, `migrations/0001`–`0057`): `0001` (baseline schema), `0003` (overlap exclusion constraint), `0011`/`0012`/`0015`/`0018`/`0019`/`0024`/`0025`/`0026`/`0027`/`0028`/`0029` (×2)/`0030`/`0031` (×2)/`0032` (×2)/`0033`/`0034`/`0035`/`0036`/`0037`/`0038`/`0039`/`0040`/`0043`/`0046`/`0048` (×2)/`0050`/`0051`/`0052`/`0053`/`0054` (×2)/`0055`/`0056`/`0057`.

**Services inspected**: `appointment_services.py`, `availability_engine.py`, `patient_timeline_service.py`, `visit_completion_service.py`, `patient_merge.py`, `patient_duplicate_detection.py`, `patient_identifiers.py`, `uhid.py`, `order_services.py`, `pharmacy_services.py`, `medication_services.py`, `allergy_check_service.py`, `fhir_mappers.py`, `clinical_services.py`, `billing_services.py`, `billing_history_service.py`, `module_services.py`, `audit_log.py`, `staff_auth.py`, `staff_management.py`, `exception_engine.py`.

**APIs inspected**: `patients.py`, `appointments.py`, `scheduling.py`, `queue_display.py`, `orders.py`, `pharmacy.py`, `clinical.py`, `billing.py`, `billing_history.py`, `packages.py`, `appointment_types.py`, `department_appointment_types.py`, `doctor_appointment_types.py`, `departments.py`, `doctors.py`, `module_licensing.py`, `audit_log.py`, `staff_auth.py`, `app_config.py`, `fhir.py`.

**Frontend inspected**: `AdminApp.tsx` (563 lines), `ConsultationWorkspace.tsx` (1590 lines — the largest component in the app), `AppointmentBillingPanel.tsx` (790 lines), `LabRadiologyWorklistPanel.tsx` (479 lines), `PatientTimelineModal.tsx` (273 lines), `BillingPanel.tsx`/`BillingHistoryPanel.tsx`/`PaymentHistoryPanel.tsx`, `ModuleLicensingPanel.tsx`, `AdminSidebar.tsx` (63 lines), `PrescriptionPanel.tsx`, `PharmacyPanel.tsx`, `MedicationPicker.tsx`.

**Tests inspected/counted**: 791 test functions confirmed by direct count (`grep -rE "^def test_" tests/*.py | wc -l`) across 78 files, including targeted reads of `test_encounters.py`, `test_visit_completion_checklist.py`, `test_queue_hold_and_priority.py`, `test_concurrency.py`, `test_concurrency_hardening.py`, `test_exclusion_constraint.py`, `test_patient_creation_race.py`, `test_orders.py`, `test_order_results.py`, `test_lab_workflow.py`, `test_radiology_workflow.py`, `test_pharmacy.py`, `test_medication_master.py`, `test_allergy_check.py`, `test_fhir.py`, `test_billing_invoices.py`, `test_billing_history.py`, `test_packages.py`, `test_role_based_access.py`, `test_module_licensing.py`, `test_admin_rbac.py`.

**Documentation inspected**: the entire `docs/` tree — `docs/product/*`, `docs/architecture/*`, `docs/decisions/ADR-001` through `ADR-005`, `docs/workflows/*` (9 files), `docs/ux/*` (4 files), `docs/implementation/*` (3 files), `docs/printing/DOCUMENT_MANAGEMENT.md`, `docs/DATABASE_P1_NOTES.md`, and `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` (the prior full pass, as of PR #112).

**Non-repository evidence**: `git log` (confirms Phases through FHIR/ABDM/diagnostic-workflow work, PR #119 as the most recent merge), `ipd-service/schema/0001_baseline_ipd_schema.sql` (superseded sketch, read for its table shapes only).

---

## 3. Current Architecture

### 3.1 System architecture

A single FastAPI + Postgres monolith serving three client surfaces over the same database and the same service layer: a WhatsApp conversational booking flow (`app/api/scheduling.py`, the original product), a patient-facing web app, and a staff/admin React/Vite SPA (`frontend/src/admin`). `hospital_id` is present on most tables (migration `0027`) but the system is single-tenant in practice — this is "purely structural" multi-tenancy, not exercised or tested as multi-tenant today.

### 3.2 Domain architecture — the verified spine

```
patients (uhid, permanent identity)
   │ 1:N (one encounter per appointment, always — see §5.2)
   ▼
encounters (encounter_type='OPD' only; status OPEN/CLOSED, driven one-directionally by appointment status)
   │
   ├─▶ appointments (scheduling lifecycle: PENDING/CONFIRMED/REJECTED/CANCELLED/CHECKED_IN/COMPLETED/NO_SHOW)
   ├─▶ vitals
   ├─▶ consultations ──▶ consultation_amendments (archive-then-update, post-completion only)
   ├─▶ orders (order_type: LAB/RADIOLOGY/PROCEDURE/SERVICE/EXTERNAL_REFERRAL)
   │      ├─▶ order_results (generic parameter/value; +unit_system/unit_code, optional)
   │      └─▶ lab_samples (LAB only; collect→reject→recollect, rows never deleted)
   ├─▶ prescriptions ──▶ prescription_items ──▶ pharmacy_dispense_records
   ├─▶ charges (Ledger B; source_type discriminator)
   │      └─▶ invoices (1:1 with encounter) ──▶ payments (1:N, each payment settles exactly one invoice)
   └── (Ledger A, on the appointment, NOT on the encounter): payment_status/payment_amount/refund_*, invoice_line_items
```

Every clinical/financial table added since migration `0028` FKs to `encounter_id`, confirmed directly (not assumed) for `vitals`, `consultations`, `orders`, `prescriptions`, `charges`, `invoices`. This is real, not aspirational — the single most important fact this audit re-confirms.

### 3.3 Database relationships (cardinality, verified)

| Relationship | Cardinality | Evidence |
|---|---|---|
| Appointment → Encounter | Exactly one, always, created in the same transaction | `appointment_services.py` `create_appointment_service` inserts `encounters` then writes `encounter_id` onto the new appointment row in one call |
| Patient → Encounters | One-to-many, **no limit on simultaneous OPEN ones** | No unique/partial index on `(patient_id) WHERE status='OPEN'`; `test_two_appointments_for_the_same_patient_get_two_encounters` confirms two OPEN encounters coexist today |
| Encounter → Invoice (Ledger B) | Exactly one | `invoices.encounter_id UNIQUE REFERENCES encounters(id)`, plus `ON CONFLICT (encounter_id) DO NOTHING` |
| Invoice → Payments | One-to-many | `payments.invoice_id NOT NULL`, no uniqueness — "multiple rows support genuine partial payment over time" (migration comment) |
| Payment → Invoice | Exactly one (no junction table) | single `invoice_id` FK; a payment cannot settle multiple invoices/charges |
| Order → Order Results | One-to-many | `order_results.order_id`, sequence-numbered batch inserts |
| Order → Lab Samples | One-to-many (LAB only) | one row per collection *attempt*, rejected rows kept |
| Prescription → Prescription Items → Dispense Records | 1:N:N | dispensing tracked as a running total (`quantity_dispensed`) plus an append-only transaction ledger, not a status enum |

### 3.4 Workflow / state-machine architecture

| Entity | States | Enforced by DB CHECK? | Transition owner |
|---|---|---|---|
| `appointments.status` | `PENDING, CONFIRMED, REJECTED, CANCELLED, CHECKED_IN, COMPLETED, NO_SHOW` | **No** — plain `TEXT`, documented only by a comment (unlike `payment_status`, which does have a CHECK) | `appointment_services.py`'s `_transition_appointment_status` and named service functions, each under `SELECT ... FOR UPDATE` |
| `encounters.status` | `OPEN, CLOSED` | Yes | Exclusively `_close_encounter_for_appointment`, fired only when the linked appointment reaches a terminal status; **no code path reopens a closed encounter** |
| `orders.status` | `ORDERED → COLLECTED(LAB) → IN_PROGRESS → RESULT_ENTERED → VERIFIED → COMPLETED`, or `CANCELLED` from any non-terminal state | Yes, with a type-conditional CHECK tying `COLLECTED/RESULT_ENTERED/VERIFIED` to `LAB`/`RADIOLOGY` | `order_services.py`, every transition under row lock |
| `prescriptions.status` | `DRAFT → PRESCRIBED → CANCELLED` (dispense tracked at item level, not as a status) | Yes | `pharmacy_services.py` |
| `invoices` / `payments` (Ledger B) | `invoices.status`: `OPEN/VOID`; `payments.status`: `COMPLETED/VOIDED/DECLINED` (migration `0050`); `payment_status` is a **computed**, not stored, read-time value (`UNPAID/PARTIALLY_PAID/PAID`) | Yes on the stored columns | `billing_services.py`, every mutation under `FOR UPDATE` |
| `consultations.status` | draft → `COMPLETED` (locked) → amendable only via a dedicated, RBAC-gated, archive-then-update path | Application-enforced | `clinical_services.py` |

No generic workflow engine exists, and none is needed at current scale — every state machine above is a plain column plus row-locked service functions, which is proportionate to the actual complexity (see ADR-012).

### 3.5 Frontend architecture

`AdminApp.tsx` is the single hub for navigation, role-based section visibility (`ROLE_VISIBLE_SECTIONS`, a hardcoded `Record<StaffRole, Set<Section>>>` — string comparison, not a permissions array from the backend), and post-login landing (`ROLE_LANDING_SECTION`). `ConsultationWorkspace.tsx` (1590 lines) and `AppointmentBillingPanel.tsx` (790 lines) are by a wide margin the largest components — both are legitimately tab-heavy workspaces, not obviously mis-scoped, but `ConsultationWorkspace.tsx` is the single largest concentration of business logic in the frontend (it independently computes a `readOnly` gate from `encounter.appointment_status`/`consultation.status`, i.e. the frontend re-derives a state-transition rule rather than only reflecting a backend-provided flag). Reuse is strong at the CSS-class level (`.pill status-*`, `.patient-context-meta`) and weak at the component level — confirmed directly: no `PatientHeader`, `StatusBadge`, or `Timeline` component exists anywhere; the patient-context header and every status pill are independently re-implemented per screen (~20 files for status pills alone). Billing has four separate panels (`BillingPanel`, `BillingHistoryPanel`, `PaymentHistoryPanel`, `AppointmentBillingPanel`) each calling its own API functions directly, with no shared data hook.

### 3.6 Authorization architecture

`roles` / `permissions` / `role_permissions` (many-to-many) + `staff_roles`. `require_permission(name)` is a FastAPI dependency joining `staff_roles → role_permissions → permissions`, or an active `break_glass_grants` row. Confirmed genuinely enforced, not just declared: `test_role_based_access.py` creates live staff accounts under DOCTOR/NURSE/RECEPTIONIST/LAB_TECH/PHARMACIST/BILLING and exercises real 200-vs-403 HTTP outcomes against real endpoints (e.g. pharmacist can manage stock, staff cannot; doctor can prescribe, billing cannot). Every mutating endpoint sampled across ten different `api/*.py` files calls `require_permission()`; GET-only routers deliberately use authentication-only, by their own file-header comments. Module licensing enforces "cannot self-grant a paid module" at the database layer (`CHECK (enabled = FALSE OR licensed = TRUE)` on `hospital_modules`), not merely in application code. Frontend authorization is confirmed to be UX-only for both sampled sensitive actions (void payment, module-licensing change) — a modified client gains nothing, since the server independently re-checks.

---

## 4. Architectural Strengths — do not change these

1. **The encounter-centric FK spine is real, DB-enforced, and load-bearing** — not marketing language in a doc. Every clinical/financial table added since Phase 3 FKs to `encounter_id`. This is the single most important asset this audit found and the strongest argument for "evolve, don't rebuild."
2. **The generic Order Spine survived a full diagnostics phase (Phase 7) without forking.** Lab's real sample-collection lifecycle and Radiology's Findings/Impression/Technique report both landed as *values*, not new tables — a genuinely difficult discipline to maintain under feature pressure, and it worked.
3. **Concurrency architecture is unusually mature for this codebase's size**, and it is battle-tested, not theoretical: a real cross-path double-booking race was reproduced live (19/20 attempts) and is now closed by a two-layer defense — a unified `pg_advisory_xact_lock(doctor_id)` across both the WhatsApp and REST booking paths, backed by a Postgres `EXCLUDE USING gist` constraint as a database-level backstop that doesn't care which code path (or future bug) inserted the row. The same `FOR UPDATE`-row-lock discipline is applied consistently across queue-token generation, billing, pharmacy dispense, and consultation amendment.
4. **Module Licensing's three-concept model (Licensed/Enabled/Available) is real and DB-enforced**, not just documented: `CHECK (enabled = FALSE OR licensed = TRUE)` holds regardless of which code path attempts the write, and degradation (`EXTERNAL`/`BLOCKED`/`HIDDEN`) never deletes historical rows — confirmed by reading `module_services.py` directly, not assumed from `docs/architecture/MODULE_ARCHITECTURE.md`.
5. **RBAC is genuinely role-differentiated in the backend today**, resolving a contradiction between two existing docs in favor of the newer one: `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` (PR #112) called this "aspirational," but migrations `0043`/`0048` and `test_role_based_access.py`'s real HTTP-level 200-vs-403 tests prove it shipped for real in PR #114 and after.
6. **Migration discipline is genuinely additive.** 65 migrations, zero destructive changes found; `scripts/migrate.py` takes an advisory lock for multi-instance safety.
7. **Restraint on terminology/coding was exercised correctly, twice.** Diagnosis coding (migration `0056`) and order-result unit coding (migration `0057`) both add optional, instance-level, never-auto-populated columns rather than inventing a fake terminology catalog or guessing codes — exactly the discipline a system without a real terminology source should show.
8. **The allergy safety check is a real, tested clinical-safety feature**, not a stub: substring-matched, soft-blocking (never silently applied, never a hard unconditional refusal), fully audited on shown/overridden/cancelled, re-checked on every resubmission.
9. **Patient identity is genuinely durable**: UHID-based, duplicate detection at registration time, a real (not "readiness-only") merge/unmerge workflow with its own safety checks.

---

## 5. Architectural Gaps

| # | Gap | Evidence | Impact | Priority |
|---|---|---|---|---|
| 1 | Two unreconciled billing ledgers per visit (appointment-level consultation fee vs. encounter-level invoice) | Migration `0033`'s own header; `billing_services.py` never reads `appointments.payment_status`; `billing_history_service.py` never surfaces Ledger A payments | No single canonical bill per visit; every future billing feature must pick a side or inherit the ambiguity; blocks clean IPD/insurance billing | **P0** |
| 2 | `hospital_modules`' single-flag-per-module shape is unproven for a whole care setting (a future `IPD` "module") | `docs/architecture/MODULE_ARCHITECTURE.md`'s own open question, re-confirmed against `module_services.py`'s `MODULES` dict (3 capability-shaped modules only) | Building an `IPD` module entry on the current shape without deciding this first risks a mismatched abstraction (ward-level licensing vs. a single hospital-wide flag) | **P1** |
| 3 | No medication-administration (MAR) model — pharmacy is dispense-only | Grep across migrations/services for "administer": zero matches; only `pharmacy_dispense_records` exists | IPD needs "nurse gave this dose to this inpatient," which is a distinct event from "pharmacy handed over the medicine" — must be designed before IPD pharmacy, not bolted on after | **P1** |
| 4 | `encounters.encounter_type` CHECK allows only `'OPD'`; no policy on simultaneous OPEN encounters per patient | `migrations/0028`; no unique/partial index on `(patient_id) WHERE status='OPEN'`; confirmed two OPEN encounters can coexist today, untested against IPD-concurrent-with-OPD | Widening the CHECK is a one-line migration, but doing it without first deciding whether an OPD and an IPD encounter can be open for the same patient simultaneously (almost certainly yes in reality — a patient can be admitted while an old OPD follow-up is still nominally open) risks silently wrong Patient-360/billing aggregation later | **P1** |
| 5 | Audit-log coverage is inconsistent for sensitive, permission-gated actions | `record_audit_log` is called from only ~12 of the API files; confirmed **not** called from `billing.py` (void_invoice/add_charge/void_charge/void_payment/refund_payment/update_invoice_terms) or from `clinical.py`'s `amend_consultation` | A billing void/refund or a consultation amendment is exactly the class of action a compliance review will ask for in `audit_logs`; some of these already have a *stronger* dedicated history table (`consultation_amendments`), but that's inconsistent, not a substitute for a uniform trail | **P1** |
| 6 | Frontend sidebar does not gate Pharmacy/Packages/Lab Worklist by module availability for ADMIN/STAFF sessions, contradicting `docs/ux/NAVIGATION.md`'s own claim | `AdminApp.tsx`'s `menuItems` entries for these three are defined unconditionally; no `available`/`license`/`degradation` reference found inside `PharmacyPanel.tsx`/`LabRadiologyWorklistPanel.tsx` | Confusing UX (a disabled module's screen still appears in the sidebar) but **not** a security gap — `create_order_service` and pharmacy dispensing independently call `is_module_available()` server-side | **P2** |
| 7 | No internal referral model exists — not even a stub | Grep confirms no `referrals`/internal-referral concept anywhere; `docs/architecture/OPD_TO_IPD.md`/master spec both note External Referral was built first-class while internal referral was not | Blocks a genuine multi-speciality workflow (Cardiology → Orthopedics handoff) that a 100+ bed hospital will need; requires an explicit encounter-linkage decision (see ADR-008) before any implementation | **P1** (relative to a referral phase specifically; not urgent otherwise) |
| 8 | No Ward/Room/Bed master data at all | Grep across all migrations for `ward\|room\|bed`: zero matches | Expected and correct for an OPD-only system today; a hard blocker only for IPD, which is not yet scoped | **P0 relative to IPD**, **P3 relative to continuing OPD** |
| 9 | No Insurance Provider/TPA identity model beyond a bare `bill_type` classification label | `invoices.bill_type` enum only (`CASH/SELF_PAY/CORPORATE/INSURANCE/TPA/GOVERNMENT_SCHEME`); no provider/policy/pre-auth/claim table or columns anywhere — confirmed deliberate per migration `0039`'s own header | Fine for OPD continuation; blocks any real insurance/TPA claims workflow | **P2** |
| 10 | `appointments.status` has no DB CHECK constraint (unlike every other status column in the system) | Confirmed: plain `TEXT`, documented only by a migration comment | Cheap, safe, additive fix; low current risk since application code is disciplined, but it's the one status column in the whole schema without a DB-level backstop | **P2** |
| 11 | No searchable test/service/lab/radiology catalog | `orders.description` is free text for every type; confirmed no catalog table exists anywhere, independently re-confirmed by three separate audit phases in this repo's own history | Blocks real pricing-driven ordering, LOINC/parameter coding, and any future insurance-claims itemization; correctly deferred so far (no real requirement has forced it yet) | **P2/P3** |
| 12 | Frontend re-derives clinical/financial business rules instead of only reflecting backend state | `ConsultationWorkspace.tsx`'s `readOnly` gate independently encodes "editable only if `CHECKED_IN` and not `COMPLETED`"; `AppointmentBillingPanel.tsx` independently encodes "can't submit a payment over balance" | Drift risk: if a backend transition rule changes, there are now two places to update, and they are not automatically kept in sync by any shared contract | **P2** |
| 13 | No `PatientHeader`/`StatusBadge`/`Timeline` shared frontend components | Confirmed: ~20 independent inline `pill status-*` implementations, 2+ independent patient-header markup blocks, one bespoke non-reusable timeline renderer | Pure UX/maintainability debt; does not block backend architecture work; explicitly should wait until a phase has two real call sites to design the shared API against (already the recommendation in `docs/ux/DESIGN_SYSTEM.md`, re-confirmed) | **P3** |
| 14 | Patient 360 is reachable only as a modal, not inline during an active consultation | Confirmed: `PatientTimelineModal.tsx` is imported only by `PatientsPanel.tsx`/`GlobalSearchBar.tsx`; zero references inside `ConsultationWorkspace.tsx` | Real UX gap against the master spec's "without leaving the workflow" requirement; purely additive fix (reuse the existing service/modal's data-fetching, no new backend work) | **P3** |
| 15 | Packages can be billed more than once against the same encounter with no link back to the orders they conceptually cover | Migration `0038`'s own header: deliberate, by design, no uniqueness index on `source_package_id`; `test_bill_a_package_can_be_billed_more_than_once` confirms this is intentional, tested behavior | Correct for today's usage pattern (a package is a flat priced bundle, not derived from itemized orders) but is a double-billing-adjacent risk if staff misuse it — worth a UI confirmation step, not a schema change | **P3** |
| 16 | No idempotency-key mechanism anywhere in the API | Confirmed by grep: zero matches for any `Idempotency-Key` handling | Currently fully compensated for by DB unique/exclusion constraints (`payments.transaction_id`, booking's exclusion constraint, etc.) — works today because all current clients are trusted, synchronous, browser-driven UIs | **P3** (becomes P1 the moment an async/webhook-driven integration — payment gateway, insurance TPA API — is added) |
| 17 | No configuration-matrix test suite validating the full patient journey under OPD-only / OPD+Lab / OPD+Pharmacy / full configurations | `docs/implementation/TESTING_STRATEGY.md`'s own named gap, re-confirmed: `test_module_licensing.py` tests the toggle mechanism itself, not the end-to-end degraded journey | Real testing gap that matters more the moment a second module-gated capability (IPD) exists alongside the first three | **P2** |
| 18 | FHIR layer is export/read-only; no write/import path | `fhir_mappers.py`'s own docstring: "No database access happens here"; `app/api/fhir.py` exposes only GET routes | Correct scope for the phase that built it; flag only if/when ABDM or an insurance integration needs writes | **P3** |

---

## 6. Target Architecture

### 6.1 Domain model — confirmed shape, not the master prompt's assumed one

The master prompt's suggested target (§27) assumed a `Charges/Invoice/Payments/Refunds` financial subtree hanging cleanly off one `Financial` node per encounter. The actual target, given the evidence, needs one more level of honesty: **that clean subtree already exists (Ledger B) — but it currently coexists with a second, older one (Ledger A) that the target architecture must retire, not ignore.**

```
Patient (uhid)
   │
   ├── Appointments (scheduling object; carries the LEGACY consultation-fee
   │                  ledger fields, target: fold into Financial below)
   │
   └── Encounters (encounter_type: OPD | IPD* | EMERGENCY*  — *target, not built)
          │
          ├── Clinical Activities
          │      ├── Consultation (+ Amendments)
          │      ├── Vitals
          │      └── (target) Internal Referral — links to a NEW linked encounter,
          │            not the same encounter (see ADR-008)
          │
          ├── Orders (LAB / RADIOLOGY / PROCEDURE / SERVICE / EXTERNAL_REFERRAL /
          │           (target) INTERNAL_REFERRAL / NURSING / DIET for IPD)
          │      └── Order Results (generic; Lab adds Samples as the one real
          │                          child entity; Radiology adds nothing new)
          │
          ├── Prescriptions → Prescription Items → Dispense Records
          │      └── (target, IPD-only) Medication Administrations — a NEW,
          │            separate event type, not a dispense-record variant
          │
          └── Financial (target: ONE canonical model)
                 ├── Charges (source_type, already generic — extend, don't fork)
                 ├── Invoice (1:1 with encounter, already real)
                 ├── Payments (already real; refund is per-payment, already real)
                 └── (target) the appointment-level consultation fee becomes a
                       Charge with source_type='CONSULTATION' at encounter-open
                       time, and Ledger A's dedicated columns are deprecated via
                       expand/contract, not deleted in place
```

### 6.2 Module boundaries (target, unchanged in shape from current — see §3 of `docs/architecture/MODULE_ARCHITECTURE.md`, re-confirmed accurate)

`Patient Management` / `Scheduling` / `Encounter` / `Queue` / `Clinical` / `Diagnostics` / `Pharmacy` / `Billing` / `Referral` (target) / `Reporting` / `Administration` / `Printing` / `Audit`. No circular dependency was found among these in the backend service layer (routes are thin, services own logic, confirmed by direct sampling of ten API files). The one boundary concern found is not circular dependency but **duplication**: Ledger A's billing logic (`appointment_services.py`'s payment/waiver/refund functions) and Ledger B's billing logic (`billing_services.py`) are two independent implementations of overlapping concepts (payment, refund) that don't call each other or share validation — this is the concrete manifestation of Gap #1 at the module-boundary level.

### 6.3 Financial architecture (target)

One canonical invoice per encounter (already true for Ledger B). The consultation fee becomes a `charges` row with `source_type = 'CONSULTATION'`, created at the same point Ledger A currently sets `payment_status`. `appointments.payment_status`/`payment_amount`/`refund_*`/`invoice_line_items` become read-only legacy columns during a migration window (expand/contract), then are dropped once no code reads them. Discounts stay invoice-level (already correct — a charge-level discount was never actually needed by any real workflow found). Refunds stay per-payment with an accumulating cap (already correct). Insurance/TPA gets a real `insurance_providers`/`policies` master and per-invoice `payer_id`/`policy_number`/`pre_auth_number`/`claim_status` columns — additive, only when that phase is actually scoped (do not add empty columns speculatively, per this codebase's own established discipline).

### 6.4 Diagnostic architecture (target — already achieved, preserve)

No change recommended. The shared `orders`/`order_results` spine already proved it can carry Lab's sample lifecycle and Radiology's structured report without forking. The one real remaining gap (a test/study catalog) should be added as an optional `catalog_item_id` FK on `orders`, never as new `lab_orders`/`radiology_orders` tables.

### 6.5 Referral architecture (target — net new)

**Decision: an internal referral creates a new, linked encounter — not the same encounter, and not a fully disconnected one.** Reasoning: the same principle that made "one encounter per visit" correct for OPD (a clinical record needs a visit-scoped container, and conflating two different doctors' documentation under one consultation record would corrupt both) applies identically to referral — a receiving doctor in a different department needs their own consultation record, own vitals if re-triaged, own orders. But it must not be a disconnected record the way `ipd-service`'s original sketch would have been: the new encounter carries a `referring_encounter_id` (or a lightweight `referrals` table linking the two encounter ids + referring clinician + receiving department + reason + urgency + status), so Patient 360 renders both encounters as one continuous story, exactly as it already does across a patient's separate OPD visits today (it already aggregates by `patient_id` across multiple encounters — this is not new machinery, just a new edge/link).

---

## 7. Architecture Decision Records

Status legend: **DECIDED** (already accepted and implemented, confirmed by source, carried forward here for completeness against the master prompt's requested ADR list) · **DECISION REQUIRED** (a real, unmade choice this audit surfaced).

### ADR-001: Patient / Encounter Model — **DECIDED**
Maps to the existing, verified-accurate `docs/decisions/ADR-001-PATIENT-IDENTITY.md` + `ADR-002-ENCOUNTER-CENTRIC-DESIGN.md`. Patient is the permanent identity (UHID); Encounter is the clinical context; every clinical/financial table FKs to `encounter_id`. **Addendum surfaced by this audit (DECISION REQUIRED)**: whether a patient may hold more than one simultaneous OPEN encounter is currently unconstrained by the database and untested — this must be answered explicitly before IPD/Emergency (where an admission opening while an OPD follow-up encounter is still nominally open becomes a realistic case) rather than left as an accident of "nothing has hit this yet."

### ADR-002: Appointment vs. Encounter Boundary — **DECIDED** (newly made explicit)
**Decision**: the appointment is a pure scheduling object; the encounter is the clinical context. An appointment always creates or attaches to exactly one encounter, in the same transaction, with no exceptions found in the code (walk-ins go through the identical `create_appointment_service` path as booked appointments — there is no "encounter without an appointment" path). A reschedule carries the *same* encounter forward (it is the same care episode moved in time), confirmed by `test_two_appointments...`/reschedule tests. Encounter closure is a one-directional side effect of the appointment reaching a terminal status (`CANCELLED/REJECTED/COMPLETED/NO_SHOW`) — the encounter itself has no independent transition trigger. **Why**: this is exactly right for OPD, where every clinical event genuinely does originate from a scheduled or walk-in visit. **Risk carried forward**: this same mechanism has no answer yet for an IPD admission, which has no appointment to attach to — ADR-003 must resolve how an encounter gets created without going through `create_appointment_service` at all.

### ADR-003: Encounter Type Strategy — **DECISION REQUIRED**
**Options**: (a) widen the `encounter_type` CHECK additively when IPD is scoped, keep `encounters` as a single table with a type discriminator (current design's own stated intent); (b) convert `encounter_type` to a lookup/master table. **Decision**: (a) — a CHECK-constrained enum, not a master table. Evidence: only two-to-four values are foreseeable (`OPD`, `IPD`, `EMERGENCY`, possibly `DAY_CARE`) and the value drives real structural differences in downstream logic (encounter creation path, closure trigger) that a lookup table would not meaningfully decouple — this mirrors `orders.order_type`'s own already-proven pattern. **Not decided by this audit, flagged DECISION REQUIRED**: (1) whether an IPD admission creates a *new* encounter for the same patient while an OPD encounter is still open, and if so what — if anything — links them; (2) whether `encounters.status` needs a real independent state-transition model for IPD (an admission has no appointment to derive OPEN/CLOSED from) rather than continuing to piggyback on appointment status, which literally does not exist for an admission.

### ADR-004: Clinical Domain Model — **DECIDED**
Single `diagnosis` free-text field per consultation, with an optional, never-auto-populated `diagnosis_code_system`/`diagnosis_code`/`diagnosis_code_display` triple (migration `0056`) for future terminology readiness. No primary/secondary/provisional/differential structure — re-verified against the current repository (Phase 6) and found still accurate that no workflow, UI, or report anywhere assumes more than one diagnosis per consultation. **Why not build multi-diagnosis now**: no concrete evidence of a real requirement; adding it speculatively would be exactly the anti-pattern this codebase has otherwise avoided throughout its coding-readiness work.

### ADR-005: Order Architecture — **DECIDED**
One generic `orders` + `order_results` table for every order type, discriminated by `order_type`, with type-conditional status values (LAB/RADIOLOGY get the wider diagnostic lifecycle) rather than separate tables or a second parallel state machine. Proven under real feature pressure across Phase 6 and Phase 7 without forking. **Non-negotiable rule carried forward**: any future IPD nursing/diet order extends `order_type`, never a new `clinical_orders` table (the superseded `ipd-service` sketch's own `clinical_orders` table is explicitly named as the anti-pattern to avoid).

### ADR-006: Lab Architecture — **DECIDED** (workflow granularity) / **DECISION REQUIRED** (catalog)
Decided: `lab_samples` as the one genuinely new child entity (a specimen has its own collect/reject/recollect lifecycle that doesn't fit as order-level columns); result-parameter rows stay in the existing generic `order_results` table. Decision required, not urgent: a searchable lab-test catalog (name, default units, reference ranges) — deferred correctly so far; when scoped, it should be a new `catalog_item_id`-style optional FK from `orders`, never a `lab_orders` table.

### ADR-007: Radiology Architecture — **DECIDED** (workflow granularity + report shape) / **DECISION REQUIRED** (PACS/DICOM)
Decided: a radiology report is three ordinary `order_results` rows (`parameter` = 'Technique'/'Findings'/'Impression'), not a new structured table — this was a genuine, evidence-based simplification found during Phase 7 (an earlier draft assumed a JSON/column-set was needed; it wasn't). Decision required, explicitly out of scope for now: image/attachment storage and any PACS/DICOM/RIS integration — this is a substantially larger, separate design effort (storage, access control, retention) that should not be bolted onto the existing `order_results.result_value` text column.

### ADR-008: Internal Referral Architecture — **DECISION REQUIRED** (this audit's own recommendation, not yet built)
See §6.5 above for full reasoning. **Recommendation**: a new, linked encounter per referral (same `patient_id`, `referring_encounter_id` link, `order_type = 'INTERNAL_REFERRAL'` or a small dedicated `referrals` table carrying referring clinician/receiving department/reason/urgency/status), not the same encounter and not a disconnected record. **Rejected alternatives**: same encounter (would conflate two doctors' clinical documentation under one record — the same reason `appointments` doesn't directly carry clinical data); fully independent, unlinked record (breaks Patient 360 continuity, the project's own most important principle).

### ADR-009: Billing Architecture — **DECISION REQUIRED** (the single most consequential decision in this audit)
**Problem**: two ledgers, no reconciliation, confirmed in detail in §5, Gap #1. **Target**: fold the appointment-level consultation fee into Ledger B as a `charges` row (`source_type='CONSULTATION'`), created at the same point Ledger A currently sets `payment_status`; treat `invoices` as the single canonical financial document per encounter going forward. **Migration approach**: expand/contract, not a rewrite — (1) add the consultation-fee-as-charge code path additively, running alongside the existing Ledger A path; (2) backfill: for every existing appointment with a Ledger A payment, insert an equivalent Ledger B charge/payment pair so historical totals reconcile once queried through the new path; (3) switch all read paths (billing history, Patient 360, receipts) to read only Ledger B; (4) only then stop writing to Ledger A's dedicated columns; (5) never delete Ledger A's historical columns — they remain as an immutable record of what actually happened under the old mechanism. **Why this is P0, not P1**: `docs/implementation/PHASES.md`'s own words call this "the most concurrency-sensitive path in the app," and every phase that ships more billing-adjacent surface area on top of the current split (packages already did; insurance/TPA and IPD billing would next) makes the eventual unification strictly more expensive. This deserves its own dedicated phase, not a rider on another phase's work.

### ADR-010: Payment Architecture — **DECIDED** (Ledger B's own internal model) / **DECISION REQUIRED** (post-unification)
Decided, and correct as far as it goes: one invoice, many payments; one payment settles exactly one invoice (no split-payment-across-invoices concept, and no evidence any workflow needs one); refund is scoped to a specific payment, accumulating, capped at that payment's own amount (`refunded_amount <= amount`), not a separate refund table — simpler and sufficiently auditable given payments are already an append-only-in-practice ledger. A `DECLINED` payment status (migration `0050`) already gives failed-attempt tracking with retry support, closing what an earlier audit wrongly assumed was a missing "pending/failed" concept (Ledger A has had `FAILED` since migration `0018`). Decision required: once ADR-009 lands, whether Ledger A's refund columns get a compatibility read-path or are simply frozen as historical-only.

### ADR-011: Master Data Strategy — mostly **DECIDED**, gaps **DECISION REQUIRED** only when their owning phase starts
| Entity | Classification (confirmed) |
|---|---|
| Department, Doctor, Appointment Type, Medication | Real DB tables with their own CRUD — correct, keep |
| Payment Method, Charge/Billing `source_type` | CHECK-constrained enums — correct at current cardinality, don't promote to tables without a real multi-value-per-hospital need |
| Diagnosis, Lab/Radiology test | Free text, deliberately not catalogued yet — correct, revisit only alongside a real pricing/terminology requirement |
| Ward, Room, Bed | Not modeled at all — correct for OPD-only; **DECISION REQUIRED at IPD scoping time**, not before |
| Insurance Provider, TPA | Not modeled beyond a bill_type label — correct for OPD-only; **DECISION REQUIRED at insurance-phase scoping time**, not before |

### ADR-012: Workflow / State Strategy — **DECIDED**
No generic workflow engine. Per-entity status columns plus row-locked service-layer transition functions are proportionate to this system's actual complexity and have held up under real concurrency testing. **Two small hardening items, not a redesign**: add the missing `CHECK` constraint on `appointments.status` (the one status column in the schema without a DB-level backstop, cheap and purely additive), and resolve the ADR-001 addendum (simultaneous-open-encounters policy).

### ADR-013: Audit / Versioning Strategy — **DECIDED** (pattern) / gap flagged, **P1**, not a new framework
The archive-then-update pattern (`consultation_amendments`, `lab_samples`' never-delete rows) is a genuinely stronger audit trail than a generic log row for the specific actions it covers, and should be reused, not replaced. **Gap**: extend `record_audit_log()` coverage to billing voids/refunds/terms-updates and consultation amendments — this is additive (the transactional pattern already exists and is correctly used elsewhere; it's simply not called from `billing.py` or `clinical.py`'s amend path yet), not a second audit framework.

### ADR-014: Pharmacy Architecture — **DECIDED** (OPD dispense model) / **DECISION REQUIRED** (MAR for IPD)
Decided: stock/batch/expiry tracked as append-only `pharmacy_dispense_records`, partial dispensing derived from `quantity_dispensed` vs. `quantity` rather than a status enum — correct and tested. Decision required: IPD needs a **medication-administration** concept (nurse gives a specific dose to an inpatient at a specific time) that is structurally distinct from dispensing (pharmacy releases stock) — recommend a new `medication_administrations` table FK'd to `encounter_id` and (optionally) `prescription_item_id`, added only when IPD pharmacy is actually scoped, not before.

### ADR-015: Patient 360 Architecture — **DECIDED**
A read-only, Python-side aggregation over the existing encounter spine (confirmed: ~9 separate queries joined in application code, not a SQL view) is sufficient at current scale and correctly avoids creating a second, parallel history model. **Do not** introduce a materialized view or summary/cache table unless a real, measured query-performance problem appears — none was found.

### ADR-016: Cross-Module Boundaries — **DECIDED** (pattern), one **P3** flag
Routes are thin, services own logic — confirmed by sampling across ten API files with no counter-example found. The one god-component risk is on the frontend, not the backend: `ConsultationWorkspace.tsx` at 1590 lines is the largest concentration of UI logic in the app and independently re-derives at least one state-transition rule. Not urgent; worth a scoped refactor once a second real change touches it, per the same "don't extract speculatively" discipline already applied to `PatientHeader`/`StatusBadge`.

### ADR-017: API Architecture — **DECIDED**, one gap flagged for future integrations
REST resource-noun routes, consistent `{success, errorCode, message, details}` error envelope, permission-per-endpoint. No idempotency-key mechanism exists; today this is fully compensated for by DB unique/exclusion constraints because every current client is a trusted, synchronous, browser-driven UI. **Decision required only if/when** an async or retry-prone integration (a payment-gateway webhook, an insurance-TPA API) is added — that is the trigger to add real idempotency-key support, not before.

### ADR-018: Database Evolution Strategy — **DECIDED**
Strictly additive migrations only; `scripts/migrate.py` takes an advisory lock for multi-instance-safe deploys. Confirmed: zero destructive migrations across 65 files. Keep this discipline unchanged.

### ADR-019: RBAC / Module Licensing Architecture — **DECIDED**, confirmed genuinely implemented
Contradiction between `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` (claimed aspirational) and `docs/product/PRODUCT_VISION.md` (claimed real) resolved in favor of the latter by direct source inspection and live-HTTP-level tests. One low-priority decision surfaced: whether to invest in wiring the frontend sidebar to the same `is_module_available()` signal the backend already enforces, purely for UX consistency (Gap #6) — not a security decision, since the backend boundary already holds independent of the frontend.

### ADR-020: Printing Architecture — **DECIDED**
Browser-native print/Save-as-PDF over a shared `.print-area`/`@media print` mechanism, with backend-generated (`GENERATED ALWAYS AS`) document identifiers. Explicitly revisit only if a concrete requirement for programmatic bulk generation or PDF-without-browser emerges — not before.

---

## 8. Dependency Graph

```
                    ┌─────────────────────────────┐
                    │  ADR-001 addendum: simultaneous-  │
                    │  open-encounter policy            │
                    │  ADR-003: encounter_type widening │
                    └───────────────┬───────────────────┘
                                    │
                ┌───────────────────┼────────────────────┐
                ▼                   ▼                     ▼
        ADR-008 Internal      IPD groundwork          Emergency
        Referral               (ADR-011 Ward/Bed,      (not scoped;
        (independent of        ADR-014 MAR)            follows IPD's
        IPD/Emergency,                                  own pattern)
        can proceed once
        ADR-001/003 land)
                                    │
                                    ▼
                        ADR-009 Billing Unification
                        (blocks clean IPD billing AND
                         real Insurance/TPA — do this
                         BEFORE either, not after)
                                    │
                        ┌───────────┴───────────┐
                        ▼                       ▼
                ADR-011 Insurance/TPA      IPD Billing
                master data

    (Orthogonal, no dependency on the above — do anytime)
    ─────────────────────────────────────────────────────
    Gap #5  Audit-log coverage extension
    Gap #10 appointments.status CHECK constraint
    Gap #6  Frontend module-availability sidebar wiring
    ADR-006/007 Lab/Radiology catalog (independent of IPD)
    Gap #17 Configuration-matrix test suite
```

**Reading this graph**: ADR-009 (billing unification) sits upstream of both IPD and Insurance/TPA, which is why it is P0 rather than "whenever convenient" — it is the one decision that, left unmade, makes two separate future phases (not just one) more expensive. ADR-001/003 (encounter policy) sits upstream of Internal Referral, IPD, and Emergency alike, since all three create a second kind of encounter that must relate to existing OPD encounters in a defined way. Everything in the orthogonal list can be picked up in any order, independent of the sequencing above, and should be — they are cheap and there is no reason to wait.

---

## 9. Migration Strategy

For each P0/P1 decision:

**ADR-009 (Billing Unification)** — Additive: yes, via expand/contract (see ADR-009 body for the five-step sequence). Existing data needs migration: yes, a one-time backfill inserting Ledger-B-equivalent rows for historical Ledger-A payments. Existing API needs compatibility: yes — `GET /appointments/{id}/invoice` (old) and `GET /appointments/{id}/bill` (new) both already coexist deliberately (migration `0033`'s own naming choice to avoid a route collision); the old route can be kept returning correct data throughout the transition by having it read from the same unified source once step 3 lands. Frontend needs changes: yes, but only in `AppointmentBillingPanel.tsx` and the two history panels, once the read-path switch (step 3) happens — no schema-facing frontend change before that. Old and new models can coexist temporarily: yes, that's the entire point of doing this as five sequential, independently-shippable steps rather than one migration. Rollback: each step is independently revertible until step 4 (stop writing to Ledger A) — after step 4, rollback means resuming the old write path, not undoing data.

**ADR-001/003 (encounter policy + type widening)** — Additive: yes, widening the CHECK is a one-line migration. Existing data needs migration: no. Existing API/frontend needs compatibility: no impact until IPD/Referral actually start creating non-OPD encounters. Can coexist: trivially, since OPD's own behavior is completely unchanged by widening a CHECK that OPD never violates. The real work here is not the migration — it's writing down the simultaneous-open-encounter policy and, if the answer is "yes, allowed," deciding what (if anything) needs to change in Patient 360's aggregation logic to render two concurrently-open encounters sensibly (today's read-only aggregation already groups by `encounter_id`, so this is likely a non-event, but should be explicitly tested, not assumed).

**ADR-014 (MAR)** — Additive: yes, a new table, no change to existing `pharmacy_dispense_records`. Existing data: unaffected. Existing API/frontend: unaffected (OPD has no inpatients to administer medication to). Should not be built before IPD is actually scoped — there is no OPD use case for it, and building it speculatively risks guessing the wrong shape.

**Audit-log coverage extension (Gap #5)** — Additive, no schema change, no migration at all — purely adding `record_audit_log()` calls inside `billing.py`'s and `clinical.py`'s existing transactions, reusing the exact pattern already proven correct elsewhere in the codebase (same cursor, same transaction, confirmed to already be the house style).

---

## 10. Phase Impact

| Existing/planned phase | Recommended disposition | Why |
|---|---|---|
| Phase 7 Diagnostics | **Continue as-is** — already completed for its scoped items | No architectural blocker found; catalog work (ADR-006/007) is a clean, independent follow-on whenever wanted |
| Phase 9 Billing (ledger unification) | **This is the next phase, and it should be scoped explicitly around ADR-009**, not folded into Phase 11 or any other work | `docs/implementation/PHASES.md` already independently flagged this as "the next phase this diagnostics work itself surfaced as still-deferred" — this audit confirms that recommendation and elevates it to the top architectural priority |
| Phase 11 Command Center | **Continue, low risk** — purely additive KPI/dashboard work over data that already exists, no dependency on any P0/P1 decision above | Confirmed by `docs/implementation/PHASES.md`'s own accurate framing |
| Internal Referral | **Pause until ADR-001/003 (encounter policy) is decided** | Internal referral's own design (ADR-008) explicitly depends on how a second, linked encounter is expected to behave — deciding referral's encounter-linkage shape before the general encounter-multiplicity policy is settled risks a wrong or inconsistent answer |
| IPD groundwork | **Do not start** — correctly not yet scoped per `docs/implementation/PHASES.md` | Depends on ADR-001/003, ADR-009 (billing), ADR-011 (Ward/Bed), and ADR-014 (MAR) all being decided first; starting IPD schema work before these compounds exactly the risk this audit exists to head off |
| Emergency | **Do not start** — no schema groundwork exists at all, unlike IPD's reserved `encounter_type` slot | Follows the same reasoning as IPD, one step further out |
| UI/UX redesign | **Pause substantive redesign; low-risk cosmetic work (OPD nav relabeling, `PatientHeader`/`StatusBadge` extraction) may continue independently** | The UI audit's own findings (component duplication, billing panel fragmentation) are real but are consequences of backend decisions (especially ADR-009's two ledgers feeding four separate billing panels) — redesigning the UI before the ledger unification risks designing a polished frontend for a data model that's about to change underneath it |
| Printing | **Continue incrementally, no change to architecture (ADR-020 stands)** | No dependency on any P0/P1 finding |

---

## 11. Recommended Architecture-First Implementation Sequence

1. **Decide and record ADR-009 (Billing Unification) and ADR-001/003 addendum (encounter policy)** as accepted ADRs in `docs/decisions/`, with the five-step expand/contract plan from §9 written out as an implementation phase brief — no code yet, but the decision itself unblocks two downstream phases (Referral, IPD) at once.
2. **Ship the two cheap, independent hardening items in parallel with step 1's decision-making**, since neither has any dependency: extend `record_audit_log()` coverage to billing voids/refunds and consultation amendments (Gap #5); add the missing `appointments.status` CHECK constraint (Gap #10). Both are additive, low-risk, and improve the audit trail before any billing-touching phase begins.
3. **Execute ADR-009's billing unification** as its own dedicated phase, following the five-step migration plan in §9. This is the P0 and should land before Internal Referral or any Insurance/TPA work.
4. **Scope and execute Internal Referral (ADR-008)**, now unblocked by step 1's encounter-policy decision.
5. **Fix the frontend/backend module-gating mismatch (Gap #6)** and add the configuration-matrix test suite (Gap #17) — both are cheap, and the test suite becomes genuinely valuable the moment step 6 (IPD) adds a second module-gated capability to test against.
6. **Only then scope IPD**, now that ADR-009 (billing), ADR-001/003 (encounter policy), ADR-011 (Ward/Bed), and ADR-014 (MAR) all have answers to build against.
7. **Emergency, real UI/UX redesign, and Insurance/TPA** follow IPD, in whichever order the business actually needs them — none of the three has an architectural dependency on the others beyond what's already captured above.

---

## 12. Definition of Architecture Complete

The architecture phase for this specific audit is complete, and implementation may resume, when:

1. ADR-009 (Billing Unification) has an accepted decision recorded in `docs/decisions/` with the five-step migration plan reviewed and approved — not merely "acknowledged," since this is the one decision this audit found to be load-bearing for two future phases.
2. The ADR-001/003 addendum (simultaneous-open-encounter policy, and the exact `encounter_type` values to widen to) is recorded as an accepted decision, even though its *implementation* (the actual CHECK-widening migration) can wait until IPD or Referral is scoped — the decision itself, not the migration, is what unblocks downstream planning.
3. ADR-008 (Internal Referral's encounter-linkage model) is recorded as an accepted decision if Internal Referral is the next phase picked up; it can remain "decision required" indefinitely if Internal Referral is not yet prioritized.
4. This document's P1/P2 findings (Gaps #2, #3, #5, #6, #10, #17) have each been explicitly triaged by the person deciding phase order — "explicitly deferred, tracked" is an acceptable outcome for any of them; "silently unaddressed" is not, per this codebase's own established discipline of naming deferrals rather than hiding them.

No further architecture-only passes are required beyond the above — this audit did not find evidence of a deeper structural problem (no rewrite, no microservices split, no abstraction inversion) that would justify additional exploration before returning to feature implementation.

---

## Appendix: Corrections to existing documentation surfaced during this audit

These should be applied as small, factual edits to the named docs the next time they're touched — not because this audit is authoritative over them, but because source code is, per this task's own stated hierarchy, and these are concrete, verified discrepancies:

- `docs/ux/NAVIGATION.md` line 9 claims sidebar items are "further gated by permission/module-availability checks — e.g. Pharmacy only shows when the PHARMACY module is available." **Not currently true** for ADMIN/STAFF sessions — confirmed no such gating exists in `AdminApp.tsx`'s `menuItems` for Pharmacy/Packages/Lab Worklist (Gap #6 above).
- `docs/DATABASE_P1_NOTES.md` describes the cross-path double-booking race as an open, undecided item ("Needs an explicit decision... not applied here"). **This is stale** — the fix was applied (unified advisory lock across both paths, migration `0003`'s exclusion constraint as backstop) and `tests/test_concurrency.py`'s own docstring documents the fix and the tests no longer being `xfail`. This doc should be marked historical/superseded, the way `ipd-service/schema/0001_baseline_ipd_schema.sql` already is.
- `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md`'s RBAC finding ("Role-based work is aspirational, not real") is superseded by `docs/product/PRODUCT_VISION.md`'s later correction and independently re-confirmed by this audit against current source and live-HTTP tests — no action needed since `PRODUCT_VISION.md` already documents the correction, but worth noting `MASTER_SPEC_AUDIT.md` itself was not retroactively annotated at that specific line.
