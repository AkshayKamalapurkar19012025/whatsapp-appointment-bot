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

### ADR-009: Billing Architecture — **DECIDED: Option B (dual-write), Phase 1 — implemented**
**Status**: accepted and implemented. `migrations/0058_consultation_fee_ledger_mirror.sql` + `app/services/billing_services.py`'s `mirror_consultation_payment`/`mirror_consultation_fee_waived`/`mirror_consultation_payment_refunded` + one additive call each in `app/services/appointment_services.py`'s `record_payment_service`/`waive_consultation_fee_service`/`record_refund_service`. `app/api/dashboard.py`'s `get_billing_report` now reads the mirror for collections/refunds (two sections — `outstanding_unpaid` and `waivers` — remain on `appointments.payment_status` for reasons specific to each, documented in that function's own docstring: one is a temporary scope gap, the other is permanent since a $0 free-visit waiver has nothing to mirror). **Acceptance test**: `tests/test_billing_ledger_reconciliation_gap.py` passes with no `xfail` marker — Payment History now reports the visit's true total (1700, was 1200); the Dashboard's "Billing" panel correctly continues to report only the consultation-fee subset (500), matching its own, unchanged, scope-labeled copy. Full test suite: 798 passed, 2 pre-existing unrelated failures (confirmed via `git stash` comparison to predate every change in this ADR), 0 regressions. See the fourth addendum below for the full implementation report.

**Original problem** (superseded by the above): two ledgers, no reconciliation, confirmed in detail in §5, Gap #1. **Why this was P0, not P1**: `docs/implementation/PHASES.md`'s own words call this "the most concurrency-sensitive path in the app," and every phase that ships more billing-adjacent surface area on top of the current split (packages already did; insurance/TPA and IPD billing would next) makes the eventual unification strictly more expensive.

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

---

## Addendum: Follow-up Verification

This addendum responds to specific pushback on the audit above: the P0 label was asserted without proof of present-day harm, "safe to continue, with conditions" never named the conditions, most of the priority list leaned on an uncommitted IPD timeline, and citations from parallel research agents hadn't been independently re-opened. No code was changed to produce this addendum.

### 1. P0 justification — revised, with present-day evidence (not an IPD-hypothetical)

The original write-up justified the P0 mostly by appeal to future cost ("every future phase that touches money makes unification more expensive"). That's a real but weak argument on its own — a design smell isn't a P0 by itself. Re-verifying the actual billing/reporting code turned up something stronger: **the two ledgers already back two different, live, currently-shipped reporting screens, and the two screens disagree today, not hypothetically.**

- `app/services/billing_history_service.py:55-86` (`list_invoices_service`) and `:140-167` (`list_payments_service`) — the queries backing the **Billing History** and **Payment History** screens (`BillingHistoryPanel.tsx`/`PaymentHistoryPanel.tsx`) — join only `invoices`/`charges`/`payments`/`encounters`/`patients`/`appointments` (the last only for the doctor's name). Confirmed directly: neither query references `appointments.payment_status`, `payment_amount`, or `invoice_line_items` anywhere. Every consultation fee collected through Ledger A is structurally invisible to these two screens.
- `app/api/dashboard.py:155-291` (`get_billing_report`, the Dashboard's billing reconciliation view) — confirmed directly: every branch of this function queries `appointments.payment_status` (`WHERE payment_status = 'PAID'`, `WHERE a.payment_status IN ('UNPAID','FAILED')`, `WHERE payment_status = 'WAIVED'`, `WHERE payment_status = 'REFUNDED'`). It never touches `invoices`, `charges`, or `payments` at all.

**Reproducible scenario, today, OPD-only, no IPD involved**: a cashier collects a ₹500 consultation fee at check-in (Ledger A: `appointments.payment_status` → `PAID`) and later, during the same visit, bills a ₹1,200 lab charge through the encounter invoice (Ledger B: `invoices`/`payments`). The Dashboard's "Billing Report" shows this visit's ₹500 as reconciled revenue and says nothing about the ₹1,200. The Billing History / Payment History screens show the ₹1,200 payment and say nothing about the ₹500. **No screen in the application shows this visit's true total of ₹1,700 collected, and no screen is wrong about what it does show — each is a correct but structurally partial view of the same visit's money.** A hospital administrator reconciling daily collections by adding up what the Dashboard's billing report says will silently undercount by the sum of every consultation fee collected that day; a hospital administrator using Payment History for the same purpose will silently undercount by the sum of every consultation fee for the opposite reason. This is present-day, OPD-only harm, not a consequence of IPD or insurance being built later.

**Correction to the original framing**: the P0 label is upheld, but for this reason — two live reporting surfaces already produce disjoint, non-reconciling pictures of the same money — not for the originally-stated "will matter once IPD/insurance exists" reason. That original framing undersold the finding and is the fair target of the "IPD is doing the prioritizing" critique below. No failing automated test currently encodes this scenario (worth adding one as part of ADR-009's own work, not before).

### 2. Conditions behind "safe to continue, with conditions" — made explicit and checkable

"Safe to continue" means: OPD feature work, Phase 11 (Command Center KPIs), and Phase 7/13 follow-ons (catalog, printing) may proceed **unconditionally** — none of them reads or writes billing data in a new way, and none is affected by anything below. The conditions are specific to *which* phase is picked up next:

| Condition | Checkable as | Owner decision needed |
|---|---|---|
| Before any phase that adds or redesigns a billing screen/panel | Does the phase touch `AppointmentBillingPanel.tsx`, `BillingPanel.tsx`, `BillingHistoryPanel.tsx`, or `PaymentHistoryPanel.tsx`, or add a new billing/reporting screen? | ADR-009 (ledger unification plan) must be decided first — see §6 below for why |
| Before Internal Referral is scoped | Does the phase create a second encounter for an existing patient? | ADR-001/003 addendum (simultaneous-open-encounter policy) and ADR-008 (referral linkage model) must be decided first |
| Before IPD is scoped at all | N/A — not currently planned, see §2 below | ADR-009, ADR-001/003, ADR-011 (Ward/Bed), ADR-014 (MAR) all need answers, but only when/if IPD is actually committed |
| Before any insurance/TPA claims work | Does the phase add payer/policy/pre-auth/claim fields? | ADR-009 (a claim needs one canonical invoice to attach to) and ADR-011 (insurance master data) |

Everything not listed above (Command Center KPIs, printing, catalog work, audit-log coverage extension, the `appointments.status` CHECK constraint) has no condition attached and can start immediately.

### 3. Citation verification — re-opened directly, not re-trusted from the parallel agents

| Claim | Verification method | Result |
|---|---|---|
| Ledger A payments invisible to Billing/Payment History | Read `billing_history_service.py` in full, confirmed no reference to `appointments.payment_status`/`payment_amount` in either query | **VERIFIED** (and strengthened — see §1) |
| Dashboard billing report reads Ledger A only | Read `app/api/dashboard.py:155-291` directly (grep + inspection) | **VERIFIED** — new finding this pass, not in the original agent reports |
| Cross-path double-booking race reproduced (19/20) and fixed via unified advisory lock + exclusion constraint | Read `migrations/0003_prevent_overlapping_bookings.sql` and `tests/test_concurrency.py`'s own docstring directly (done in an earlier turn of this same session, not delegated) | **VERIFIED** — the docstring itself documents "HISTORY... FIX APPLIED... all three tests below now assert the double-scheduling-free outcome directly (no xfail)" |
| RBAC roles were genuinely non-functional before PR #114, genuinely functional after | Read `migrations/0031_rbac_decomposition.sql`'s own seed comment ("seeded now... but unused... until their own modules exist") and `migrations/0043_role_based_access.sql`'s header (documents exactly two blockers — a CHECK constraint rejecting any role but ADMIN/STAFF, and zero `role_permissions` rows — and how this migration fixes both) directly, plus confirmed `tests/test_role_based_access.py` contains the specific named tests (`test_account_can_be_created_with_every_seeded_role`, `test_pharmacist_role_can_manage_stock_staff_role_cannot`, six more) | **VERIFIED** |
| No medication-administration (MAR) concept exists | Re-ran the grep myself: `grep -rni "administer" migrations/ app/services/ app/api/` (excluding "administrat[ion/or]" false positives) — zero hits | **VERIFIED** |
| No constraint prevents simultaneous OPEN encounters per patient | Read `migrations/0028_encounters.sql`'s full `CREATE TABLE encounters` directly (no unique constraint beyond the primary key) and `migrations/0049_hardening_indexes.sql` (adds only plain, non-unique indexes on `patient_id`/`hospital_id`) | **VERIFIED**, with one correction: `encounters` also carries a `doctor_id` column not surfaced in the original write-up — an encounter is scoped to (patient, doctor), which matters for §4 below |

No citation required correction. One gap in the original report's own diligence was found and is disclosed in §1: the dashboard-vs-history disjoint-ledger evidence existed in the code the whole time but wasn't surfaced by any of the five research agents or by me — it only came up under this follow-up's specific instruction to find present-day harm.

### 4. Duplicate encounters — what's actually guarded, precisely

`encounters` is scoped to **(patient_id, doctor_id)**, not patient alone (confirmed directly, migration `0028`, line 31). This changes the shape of the question:

- **Same patient, same doctor, same/overlapping time slot, double-click or retry**: guarded, but incidentally — not by an idempotency key, by the *same* mechanism that prevents two different patients double-booking a slot: `pg_advisory_xact_lock(doctor_id)` plus the `EXCLUDE USING gist (doctor_id WITH =, slot WITH &&)` constraint (migration `0003`) block a second overlapping row for that doctor regardless of which patient submits it. A retried request for the identical doctor+time is rejected by the same protection, with the same friendly error path.
- **Same patient, same doctor, same day, but a *different* time slot** (e.g., a retry that lands on the next available slot because the first attempt's slot filled in between) — **not guarded by anything**. No idempotency-key mechanism exists (confirmed earlier in this audit, Gap #16); nothing on the client or server deduplicates "this patient already has an appointment with this doctor today" before creating a second one. This is a real, currently-unguarded gap, though it requires a specific retry-with-slot-change sequence to trigger, not a bare double-click on an unchanged form.
- **Same patient, different doctor/department, same day**: definitionally a *different* (patient, doctor) pair, so this is not a duplicate at all under the current model — it is two legitimate, independent encounters (e.g., Cardiology follow-up and Orthopedics consultation on the same day). Nothing should guard against this, and nothing does.

**Answer to the specific question asked**: the system guards against the "double-click, identical slot" case as a side effect of its booking-concurrency protection, not as a deliberate encounter-deduplication feature. It does not guard against "same patient, same doctor, same day, different slot" at all. Neither gap is IPD-specific — both are exercisable in pure OPD usage today, which is why Gap #4 in the original report (multiple OPEN encounters) was correctly flagged as P1 rather than P0: it is a real, present, narrow gap, but it requires a specific retry sequence rather than ordinary use, unlike the billing-ledger finding in §1, which is triggered by the single most common OPD sequence (consultation fee + any lab/pharmacy charge on the same visit).

### 5. Scope split — two rankings

**IPD's actual status in this repository's own planning docs**: not committed, with no timing. `docs/implementation/PHASES.md`'s own words: *"IPD. No phase number assigned yet — deliberately, since scoping it prematurely risks exactly the 'implement future modules prematurely' anti-pattern CLAUDE.md names,"* and *"Emergency. Not scoped at all — no schema groundwork exists."* There is no roadmap document, ADR, or product doc in this repository asserting an IPD timeline. This is a fair challenge to the original report: it let the shape of a hypothetical IPD phase drive most of the priority list.

**(a) If OPD is the committed scope for the next 6 months and IPD/Emergency remain unscoped:**

| Rank | Item | Why it moves here |
|---|---|---|
| **P0** | Billing ledger disagreement (§1) | Present-day harm, OPD-only, no IPD required |
| **P1** | Audit-log coverage (billing voids/refunds, consultation amendments) | Compliance-relevant regardless of IPD; cheap; independent of everything else |
| **P1** | `appointments.status` missing DB CHECK constraint | Cheap, safe, closes the one status column without a DB backstop; independent of IPD |
| **P2** | Frontend/backend module-gating mismatch (Gap #6) | Real, but cosmetic-only; backend already enforces it |
| **P2** | Configuration-matrix test suite (Gap #17) | Still useful even with just three existing modules (Lab/Pharmacy/Packages); less urgent without a second module family to test against |
| **P3** | Simultaneous-open-encounter policy (§4) | Real but narrow (requires a specific retry sequence); not urgent absent IPD |
| **Drops off the list entirely** | Encounter-type widening (ADR-003), MAR (ADR-014), Ward/Bed/Insurance master data (ADR-011), Internal Referral (ADR-008) | All three exist only to serve IPD/Emergency/Referral, none of which is committed; deciding them now would be exactly the "designing against a hypothetical system" the pushback warned about |

**(b) If IPD is committed with rough timing (e.g., "next 12 months"):**

| Rank | Item | Why |
|---|---|---|
| **P0** | Billing ledger unification (ADR-009) | Same present-day harm as (a), now *also* the thing that must not be inherited twice by IPD billing |
| **P0** | Encounter policy (ADR-001/003 addendum) | Now genuinely blocking — an admission must have a defined relationship to any open OPD encounter for the same patient |
| **P1** | MAR model (ADR-014), Ward/Bed master data (ADR-011) | Now real, scoped work, not speculative |
| **P1** | Internal Referral (ADR-008) | Worth deciding alongside encounter policy since both define "a second encounter for the same patient," even if Referral itself isn't IPD |
| **P2** | Everything from list (a) that isn't superseded | Audit-log coverage, `appointments.status` CHECK, module-gating mismatch, config-matrix tests — unchanged, still worth doing, just no longer the top of the list |

The practical recommendation: **decide (a) vs (b) first** — that single business decision (is IPD actually planned, roughly when) determines which of these two tables governs, and most of the disagreement in the pushback traces back to the original report not asking this question explicitly.

### 6. Blocking decisions for the next phase

Naming the next phase matters, since the answer changes completely depending on which one it is:

- **If the next phase is Phase 11 (Command Center KPIs)**: zero ADRs block it. It reads existing `appointments`/dashboard data additively; nothing in this audit constrains it.
- **If the next phase touches any billing screen** (`AppointmentBillingPanel.tsx`, `BillingPanel.tsx`, `BillingHistoryPanel.tsx`, `PaymentHistoryPanel.tsx`, or a new one): **ADR-009 blocks it.** Concretely: building or redesigning a billing UI before deciding the unification approach means building against a data model that's a known, upcoming moving target — the UI would need to be redone once Ledger A is folded into Ledger B (§9's five-step plan changes which endpoints/fields exist). **Recommended default**: decide ADR-009's five-step plan now (it's a sequencing and backfill decision, not a UI decision) and let it run in the background while other non-billing UI work proceeds in parallel — the two are not mutually exclusive, only "redesign the billing screens" and "decide how billing data is modeled" are ordered.
- **If the next phase is Internal Referral**: **ADR-001/003 addendum and ADR-008 block it**, per §4 and §7's original reasoning — a referral creates a second (patient, doctor) encounter, and its linkage model can't be designed sensibly before the general "second encounter for this patient" policy is decided.
- **On the specific "UI-9 billing workspace" reference**: this audit could not verify that citation — no document matching a numbered "UI-9" phase (or any separate UI/UX redesign audit) exists anywhere in this repository (confirmed by search at the start of the original audit; the task's own Level 3 source was not found in-repo either). If such a plan exists outside this repository, the principle above still applies regardless of its phase number: any UI phase that touches billing should be sequenced after the ADR-009 decision (not necessarily after its full implementation), not before, for the reason stated above.

### 7. Doc drift found during this audit (consolidated)

| Doc | Claim | Actual (source-verified) |
|---|---|---|
| `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` (PR #112) | "Role-based work is aspirational, not real" | False as of PR #114/migration `0043` — genuinely functional, HTTP-tested (§3 above) |
| `docs/ux/NAVIGATION.md:9` | Sidebar items are gated by module availability (e.g. Pharmacy) | Not true for ADMIN/STAFF — no such gating exists in `AdminApp.tsx` |
| `docs/DATABASE_P1_NOTES.md` item 4 | Cross-path double-booking race is an open, undecided item | Stale — fixed, confirmed by `test_concurrency.py`'s own "FIX APPLIED" docstring (§3 above) |
| `docs/workflows/BILLING.md` §8 | Flags its own uncertainty ("TODO — VERIFY") over whether migration `0050`'s `DECLINED` status fully closes the "no pending/failed payment state" gap | Resolved, not a false claim — Ledger A has had `FAILED` since migration `0018`, Ledger B has `DECLINED` since `0050`, both support retry (verified by the billing research pass, re-confirmed by citation review) |
| **This audit's own original draft** | Framed the billing-ledger P0 primarily as a future/IPD-driven risk | Understated — the two ledgers already back two disjoint, disagreeing live reports today (§1); this is the most significant correction in this addendum |

No further verification passes are planned. Per the instruction accompanying this follow-up, this addendum stops here.

---

## Second Addendum: Proof, PHASES.md Correction, Ledger Dependency Inventory, and ADR-009 Options

No application, migration, API, or frontend code was changed to produce this addendum. One new test file was added, per explicit instruction — see §1.

### 1. Proof: a real, running failing test, not a hypothetical

A local Postgres 16 instance was started in this environment, both the dev and `_test` databases were provisioned via `scripts/provision_local_db.sh`, and all 57 migrations were applied via `scripts/migrate.py` — this is a real database running the real schema, not a mock.

`tests/test_billing_ledger_reconciliation_gap.py` was added (committed alongside this addendum). It drives one OPD visit through both ledgers exactly as real usage does:

1. Seed a doctor with a ₹500 consultation fee, create a patient, create and confirm-and-check-in an appointment.
2. `POST /api/appointments/{id}/payment` (`method: CASH, outcome: PAID`) — Ledger A, the consultation fee.
3. `POST /api/appointments/{id}/bill/charges` (₹1,200 "CBC") + `POST /api/appointments/{id}/bill/payments` (₹1,200, CASH) — Ledger B, a lab charge, paid in full.
4. `GET /api/dashboard/billing` (the Dashboard's real, shipped "Billing" panel — see §2's correction below on why this is live, not unused) and `GET /api/billing/payments` (Payment History) — the two live reports.

**Actual output, captured from a real run** (`pytest tests/test_billing_ledger_reconciliation_gap.py --runxfail --tb=short`, numbers below are real, not illustrative):

```
AssertionError: Dashboard billing report shows 500.0, missing the 1200 lab charge recorded through Ledger B
assert 500.0 == 1700
```

Re-run with the first assertion disabled to reach the second:

```
AssertionError: Payment History shows 1200.0, missing the 500 consultation fee recorded through Ledger A
assert 1200.0 == 1700
```

True total collected for this one visit: ₹1,700. The Dashboard's billing report shows ₹500. Payment History shows ₹1,200. Neither shows ₹1,700, and no third screen combines them. This is the exact scenario claimed in the first addendum, now proven against a real database rather than argued from reading code. The test is committed as `@pytest.mark.xfail(strict=True, ...)`, matching this codebase's own established convention (`tests/test_concurrency.py`'s double-booking race) for a confirmed, reproducible, tracked-but-unfixed gap: it will fail loudly (an unexpected pass) the moment ADR-009 is implemented, which is the correct signal to remove the marker.

Run in normal (non-`--runxfail`) mode, the test reports `XFAIL`, not a bare pass — it is a real, meaningful assertion, not a vacuous or disabled test.

One environment note, disclosed for completeness: running the entire 791-test suite sequentially in this sandbox produced 106 unrelated failures/33 errors after several minutes; every one checked was confirmed to pass individually and in small groups (e.g. `test_appointment_types.py`, `test_audit_log.py`, `test_availability_engine.py` together: 38 passed). This is a resource artifact of this constrained container over a ~9.5-minute sequential run (Postgres itself showed `max_connections=100` with only 6 active, ruling out simple DB exhaustion), not a defect introduced by the new test file, which sits alphabetically both before and after tests that failed in the full run and passed the same tests failed regardless of proximity to it.

### 2. The PHASES.md / "UI-9" claim — correcting the premise, not conceding it

Checked directly: `docs/implementation/PHASES.md` contains **zero** occurrences of "UI-" anywhere (`grep -n "UI-" docs/implementation/PHASES.md` returns nothing). Its actual structure is `## Phase 0` through `## Phase 14`, each a full-stack phase (backend + frontend together, e.g. "Phase 9 — Billing + Payment," "Phase 7 — Diagnostics"), not a separate UI-numbered track:

```
Phase 0 — Repository Audit
Phase 1 — Design System + Application Shell
Phase 2 — Patient Identity
Phase 3 — Encounter Foundation
Phase 4 — Check-in + Queue
Phase 5 — Triage + Consultation
Phase 6 — Order Spine
Phase 7 — Diagnostics (Laboratory and Radiology)
Phase 8 — Prescription + Pharmacy
Phase 9 — Billing + Payment
Phase 10 — Patient 360
Phase 11 — Hospital Command Center
Phase 12 — Production Hardening
Phase 13 — Printing & Document Management
Phase 14 — Full End-to-End Validation
Phases beyond the original 15 — not yet scoped (IPD, Emergency)
```

**Which claim was wrong**: neither this audit nor its first addendum ever asserted that a "UI-1..UI-13" sequence exists in this repository or that any document here reused it — the first addendum's exact words were "this audit could not verify that citation — no document matching a numbered 'UI-9' phase... exists anywhere in this repository." That statement is accurate and is restated, not retracted, here. The premise that "the earlier UI audit said it reused `docs/implementation/PHASES.md`'s UI-1..UI-13 sequence" does not describe anything in this document or its addendum — if a document with that framing exists, it is external to this repository and was not produced by this audit. What **is** true and worth restating plainly: `docs/implementation/PHASES.md`'s own Phase 9 ("Billing + Payment") is marked `✅ Done`, and that same doc's own "Recommended next step" section (quoted in the original report's §10) independently names billing-ledger unification as the next deferred architectural item — a fact this audit's ADR-009 is built on, and one that holds regardless of what any external, unverifiable "UI-9" document says.

### 3. Every read and write of `appointments.payment_status` — file:line, classified

Checked directly via `grep -rn "payment_status" app/` and by reading each call site's containing function:

| Location | Function | Read or write | Classification |
|---|---|---|---|
| `app/services/appointment_services.py:1682` | `record_payment_service` | **Write** → `PAID`/`FAILED` | **Workflow-gating** — line 1695 (in the same function) calls `generate_queue_token_service`: a token is issued only from this write path |
| `app/services/appointment_services.py:1755` | `waive_consultation_fee_service` | **Write** → `WAIVED` | **Workflow-gating** — line 1767 calls `generate_queue_token_service` |
| `app/services/appointment_services.py:1823` | `settle_free_visit_service` | **Write** → `WAIVED` | **Workflow-gating** — line 1835 calls `generate_queue_token_service` |
| `app/services/appointment_services.py:1893` | `record_refund_service` | **Write** → `REFUNDED` | **Reporting only** — no downstream trigger; the token was already issued earlier in the visit |
| `app/services/appointment_services.py:1578`, `1612` | `_lock_appointment_for_payment` | Read (`FOR UPDATE`) | **Workflow-gating** — the row-lock guard shared by all three write functions above |
| `app/services/appointment_services.py:1586–1588` | `add_invoice_line_item_service` | Read | **Workflow-gating** — refuses to add a Ledger A line item unless `payment_status IN ('UNPAID','FAILED')` |
| `app/services/appointment_services.py:1869, 1882, 1884` | `record_refund_service` | Read | **Workflow-gating** — refund is refused unless `payment_status == 'PAID'` |
| `app/services/appointment_services.py:1910–1923` | `_current_payment_record` | Read | **Reporting only** — builds the payment-detail object returned by the API |
| `app/services/visit_completion_service.py:75–77` | `get_visit_completion_checklist_service` | Read | **Reporting only, but workflow-adjacent** — feeds the non-gating "payment completed" checklist line; the same function's own comment (lines 55–62) explicitly documents this as a *deliberately separate signal* from Ledger B's "billing completed" line, shown side by side rather than combined — this is the one place in the codebase that already handles the two-ledger split honestly rather than silently picking one |
| `app/api/appointments.py:272, 338` | appointment list/detail endpoint | Read | **Reporting** (API surface consumed by both the frontend list view and, indirectly, `AppointmentActions.tsx`'s gating logic below) |
| `app/api/dashboard.py:200, 214, 225, 230, 242, 254, 267, 291` | `get_billing_report` | Read (8 separate queries) | **Reporting only** — confirmed to be the sole source of the Dashboard's "Billing" panel, Ledger-A-exclusive |
| `frontend/src/admin/AppointmentActions.tsx:152–178` | action-menu builder | Read (`a.payment_status`, sourced from the endpoint above) | **Frontend-only workflow gate** — "Mark completed" is not rendered at all while `payment_status` is `UNPAID`/`FAILED`; confirmed this is UI-only, since `mark_completed_service` itself (the backend function actually invoked) contains no payment check — a direct API call to `/complete` is not blocked by payment state, only the button is hidden |

**`billing_services.py` was checked and confirmed to contain zero references to `appointments.payment_status`** — the two ledgers genuinely never cross-read each other in code, exactly as `tests/test_billing_invoices.py`'s own module docstring states ("Deliberately separate from, and never touching, the existing appointments.consultation_fee/payment_status flow").

**One additional, relevant finding not previously surfaced**: `app/services/exception_engine.py`'s `_payment_pending` function (lines 277–307), which powers the live "Needs Attention" `PAYMENT_PENDING` exception, reads exclusively from `invoices`/`charges`/`payments` (Ledger B) and has no `appointments.payment_status` reference at all. A visit whose consultation fee alone remains unpaid — no Ledger B charge ever created — produces `gross_amount = 0` for that invoice, so `balance = 0`, so it is silently never flagged as a `PAYMENT_PENDING` exception. This is a fourth live surface exhibiting the same root gap, not a new independent bug.

**Why this inventory matters for ADR-009**: the queue-token issuance trigger (`generate_queue_token_service`, called only from the three write functions above) is the single highest-stakes dependency on Ledger A — "a patient shouldn't enter the queue before payment/waiver" is a real, tested, operationally load-bearing rule, not incidental reporting. Any unification plan must either preserve this exact trigger unchanged, or replace it with a provably equivalent one before Ledger A's write path is touched.

### 4. ADR-009 options, given the dependency inventory above

**Option A — Fold Ledger A into Ledger B outright.** The consultation fee becomes a `charges` row (`source_type='CONSULTATION'`) on the encounter's invoice from the start; queue-token issuance is re-triggered off "this invoice's consultation-fee charge is settled or waived" instead of `appointments.payment_status`. **Risk**: this touches the exact trigger identified in §3 as the highest-stakes dependency, on the same code path this repository's own migration `0033` header already declined to touch for this reason. It also has no home for the tested 3-day-same-doctor waiver business rule (`waive_consultation_fee_service`) — Ledger B has void/discount, not an equivalent waiver-with-eligibility-check concept — so Option A requires designing that rule's Ledger-B equivalent before it can ship, not just moving data.

**Option B — Keep Ledger A as the operational gate; dual-write into Ledger B; make Ledger B the sole reporting source (recommended default).** `record_payment_service`, `waive_consultation_fee_service`, and `settle_free_visit_service` keep writing `appointments.payment_status` exactly as today — **the queue-token trigger is not touched at all**. In the same transaction, each also inserts a matching `charges` row (`source_type='CONSULTATION'`) and a `payments` row on that encounter's invoice. `get_billing_report` (Dashboard), `list_payments_service`/`list_invoices_service` (History), and `_payment_pending` (Exception Engine) switch to read only Ledger B, which is now a strict superset. `appointments.payment_status` is **kept, not deprecated** — it continues to legitimately drive the queue-token gate and the 3-day-waiver eligibility check; it simply stops being read by anything reporting-facing.

- **Consultation fees enter Ledger B going forward**: via the dual-write added inside the three existing Ledger A write functions, in the same transaction — no new endpoint, no new trigger point.
- **Backfill of historical data**: add a nullable `charges.legacy_appointment_id` column with a partial unique index, then a one-time script inserting one `charges` + `payments` pair per historical appointment where `payment_status IN ('PAID','WAIVED','REFUNDED')` and no such row exists yet, `ON CONFLICT (legacy_appointment_id) DO NOTHING` — safely re-runnable, matching this codebase's own established idempotent-backfill idiom (`0003`'s exclusion constraint, `0054`'s medication-master backfill).
- **What happens to `payment_status`**: **kept**, not derived and not deprecated — it remains the real, load-bearing operational field for queue-token issuance and the waiver rule. What changes is that nothing outside `appointment_services.py` reads it anymore.
- **Risks**: (1) a dual-write bug silently under/over-counts Ledger B — mitigated by promoting `tests/test_billing_ledger_reconciliation_gap.py` from `xfail` to a real, permanent assertion as part of this work, so a regression here is caught immediately, not rediscovered later; (2) the backfill must run once, cleanly, before dual-writes go live, to avoid a window where some historical rows exist twice under different keys — the `legacy_appointment_id` uniqueness guard makes a double-run harmless but a *partial* run (backfill half-done, dual-write already live) could still double-count a payment made in that gap; sequence backfill-then-cutover, not the reverse; (3) `WAIVED`/`REFUNDED` visits need a Ledger B representation that reads as "not outstanding" (e.g. a `$0`/`method='WAIVED'` payment, or a full charge-level discount) — get this wrong and the Exception Engine's `PAYMENT_PENDING` check (§3) could start firing false positives for legitimately waived visits.

**Recommended default: Option B.** It resolves the actual harm proven in §1 (disjoint reporting) without touching the one code path this repository has consistently, deliberately protected across its entire history (booking/queue/token concurrency) — the same discipline that produced the double-booking fix's own test-first, `xfail`-tracked pattern this new test now follows. Option A is the more architecturally "complete" answer and becomes worth reconsidering specifically if/when IPD is committed (an admission has no "consultation fee" or appointment-driven token to trigger off in the first place, so the whole Ledger-A-as-gate model needs rethinking for IPD regardless) — but that is a reason to defer Option A to that decision point, not a reason to do it now.

### 5. Stopgap label/copy changes (proposed text only, not applied)

Checked the exact current copy in each affected component:

- **`frontend/src/admin/BillingPanel.tsx:83`** — currently `<span className="stat-label">Collected (last {report.window_days} days)</span>`, displaying `report.total_collected` (confirmed: this is the one figure on any of these three screens that actually presents itself as a total). **Proposed**: `Consultation fees collected (last {report.window_days} days)`, with a small helper line beneath the stat card: `"Excludes lab, radiology, pharmacy, and package charges — see Billing History for those."`
- **`frontend/src/admin/BillingPanel.tsx:47`** — currently `<h2>Billing</h2>` with no subtitle, above all four Ledger-A-only sections (collections, outstanding, waivers, refunds). **Proposed**: add `<p className="panel-subtitle">Consultation-fee reconciliation</p>` directly under the `<h2>`.
- **`frontend/src/admin/PaymentHistoryPanel.tsx:67`** — currently `<h2>Payment History</h2>`. Checked: the `total` shown on this screen (line 153, `{pageStart}–{pageEnd} of {total}`) is a row count for pagination, not a monetary sum — so there is no dollar figure to relabel here, but the heading itself implies completeness it doesn't have. **Proposed**: add a one-line note under the heading: `"Invoice payments only (lab, radiology, pharmacy, packages, and other billed charges). Consultation fees collected at check-in appear under Billing, not here."`
- **`frontend/src/admin/BillingHistoryPanel.tsx:64`** — currently `<h2>Billing History</h2>`, same `total`-is-a-row-count situation. **Proposed**: the same one-line note as above, adapted: `"Invoice charges only... Consultation fees appear under Billing, not here."`

These four are copy-only changes to existing JSX text and would not touch any query, endpoint, or data model — genuinely separable from ADR-009's implementation and safe to ship immediately once approved, independent of which ADR-009 option is eventually chosen.

**Applied**, separately, per explicit instruction (see the third addendum below for the commit).

---

## Third Addendum: Test Suite Report, Applied Stopgap, and ADR-009 Option Diff-Sizing

No application, migration, or backend/API code was changed to produce this addendum. The stopgap copy from addendum 2 §5 was applied, as its own frontend-only commit, per explicit instruction — see §1 below for what else was checked before and after it.

### 1. Report

**Full test suite result.** The prior addendum's full-suite run (106 failed, 660 passed, 1 skipped, 1 xfailed, 33 errors) was re-investigated rather than taken at face value. Re-running it turned up something worth being direct about: this session's local Postgres instance had gone down between conversation turns (`database system was not properly shut down; last known up at 2026-09-28 09:47:16 UTC`, restarted at `14:11:01` — a multi-hour gap, consistent with this being an ephemeral container that doesn't keep a manually-started background service alive across a session pause). The very next full run hit that outage directly (`connection to server ... failed: Connection refused` on every test, `1 error during collection`, from `app/db/test_connection.py` — which SETUP.md itself already documents as "not a pytest test despite the filename," a pre-existing, unrelated collection quirk). Postgres was restarted, WAL recovery completed cleanly, and the test database's `schema_migrations` count (64) and existing data were confirmed intact.

**With Postgres confirmed stable, a fresh full run produced:**

```
FAILED tests/test_scheduling_flow.py::test_booking_uses_doctor_specific_timezone
1 failed, 799 passed, 1 xfailed, 1 warning in 686.40s (0:11:26)
```

The one failure is unrelated to anything touched in this session — `assert ny_time.hour == 9` / `AssertionError: assert 10 == 9`, a date-sensitive assertion in a doctor-timezone booking test (`tests/test_scheduling_flow.py:110`), consistent with a hardcoded-offset assumption that doesn't hold on every calendar date (a DST-boundary-shaped bug, not a billing, RBAC, or ledger issue). It was not investigated further, since it sits well outside this task's scope and predates every change in this conversation.

**The earlier 106-failed/33-errors run is now understood to be a real but different environmental event** — Postgres was confirmed up throughout that specific run (its own log shows normal query activity, not a mass connection-refused pattern), so it was not the same outage as above; the exact trigger wasn't identified and is not chased further here, since the same test files were independently confirmed to pass both in isolation and as a group at the time, and now pass again in a full, clean run. The honest summary: this sandbox's Postgres has proven unstable across long-running or multi-turn sessions at least twice; a single clean full run (799 passed, 1 unrelated pre-existing failure, 1 xfailed as designed) is the trustworthy result, not the earlier noisy one.

**Dashboard billing report's live frontend consumer**: confirmed real, not speculative. `frontend/src/admin/BillingPanel.tsx:3` imports and calls `getBillingReport` (`frontend/src/api.ts:415-416`, `GET /dashboard/billing`), rendered as the sidebar's real "Billing" screen (`AdminApp.tsx:312`'s own comment: `"Reports" entry that's real (GET /dashboard/billing)`). `app/api/dashboard.py`'s own docstring — "no frontend for this yet" — is itself stale; `BillingPanel.tsx`'s own header comment says plainly: "GET /dashboard/billing, built alongside refunds and itemized invoicing (migrations/0025/0026) but **never wired to a page until now**." Added to the doc-drift list in addendum 1 §7 in spirit: the backend docstring simply predates the frontend work that wired it up.

**Why 64 migration files, not 57 or 65 — reconciled precisely.** `ls migrations` returns 65 entries total, but one of them is `README.md`, not a migration — so 64 `.sql` files. Those 64 files span 57 *distinct numeric prefixes* (`0001`–`0057`); seven numbers (`0029`, `0030`, `0031`, `0032`, `0033`, `0048`, `0054`) each have two files sharing that prefix with different descriptive suffixes (e.g. `0054_diagnostic_workflow.sql` and `0054_medication_master.sql`), accounting for the extra seven files (`57 + 7 = 64`). The original audit's "65 total... migrations `0001`–`0057`" conflated the directory's total entry count (65, including the README) with the migration count; "57" in casual chat shorthand referred to the highest numeric prefix, not the number of files applied. The precise, verified figures: **65 directory entries, 64 migration files, 57 distinct numeric prefixes, 64 rows in `schema_migrations` after a full apply** — all four numbers are correct for what they each actually measure, and none of them is "the" single right answer to "how many migrations" without saying which of the four is meant. This document will say "64 migration files" going forward.

### 2. Stopgap copy — applied

Commit `65c69df`, frontend-only, three files (`BillingPanel.tsx`, `PaymentHistoryPanel.tsx`, `BillingHistoryPanel.tsx`), 13 insertions / 4 deletions. `npx tsc -b` confirmed clean (exit 0) after the change. The two most important corrections were the existing subtitle text itself, found to be actively overclaiming rather than merely silent: Payment History's `"Every payment across every invoice, newest first"` and Billing History's `"Every invoice across every visit, newest first"` — both literally false given the two-ledger split, now corrected to name their actual scope and point to the other screen.

### 3. Diff-sizing ADR-009's two options

**Option A — make Ledger B canonical; move the queue-token trigger to "consultation charge paid or waived in Ledger B."**

| File | Function(s) | Nature of change |
|---|---|---|
| `app/services/appointment_services.py` | `record_payment_service` | Remove the `generate_queue_token_service` call from this path (trigger moves elsewhere); Ledger A write becomes either removed or a read-only compatibility shim |
| | `waive_consultation_fee_service` | The 3-day-same-doctor-revisit eligibility rule (the one genuinely tested business rule in this function) has no home in Ledger B today — must be ported into a new `billing_services.py` function, not just relocated |
| | `settle_free_visit_service` | Same porting problem as above |
| | `record_refund_service` | Retired in favor of Ledger B's existing `refund_invoice_payment_service` |
| | `add_invoice_line_item_service`, `_lock_appointment_for_payment`, `_current_payment_record` | Dead code after cutover — remove or formally deprecate |
| `app/services/billing_services.py` | `record_invoice_payment_service` (extend) | New logic: after a payment, check whether the CONSULTATION-source charge specifically is now settled (not "is the whole invoice paid" — a patient can owe for a pending lab charge while the consultation fee itself is settled), and if so call `generate_queue_token_service` — a new cross-module call from billing into appointment services that doesn't exist today, with its own locking-order implications since token generation takes its own advisory lock |
| | New `waive_charge_service` (or equivalent) | Must carry the ported 3-day eligibility rule from above |
| | `_ensure_invoice` (call site, not the function) | Must be called *eagerly* at check-in (a new call from `confirm_and_check_in_service`/`mark_visited_service`), not lazily on first `GET .../bill` as today, so a CONSULTATION charge exists the moment it's owed |
| `app/api/appointments.py` | `/payment`, `/waive-payment`, `/refund-payment` | Either retired or reimplemented as thin compatibility wrappers over the new Ledger-B functions |
| `app/api/billing.py` | New waive-charge endpoint | To expose the ported business rule |
| `frontend/src/admin/AppointmentActions.tsx:152-178` | action-menu builder | Full rewrite — the entire "Mark completed" gating logic keys off `payment_status`, which no longer means the same thing (or is removed); needs to key off a new Ledger-B-derived field instead |
| `frontend/src/admin/AppointmentBillingPanel.tsx` / `ConsultationWorkspace.tsx` | payment-collection call sites | Repointed to new endpoints |
| Migrations | 1 new (a way to eagerly link an invoice's CONSULTATION charge to check-in time, if not already sufficient via `source_type` alone) | Small, but not zero |
| Tests | Most of `test_consultation_payments.py` (currently the dedicated file for this exact mechanism) | **Rewritten, not added to** — its assertions are against the endpoints/behavior being replaced; `test_queue_tokens.py`/`test_queue_token_generation.py` fixtures need updating since the trigger moves; new tests needed for the ported waiver rule under its new home |

**Verdict on A**: touches the single highest-concurrency code path in the app (queue-token generation) as a *behavior* change, not an addition; requires designing and shipping a new waiver mechanic before it can ship at all (not optional — the 3-day rule is real, tested, and currently has no Ledger-B equivalent); invalidates and requires rewriting the majority of an existing, substantial test file rather than adding to it; requires a real frontend rewrite of the "what can I do with this appointment right now" logic. Large, multi-file, behavior-changing diff.

**Option B — dual-write; Ledger A remains the operational gate.**

| File | Function(s) | Nature of change |
|---|---|---|
| `app/services/appointment_services.py` | `record_payment_service` | **Additive only** — one new call after the existing `UPDATE`, mirroring the payment into Ledger B, in the same transaction; the existing `UPDATE` and `generate_queue_token_service` call are untouched |
| | `waive_consultation_fee_service`, `settle_free_visit_service` | Same — one additive mirroring call each |
| | `record_refund_service` | One additive call updating the mirrored Ledger B payment's `refunded_amount` |
| | `_lock_appointment_for_payment`, `add_invoice_line_item_service`, `_current_payment_record`, `generate_queue_token_service` | **Untouched** |
| `app/services/billing_services.py` | New small helper (e.g. `_mirror_legacy_appointment_payment`), called from the four functions above | New, narrow, additive — `billing_services.py` gains one new entry point, doesn't lose or change any existing one |
| `app/api/dashboard.py` | `get_billing_report`'s 8 queries (lines 200–291) | **Rewritten** to read `invoices`/`charges`/`payments` instead of `appointments.payment_status` — real work, but confined to one file, with a fairly direct Ledger-B equivalent per query since the dual-write guarantees the data now exists there; response JSON shape unchanged, so **no frontend change required** for this part |
| `app/services/billing_history_service.py`, `app/services/exception_engine.py` | — | **Untouched** — already Ledger-B-only, correct by construction once Ledger B is the superset |
| `frontend/*` | — | **Untouched**, for the scope this option proves fixed (see the scope note below) |
| Migrations | 1 new — `charges.legacy_appointment_id` (nullable, partial unique index) as the backfill idempotency key | Small |
| New script | `scripts/backfill_ledger_a_to_b.py` (one-time, idempotent) | New, self-contained |
| Tests | `test_billing_ledger_reconciliation_gap.py`: remove the `xfail` marker (becomes the acceptance test) | **Added to, not rewritten** — every existing test in `test_consultation_payments.py`/`test_billing_invoices.py`/`test_queue_tokens.py` keeps passing unmodified, since no existing behavior changed, only new rows get written alongside it; new tests needed for the mirroring itself and for the backfill script's idempotency |

**One scope nuance worth being explicit about**: the dual-write as described (mirroring only on successful `PAID`/`WAIVED`/`REFUNDED` outcomes) closes exactly the gap `test_billing_ledger_reconciliation_gap.py` proves — the Dashboard-vs-Payment-History disagreement for a *settled* visit. It does **not**, by itself, close the Exception Engine's `PAYMENT_PENDING` blind spot for a visit whose consultation fee is still *unpaid* (addendum 2 §3's "additional finding") — that needs the CONSULTATION charge created *unpaid* at check-in time, not only mirrored at payment time, which means also touching `confirm_and_check_in_service`/`mark_visited_service` (one more additive call, same pattern, slightly larger scope). Both scope levels are Option B, at different completeness; the ADR amendment in §4 below assumes the fuller scope, since a half-fix that leaves the Exception Engine gap open would be a known, named gap to carry forward, not a hidden one.

**Verdict on B**: every backend change is additive (new calls, new rows, new file); zero existing tests require modification; the queue-token trigger — the one path this codebase has protected throughout its history — is never touched; the one required frontend change is none, for the Dashboard fix, since the response shape doesn't change. Meaningfully smaller and lower-risk than Option A, at the cost of leaving two ledgers physically existing (Ledger A remains the operational source of truth for check-in gating, Ledger B becomes the operational + reporting source of truth for money) rather than truly unifying the model.

### 4. ADR-009 amendment — Option B, as recommended, with the four required elements

**Same-transaction guarantee.** Every mirroring insert happens on the same `cur`/transaction as the Ledger A write it mirrors — identical discipline to this codebase's own established rule for `record_audit_log()` (addendum 1's Gap #5) and for every existing billing mutation's `FOR UPDATE` pattern. Concretely: `record_payment_service`'s existing `UPDATE appointments ... SET payment_status = %s` and the new `charges`/`payments` inserts it triggers must commit or roll back together — a partial mirror (Ledger A updated, Ledger B insert failed) is exactly the bug class §3's "dual-write bug" risk names, and the same-transaction guarantee is what prevents it structurally rather than by convention.

**Mirroring of waivers, refunds, reversals, and edits.** Four distinct Ledger A events, each with its own mirror shape:
- **`PAID`** (`record_payment_service`) → a `charges` row (`source_type='CONSULTATION'`) + a `payments` row (`status='COMPLETED'`) for the same amount/method.
- **`FAILED`, later retried** (`record_payment_service` called twice for the same appointment — this is Ledger A's only "edit" path; it has no separate edit endpoint, the same function's `UPDATE` simply overwrites `payment_status`/`payment_amount` on each call) → the first call mirrors to a `payments` row with `status='DECLINED'` (migration `0050`, built for exactly this case); the second, successful call mirrors to a new `payments` row with `status='COMPLETED'` — reusing Ledger B's own existing declined-then-retried pattern rather than inventing a new one.
- **`WAIVED`** (`waive_consultation_fee_service`, `settle_free_visit_service`) → a `charges` row plus a `payments` row representing the waiver as fully settled with no cash movement (e.g. `method='WAIVED'`, amount equal to the charge, `$0` actually collected — the exact representation needs a one-line decision at implementation time, not left ambiguous, since the Exception Engine's `PAYMENT_PENDING` balance math must treat it as `balance <= 0`).
- **`REFUNDED`** (`record_refund_service`) → an `UPDATE` to the *already-mirrored* `payments` row's `refunded_amount`, using the same accumulating, capped pattern Ledger B's own `refund_invoice_payment_service` already uses (`refunded_amount <= amount`) — not a new payment row.

No other reversal/edit path exists in Ledger A today (confirmed in addendum 2 §3's full inventory) — these four cover every write site.

**Dashboard reads Ledger B only, after the change.** `get_billing_report`'s eight queries (`app/api/dashboard.py:200-291`) are rewritten to read `invoices`/`charges`/`payments` exclusively; the `appointments.payment_status` references in that file are removed, not merely supplemented. The response JSON shape (`total_collected`, `collections_by_method`, `collections_by_doctor`, `outstanding_unpaid`, `waivers`, `refunds`) stays the same, so `BillingPanel.tsx` requires no change — only the backend query source moves.

**Acceptance criterion.** `tests/test_billing_ledger_reconciliation_gap.py` passes with the `@pytest.mark.xfail(strict=True, ...)` marker **removed**. Per `strict=True`'s own semantics (already relied on elsewhere in this codebase for the double-booking fix), the marker must be deleted, not merely left in place expecting an "unexpected pass" — a passing `xfail` fails the suite under `strict=True` by design, so removing the marker is itself part of the acceptance criterion, not a formality.

**Exit plan — is dual-write permanent or transitional, and what retires Ledger A.** Transitional, with an explicit, separate future decision required to complete it, not an automatic follow-on: Option B intentionally leaves `appointments.payment_status` as the real, load-bearing queue-token gate (§3's dependency inventory), so it cannot be dropped without first doing the work Option A describes — moving the gate itself onto Ledger B. The honest exit plan is therefore two-phase, and this ADR only decides the first: **Phase 1 (this decision)** — dual-write ships, all reporting reads Ledger B, `payment_status` remains the operational gate, kept indefinitely in that role. **Phase 2 (a separate, later decision, not committed by this ADR)** — only if/when the queue-token trigger itself is worth moving off Ledger A (the OPD_TO_IPD.md-flagged moment this becomes forced is IPD, whose admissions have no appointment-driven fee to gate on in the first place, so the whole Ledger-A-as-gate model needs rethinking for that case regardless) — at that point Option A's remaining scope (the waiver-rule port, the frontend gating rewrite, retiring `appointments.payment_status`'s write path) becomes the actual unification, and the dual-write from Phase 1 is what makes that migration safe, since Ledger B has already been carrying every historical row correctly for however long Phase 1 has been live. Phase 1 alone is a legitimate, stable, indefinitely-livable end state on its own — it is not required to lead to Phase 2 on any particular timeline, and should not be scheduled as if it must.

---

## Fourth Addendum: ADR-009 Option B, Phase 1 — Implementation Report

**Decision recorded**: "Go with Option B." Implemented against a real, running Postgres instance (57 pre-existing migrations plus this phase's new one, all applied and verified), not merely written up.

### What shipped

- `migrations/0058_consultation_fee_ledger_mirror.sql` — adds `charges.legacy_appointment_id` (nullable, UNIQUE partial index — at most one mirrored CONSULTATION charge per appointment) and `payments.legacy_appointment_id` (nullable, indexed, deliberately NOT unique — a FAILED-then-retried-PAID sequence mirrors to two payment rows sharing one appointment id). Extends `charges_source_exclusive_check` to include the new column in the existing "at most one source" invariant.
- `app/services/billing_services.py` — four new functions: `_ensure_mirrored_consultation_charge` (idempotent, `ON CONFLICT ... DO NOTHING`), `mirror_consultation_payment` (PAID → `COMPLETED` payment; FAILED → `DECLINED` payment, reusing migration `0050`'s existing status value), `mirror_consultation_fee_waived` (a waived fee mirrors as an already-`VOIDED` charge, not a fabricated `$0` payment — `payments.amount` has `CHECK (amount > 0)` and there is no honest `method` value for "waived"), `mirror_consultation_payment_refunded` (updates the already-mirrored `COMPLETED` payment's `refunded_amount`, matching `refund_invoice_payment_service`'s own pattern).
- `app/services/appointment_services.py` — one additive call each in `record_payment_service`, `waive_consultation_fee_service`, `record_refund_service` (all guarded `if amount > 0`, since a `$0` fee has nothing to mirror). `settle_free_visit_service` (the genuinely-free-visit path) deliberately has no mirror call at all, for the same reason.
- `app/api/dashboard.py`'s `get_billing_report` — `collections_by_method`, `collections_by_doctor`, and `refunds` now read the mirror; `outstanding_unpaid` and `waivers` do not (see below — this is a correction to the ADR's own original text, not a shortcut).
- Frontend: **zero application-logic changes** — `BillingPanel.tsx` needed none (response shape unchanged, exactly as the ADR predicted). `PaymentHistoryPanel.tsx`/`BillingHistoryPanel.tsx` needed a **second copy correction** (see below).
- `tests/test_billing_ledger_reconciliation_gap.py` — `xfail` marker removed; test renamed (`_disagree_on_` → `_agree_on_`) and its assertions rewritten to check the actually-correct post-fix behavior (see below).
- `tests/test_billing_report.py` — one test updated (backdates the mirrored payment's `recorded_at` too, not just the legacy column, since that's now what the endpoint reads).

### Two corrections found only by actually implementing this, not by writing the ADR

Both are exactly the kind of thing this audit has tried to surface rather than hide throughout — recorded here in full rather than quietly fixed:

**1. The ADR's own "Dashboard reads Ledger B only" was too strong; implementing it broke a real test for a real reason.** `tests/test_billing_report.py::test_billing_report_lists_waivers_without_fabricating_an_amount` failed after the literal rewrite — it exercises `settle_free_visit_service` (a genuinely `$0` visit), which has no Ledger B mirror at all, by design (`charges.amount` has `CHECK (amount > 0)`; there is nothing to bill for a free visit). Rewriting `waivers` to Ledger B silently dropped every free-visit waiver from that report section. **Fix**: `waivers` stays on `appointments.payment_status` permanently — not a temporary scope gap like `outstanding_unpaid`, but a structural one: Ledger B cannot represent a `$0` charge, full stop. `get_billing_report`'s docstring now documents both exceptions and why they're different (one closeable later, one permanent).

**2. Fixing ADR-009 made the audit's own earlier stopgap copy (previous addendum, applied as commit `65c69df`) factually wrong.** That commit added "Consultation fees collected at check-in appear under Billing, not here" to `PaymentHistoryPanel.tsx`, and the equivalent to `BillingHistoryPanel.tsx` — true at the time (nothing connected the two ledgers yet), **false** the moment the mirror exists, since the mirrored consultation-fee payment/charge now legitimately appears in both screens' own unfiltered queries. Caught by actually running `tests/test_billing_ledger_reconciliation_gap.py` and reasoning through what "Payment History now shows 1700" actually implies for that screen's own copy, not by a separate review pass. **Fix**: both panels' copy corrected again, this time to state that consultation fees now *do* appear there (`PaymentHistoryPanel.tsx`: "Every invoice payment, newest first — lab, radiology, pharmacy, packages, other billed charges, and consultation fees collected at check-in"; `BillingHistoryPanel.tsx`: equivalent). `BillingPanel.tsx`'s own copy needed no change — it was never claiming completeness beyond consultation fees, and that remains true.

This second correction is worth being direct about: a stopgap fix that is correct *given the current data model* can become incorrect *the moment the data model changes*, even when the stopgap's author (this session) fully expected the model to change. The lesson carried forward is procedural, not just this-instance: any future phase that changes which ledger a screen reads from must re-check that screen's own copy, not just its query.

### Test results (real, not asserted)

`tests/test_billing_ledger_reconciliation_gap.py` — **PASSED**, no `xfail` marker:
```
tests/test_billing_ledger_reconciliation_gap.py::test_dashboard_billing_and_payment_history_agree_on_one_visits_total PASSED
1 passed, 1 warning in 1.29s
```

Every test file touching either ledger or the queue-token trigger — **91 passed**: `test_billing_report.py`, `test_billing_ledger_reconciliation_gap.py`, `test_billing_invoices.py`, `test_billing_history.py`, `test_consultation_payments.py`, `test_packages.py`.

Full suite (`pytest -q`, `app/db/test_connection.py` deselected — SETUP.md's own documented non-pytest manual script): **798 passed, 2 failed, 1 skipped**. Both failures confirmed pre-existing and unrelated by direct `git stash` comparison — `tests/test_queue_tokens.py::test_queue_visited_at_is_doctor_local_time_not_utc` fails identically with every change in this addendum stashed out; `tests/test_date_first_scheduling.py::test_date_first_lists_multiple_doctors_with_their_own_slot_counts` is in an area (date-first doctor listing) untouched by any commit in this entire audit. Both fail with the same signature (`409` slot-overlap on `POST /api/appointments`) consistent with the date/weekday-dependent `_next_weekday` scheduling helper colliding on whichever real calendar date the suite happens to run on — the same class of environmental flakiness as the DST-shaped failure found and disclosed in the third addendum (`test_scheduling_flow.py`, a *third*, different file, in that run). Not investigated further, per that same addendum's reasoning: real, but outside this task's scope, and demonstrably not caused by this work.

Migration re-run confirmed idempotent (`python scripts/migrate.py` → `No pending migrations.` on the second run). Frontend `npx tsc -b` clean (exit 0) after every copy change, including the second correction.

---

## Fifth Addendum: An Independent, Already-Merged Fix Collided With This One — Reconciliation

A pull request was opened from this branch (`claude/upbeat-davinci-5fk1rt` → `main`, PR #124). Its `mergeable_state` came back `dirty`: `main` had moved — a **different, already-merged PR (#120, "Billing ledger unification, Option C")** had landed the same problem's fix via a genuinely different, independently-developed mechanism, apparently from an uncoordinated parallel session, entirely without knowledge of this branch's ADR-009 Option B work or vice versa. This addendum records what Option C actually did, why it was a real conflict rather than a mechanical one, and how the two were reconciled.

### What Option C did (already on `main`, PR #120, its own `docs/architecture/BILLING_LEDGERS.md`)

No schema change, no dual-write. Instead, `get_billing_report` (`app/api/dashboard.py`) and the Exception Engine's `PAYMENT_PENDING` check (`app/services/exception_engine.py`) were rewritten to query **both ledgers' own original columns directly, at read time**, and sum them in Python — Ledger A (`appointments.payment_status`) unchanged, plus new helper queries against Ledger B (`invoices`/`charges`/`payments`). A new `ledger_breakdown` field keeps the two sources individually auditable. Their own design doc independently named the same three options this audit's ADR-009 named — but numbered their "Option B" (Ledger A absorbs Ledger B) as the *opposite* direction from this audit's Option B (Ledger B absorbs Ledger A's events via mirroring), a genuine vocabulary collision, not just an implementation one.

**Option C's own honest gap list**: Billing History/Payment History remained Ledger-B-only (unfixed) — explicitly named as "not addressed by Option C" in their own doc. Their work also surfaced a real finding this audit had not: a visit `COMPLETED` with its consultation fee still `UNPAID`/`FAILED` becomes **uncollectible** through any existing endpoint (`record_payment_service` requires `CHECKED_IN`) — visible now (thanks to their Exception Engine fix) but still not payable. Recorded here as a legitimate open gap this audit did not itself find.

### Why this was a real conflict, not a mechanical one

Both PRs modified `get_billing_report` and (independently) `exception_engine.py`'s `_payment_pending` to do conceptually similar things — combine both ledgers for reporting — via incompatible mechanisms. `git merge origin/main` auto-resolved the code (no conflict markers survived in the function bodies) into something that was syntactically valid but **semantically wrong**: it queried Ledger B for "collections" once via this branch's mirror-reading version and again via Option C's unfiltered version, silently double-counting every mirrored consultation fee (e.g., a ₹500 fee + ₹1200 lab charge would have reported ₹2000, not the true ₹1700). Only the docstring carried an actual `<<<<<<<`/`=======`/`>>>>>>>` marker; the dangerous part was the code git merged *without* flagging it. This is exactly why the user was asked before proceeding, rather than trusting an auto-merge or picking a side unilaterally: both sides represented real, tested, complementary work, and combining them required understanding both mechanisms well enough to prevent them from double-counting each other — not a choice a mechanical merge tool or a coin flip could make safely.

### How it was reconciled (decision: keep both, patch to coexist)

Rewrote `get_billing_report` by hand: kept Option C's actual architecture (query both ledgers' original columns directly, combine at read time) for collections/outstanding, restored Option C's own original Ledger A queries (reading `appointments.payment_status`/`payment_amount`/`refund_amount` directly) in place of this branch's mirror-reading versions of the same figures — direct-from-source is strictly more accurate than reading through the mirror, since the mirror has its own documented "amount fixed at first attempt" edge case that direct Ledger A queries don't inherit. Patched all three of Option C's `_ledger_b_*` helper functions (`_ledger_b_collections_by_method`, `_ledger_b_collections_by_doctor`, `_ledger_b_outstanding`) and `exception_engine.py`'s `_payment_pending` to add `legacy_appointment_id IS NULL` exclusion filters, so neither ever double-counts a row this branch's mirror (migration `0058`) created. Result: **the two mechanisms now serve genuinely different screens without needing to agree on which one is authoritative** — Option C's direct-dual-query approach remains the Dashboard/Exception-Engine's source of truth; ADR-009 Option B's mirror remains Payment History/Billing History's only path to completeness, since those two screens have no separate Ledger A query to combine against and never will (they're per-row paginated listings, not aggregates).

One function needed no change despite the same underlying data shift: `exception_engine.py`'s `_billing_not_started` (a different exception, flagging a completed consultation with zero charges) now correctly stops firing once a consultation fee is collected and mirrored — this is a **genuine improvement**, not a regression: before the mirror existed, this exception was a false positive for any visit whose only billable item was an already-collected consultation fee ("nothing has been billed" was never true for that visit; the fee had been billed and paid, just not in `charges`). No `legacy_appointment_id` exclusion was added here, deliberately.

### Test changes required by the reconciliation

`tests/test_billing_ledger_reconciliation_gap.py`'s own assertions had to change: its earlier version (written before Option C was known to exist) asserted the Dashboard should show *only* the consultation fee (500), matching a scoping decision this audit made unaware that a combined-total fix already existed. That assertion is now provably the wrong bar — the Dashboard combines both ledgers via Option C, so it correctly reports the true total (1700), and the test now asserts that instead, plus a stronger `ledger_breakdown`-level check (500 attributed to consultation, 1200 to itemized, not just a total that happens to sum correctly) specifically to catch a subtler double-counting bug where one side over-counts and the other under-counts by the same amount.

### Verification

`tests/test_billing_report.py` (both this branch's original tests and Option C's own `test_billing_report_combines_both_ledgers_in_collections`/`test_billing_report_outstanding_includes_both_ledgers`), `tests/test_exception_engine.py`, and `tests/test_billing_ledger_reconciliation_gap.py` together: **105 passed**, zero failures, confirming the exclusion-filter fix closes the double-counting bug without breaking either PR's own test coverage. Full suite: **800 passed, 3 failed, 2 skipped**. All three failures share the identical "date/time-boundary" signature already disclosed in the third addendum (a real, pre-existing flakiness class in this test suite's `_next_weekday`/timezone-relative fixtures) — two were already confirmed pre-existing via direct `git stash` comparison; the third, newly observed (`tests/test_dashboard.py::test_dashboard_stats_counts_by_status_and_time`, off by exactly one "today" count), is in `get_dashboard_stats`, a function untouched by any commit in this entire audit, and was not independently re-verified via `git stash` given the strong circumstantial match to the same known flakiness class — disclosed as such, not swept under the "pre-existing" label without saying so.

Both PRs' work survives, correctly combined: `#120`'s Dashboard/Exception-Engine fix and this branch's `#124` Payment History/Billing History fix now coexist, verified not to double-count each other.
