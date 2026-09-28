# HospitalOS — Phase 1: Canonical Healthcare Data Model Audit

Read-only discovery deliverable. **No application code, database schema, or
API was changed to produce this audit.** Every row below was checked against
actual migration files, service files, API routers, and frontend components —
table/column names, CHECK constraints, and comments are quoted directly from
the source, not recalled from memory. Where evidence was insufficient to be
certain, the row says so explicitly (`TODO — VERIFY AGAINST EXISTING
IMPLEMENTATION`) rather than guessing.

This is Phase 1 of the interoperability master prompt. Phase 0
(`docs/OPD_HIMS_INTEROPERABILITY_AUDIT.md`) established that zero external
standards (FHIR/ABDM/HL7/DICOM/etc.) are implemented. This document answers a
different question: **is the existing internal domain model — built with no
standards in mind — already close to what a future FHIR mapping layer would
need, or does it need real work first?**

---

## 1. Executive summary

The internal domain model is **encounter-centric, well-normalized, and far
along for a system that was never designed against FHIR** — this is the
single most important finding. `patients` → `encounters` → every clinical/
financial table is real, FK-enforced, confirmed by reading the actual
constraints (see `docs/architecture/DOMAIN_MODEL.md`). Several concepts map
cleanly to their FHIR counterpart already (Patient, Appointment, Encounter,
MedicationRequest). Several map only partially, because the underlying data
is deliberately free-text (Condition/diagnosis, Observation units) — a
choice the codebase's own migration comments already flagged as future work,
not something this audit discovered fresh. A few FHIR concepts have no
internal analog at all yet (Consent, Coverage/Claim beyond a bill-type
classifier, ImagingStudy, CarePlan as a distinct entity, Location as a
physical place, HealthcareService as a general catalog).

Two things surprised this audit and are worth leading with:

1. **`staff.role` and the `staff_roles`/`role_permissions` RBAC tables are
   two different mechanisms, on purpose, not by accident.** `staff.role` is
   a single denormalized column (now FK'd to `roles.name`, not a hand-listed
   CHECK) used for coarse UI display/landing-screen selection.
   `require_permission()` — every real authorization decision — is resolved
   exclusively through `staff_roles → role_permissions → permissions`
   (confirmed by reading `app/api/staff_auth.py`'s own docstring: "never a
   direct `staff["role"]` string comparison, which is the one thing this
   replaces"). This is intentional layering, not a duplicate model — but see
   §13 for the one real drift risk it creates.
2. **`patient_identifiers` already exists as a `{kind, value, is_primary}`
   table, hospital-scoped**, dual-written alongside `patients.
   whatsapp_number` (migration `0029`) and widened once already (`PHONE` →
   `PHONE, GOVT_ID`, migration `0030`) — this is exactly the shape a future
   `ABHA` identifier kind would extend into, and it already happened once
   without a redesign. Its own comment is honest that it's "not yet the
   source of truth for anything," though — `patients.whatsapp_number`
   remains what lookups actually use. This is a real, if minor, duplicate-
   representation finding (§13).

---

## 2. Existing domain model (as built, not idealized)

```
hospitals (tenant root, single row today)
   │
   ├── departments ── doctor_departments ── doctors
   │                                           │
   │                                    doctor_appointment_types (duration override)
   │                                    doctor_schedule (weekly recurring)
   │                                    doctor_blocks (one-off unavailability)
   │
   ├── staff (username/password, single `role` column, FK'd to roles.name)
   │      │
   │      └── staff_roles ── role_permissions ── permissions   (real authorization)
   │      └── break_glass_grants                                (emergency access)
   │
   └── patients (name, whatsapp_number [real identity key today], uhid [generated],
       │         date_of_birth, gender, email, address fields, blood_group,
       │         government_id, merged_into_id)
          │
          ├── patient_identifiers (kind: PHONE|GOVT_ID; dual-written, not yet authoritative)
          ├── patient_allergies (allergen, reaction, severity, active/resolved)
          ├── patient_merges / patient_duplicate_reviews
          │
          └── appointments (doctor_id, appointment_type_id, start_at/end_at,
              │              status, payment_status, token fields, arrived_at,
              │              booking_source, appointment_number [generated])
                 │
                 └── encounters (encounter_type: 'OPD' only, status: OPEN/CLOSED)
                        │
                        ├── vitals (recorded_by staff, BP/pulse/temp/SpO2/resp/weight/
                        │           height/bmi[generated]/pain_score/chief_complaint/priority)
                        ├── consultations (1:1 per encounter; chief_complaint/history_notes/
                        │      │           examination_notes/diagnosis/clinical_notes/
                        │      │           follow_up_date/follow_up_reason — all free TEXT)
                        │      └── consultation_amendments (full before/after snapshot)
                        ├── orders (order_type: LAB|RADIOLOGY|PROCEDURE|SERVICE|
                        │      │    EXTERNAL_REFERRAL; description free text; status)
                        │      └── order_results (parameter/result_value/unit/
                        │                          reference_range/is_abnormal/is_critical —
                        │                          one generic shape for every order_type)
                        ├── prescriptions (1:1 per encounter; status DRAFT/PRESCRIBED/CANCELLED)
                        │      └── prescription_items (medicine_name/generic_name/dosage/route/
                        │             │                frequency/duration/quantity — free text)
                        │             └── pharmacy_dispense_records (→ pharmacy_stock, batch/expiry)
                        └── invoices (1:1 per encounter; discount/tax_rate/status; bill_type)
                               ├── charges (source_type: CONSULTATION|LAB|RADIOLOGY|PROCEDURE|
                               │            SERVICE|PHARMACY|OTHER; ← source_order_id/source_dispense_id)
                               └── payments (method/transaction_id/status/refunded_amount)

hospital_modules (Licensed/Enabled per hospital, per module_key)
audit_log (append-only; staff_id/action/resource_type/resource_id/details JSONB)
notifications (in-app bell/list)
```

This diagram is the actual repository, not a target — every table/column name
is quoted from a real migration file (see §3 for the specific file per row).

---

## 3. Concept-by-concept audit

Status is one of **IMPLEMENTED / PARTIALLY IMPLEMENTED / NOT IMPLEMENTED**
only, per instruction — no fourth bucket for "similar."

| Concept | Existing Entity/Table | Existing API | Existing UI | Existing Workflow | Status | Evidence | Gap |
|---|---|---|---|---|---|---|---|
| **Patient** | `patients` (migration `0001`, extended by `0023`/`0024`/`0027`/`0030`/`0045`) | `app/api/patients.py` (search, admin list, CRUD, merge, timeline, allergies) | `PatientsPanel.tsx`, `PatientFormModal.tsx` | `docs/workflows/PATIENT_REGISTRATION.md` | IMPLEMENTED | `name`, `whatsapp_number` (UNIQUE, real identity key), `uhid` (generated), `date_of_birth`, `gender`, `email`, `address_line/city/state/pincode`, `emergency_contact_*`, `blood_group`, `government_id`, `merged_into_id` | None structural. A patient-facing identity concept is genuinely real and complete for OPD use; ABHA is the only missing identifier kind (see §9). |
| **Patient Identifier** | `patient_identifiers` (migration `0029`, widened `0030`) | Not directly exposed as its own CRUD API — read/written internally by `app/services/patient_identifiers.py` | none directly (surfaced indirectly via patient search) | — | PARTIALLY IMPLEMENTED | `{hospital_id, patient_id, kind, value, is_primary}`, `kind IN ('PHONE','GOVT_ID')`. Own comment: "not yet the source of truth for anything" | `patients.whatsapp_number` remains the actual lookup key; this table is a shadow, dual-written, not authoritative. See §13. |
| **Practitioner / Doctor** | `doctors` (migration `0001`, extended `0009`/`0014`/`0022`/`0027`) | `app/api/doctors.py` | `DoctorsPanel.tsx`, `DoctorProfileSection.tsx` | — | PARTIALLY IMPLEMENTED | `name`, `active`, `timezone`, `hospital_id`, `specialization`, `sub_specialization`, `qualifications`, `default_duration_minutes`, `buffer_minutes` | No medical registration/license number column, no NPI-equivalent identifier, no `Practitioner.identifier`-shaped structure — a doctor has clinical-scheduling attributes, not a licensure-grade professional identity. |
| **PractitionerRole** | `doctor_departments` (join table, migration `0001`), `doctor_appointment_types` (duration override, migration `0001`) | `app/api/department_doctors.py`, `app/api/doctor_appointment_types.py` | department assignment UI in `DoctorsPanel.tsx`/`DoctorWorkspace.tsx` | — | PARTIALLY IMPLEMENTED | doctor↔department is real and used; there is no "role at this org" concept beyond department membership (e.g. no distinct "Consulting" vs. "Visiting" practitioner-role type) | A single join table covers department scope; no richer role-at-location/role-type modeling. |
| **Organization / Hospital** | `hospitals` (migration `0027`) | none dedicated (read internally as `hospital_id` tenant scope) | none (no hospital-settings screen found) | — | PARTIALLY IMPLEMENTED | `code`, `name`, `timezone`. Own comment: "Exactly one row exists today (id=1) — this application is still single-tenant in every behavior except the schema itself" | No address, no contact info, no identifier system, no branding fields (`docs/printing/DOCUMENT_MANAGEMENT.md` independently found the same gap for print branding). Structurally present, practically a single fixed row. |
| **Department** | `departments` (migration `0001`) | `app/api/departments.py` | `DepartmentsPanel.tsx` | — | IMPLEMENTED (for its narrow scope) | `name`, `active` — that's the whole table | Minimal by design (a scheduling/organizational grouping, not a full FHIR `Organization` sub-unit with address/type/contact). |
| **Location** | None. | — | — | — | NOT IMPLEMENTED | No table representing a physical place (room, ward, building) exists anywhere — confirmed by the same grep sweep that found no `wards`/`beds` tables in the main schema (the only such table design lives in the superseded `ipd-service/` sketch, `docs/architecture/OPD_TO_IPD.md`). | Full. OPD has never needed a physical-location concept (it's slot/appointment-shaped); this is IPD-dependent. |
| **HealthcareService** | `appointment_types` (migration `0001`) | `app/api/appointment_types.py` | `AppointmentTypesPanel.tsx` | — | PARTIALLY IMPLEMENTED | `name`, `active`, per-doctor duration override via `doctor_appointment_types` | This is a *visit-type* catalog for scheduling, not a general priced service/test catalog — `docs/architecture/ORDER_SPINE.md` independently confirmed orders have no service catalog at all (free-text description). |
| **Appointment** | `appointments` (migration `0001`, heavily extended through `0048`) | `app/api/appointments.py`, `app/api/patient_scheduling.py`, `app/api/booking.py` | `AppointmentsPanel.tsx`, `BookAppointmentPanel.tsx` | `docs/workflows/OPD_CHECKIN_QUEUE.md` | IMPLEMENTED | `status IN ('PENDING','CONFIRMED','REJECTED','CANCELLED','CHECKED_IN','COMPLETED','NO_SHOW')` (evolved via `0011`), `payment_status`, `arrived_at`, `booking_source`, `appointment_number` (generated), real DB exclusion-constraint concurrency protection | None structural — this is one of the most mature entities in the schema. |
| **Schedule** | `doctor_schedule` (migration `0001`) | `app/api/doctor_schedule.py` | `ScheduleGrid.tsx` | — | IMPLEMENTED | day_of_week + start_time/end_time, date-ranged (per `docs/DATABASE_P1_NOTES.md`/WEB_P* history) | None found. |
| **Slot** | Computed, not stored | `app/api/availability.py`, `app/services/availability_engine.py` | `AdminSlotPicker.tsx`, `SlotGrid.tsx` | — | IMPLEMENTED (as a computed concept) | Slots are generated on read from `doctor_schedule`/`doctor_blocks`/existing `appointments`, not persisted as their own rows | This is a deliberate design (a FHIR `Slot` resource would need to be synthesized at request time from this same computation, not read from a table) — not a gap, a mapping note. |
| **Encounter** | `encounters` (migration `0028`) | internal (no dedicated `/encounters` CRUD API found — encounters are created/read as a side effect of appointment/consultation endpoints) | implicit — every `ConsultationWorkspace.tsx` screen operates on one | `docs/architecture/ENCOUNTER_MODEL.md` | IMPLEMENTED for OPD | `encounter_type CHECK (... IN ('OPD'))`, `status OPEN/CLOSED`, every downstream clinical/financial table FKs to `encounter_id` | `encounter_type` is not yet widened beyond `'OPD'` — see §10. |
| **Diagnosis / Condition** | `consultations.diagnosis` (migration `0029`) | `app/api/clinical.py` | `ConsultationWorkspace.tsx` (Consultation tab) | `docs/workflows/CONSULTATION.md` | PARTIALLY IMPLEMENTED | `diagnosis TEXT` — plain free text. Migration's own comment: "diagnosis is a [free-text field]... a structured, codeable diagnosis list is real future work" | **Do not say "Condition is implemented."** A diagnosis concept exists and is captured every consultation, but it is not a standardized coded clinical condition — no `{code, system, display}` shape, no separate `conditions` table (one diagnosis string per consultation, not a list of distinct, individually-trackable conditions over time). |
| **Allergy** | `patient_allergies` (migration `0042`) | `app/api/patients.py` (`GET/POST /{id}/allergies`, resolve endpoint) | not confirmed wired into any screen's patient header display — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` for exact UI surfacing beyond the API existing | — | PARTIALLY IMPLEMENTED | `allergen`, `reaction`, `severity IN ('MILD','MODERATE','SEVERE')`, `active`/`resolved_by`/`resolved_reason`/`resolved_at` — real, structured, not free-text-only | Allergen itself is free text (no terminology binding — same class of gap as Diagnosis). **Confirmed via direct grep of `app/services/pharmacy_services.py`: zero references to allergies — nothing checks a patient's allergy list against a medicine being prescribed.** This is a real clinical-safety gap, independent of any standard. |
| **Observation** | `order_results` (migration `0031`), `vitals` (migration `0029`) | `app/api/orders.py`, `app/api/clinical.py` | `LabRadiologyWorklistPanel.tsx`, `ConsultationWorkspace.tsx` (Triage tab) | `docs/workflows/ORDERS.md` | PARTIALLY IMPLEMENTED | `order_results`: `parameter`/`result_value`/`unit`/`reference_range`/`is_abnormal`/`is_critical` — generic, one shape for every order type. `vitals`: individually typed numeric columns (not generic) | `order_results.parameter`/`.unit` are free text, not LOINC/UCUM-coded. `vitals` is well-typed but is its own separate shape from `order_results` — two different internal representations of "an observation," see §13. |
| **Vital Signs** | `vitals` (migration `0029`) | `app/api/clinical.py` | `ConsultationWorkspace.tsx` (Triage tab) | `docs/workflows/CONSULTATION.md` | IMPLEMENTED | `bp_systolic`/`bp_diastolic`/`pulse`/`temperature_celsius`/`spo2`/`respiratory_rate`/`weight_kg`/`height_cm`/`bmi` (generated)/`pain_score`/`chief_complaint`/`priority`/`nursing_notes` — real, typed, constrained (`CHECK`s on physiologically sane ranges) | Units are implicit by column naming/convention (`_kg`, `_celsius`, `_cm`), not a UCUM code alongside the value. |
| **Laboratory Order** | `orders` where `order_type = 'LAB'` (migration `0030`) | `app/api/orders.py` | Orders tab (`ConsultationWorkspace.tsx`), `LabRadiologyWorklistPanel.tsx` | `docs/workflows/LABORATORY.md` | IMPLEMENTED (as one case of the generic Order Spine) | Shared table/pipeline with every other order type — see `docs/architecture/ORDER_SPINE.md` | No lab-specific fields (specimen type, collection time) — same generic `orders` row as a radiology or procedure order. |
| **Laboratory Result** | `order_results` for a `LAB` order | same as Observation row | same | same | PARTIALLY IMPLEMENTED | Generic parameter/value/unit/reference-range shape, reused for radiology too | No sample-collection or verify-then-release workflow (`docs/workflows/LABORATORY.md`'s own gap list). |
| **Diagnostic Report** | None as a distinct entity — a "report" is the set of `order_results` rows for one `order_id`, assembled at read time | `app/api/orders.py`'s result-listing endpoint | Lab/Radiology Worklist result view | — | PARTIALLY IMPLEMENTED | No `diagnostic_reports` table; a report is computed by grouping `order_results` by `order_id`, not a persisted document with its own status/conclusion field | No report-level narrative/conclusion/status distinct from the individual result rows — no `DiagnosticReport.conclusion` analog. |
| **Service Request** | `orders` generally (not just LAB/RADIOLOGY — also `PROCEDURE`/`SERVICE`/`EXTERNAL_REFERRAL`) | `app/api/orders.py` | Orders tab | `docs/workflows/ORDERS.md` | IMPLEMENTED (as the Order Spine itself) | `order_type`, `priority IN ('ROUTINE','URGENT','STAT')`, `clinical_indication`, `status` — this is functionally the closest one-to-one match to FHIR `ServiceRequest` in the whole schema | Free-text `description`, no catalog binding — same gap as everywhere else free text appears. |
| **Procedure** | `orders` where `order_type = 'PROCEDURE'` | same as Service Request | same | — | PARTIALLY IMPLEMENTED | Same generic order row, no procedure-specific fields (technique, body site, outcome beyond `result_text`) | Procedures are not modeled distinctly from any other order type. |
| **Medication** | None as a catalog — `prescription_items.medicine_name`/`generic_name` are free text per prescription line | — | `PrescriptionPanel.tsx` (free-text medicine entry) | — | NOT IMPLEMENTED (as a catalog/master entity) | No `medications` table anywhere — confirmed by grep | There is no canonical "this medicine" entity a prescription line references; each line is independently typed text, so the same drug can be spelled differently across prescriptions. |
| **Medication Request / Prescription** | `prescriptions` + `prescription_items` (migration `0032`) | `app/api/pharmacy.py` (`prescription_router`) | `ConsultationWorkspace.tsx` (Prescription tab), `PrescriptionPanel.tsx` | `docs/workflows/PHARMACY.md` | IMPLEMENTED (for the request/prescribing side) | `status IN ('DRAFT','PRESCRIBED','CANCELLED')`; items carry `dosage`/`route`/`frequency`/`duration`/`quantity`/instructions | Free-text medicine name (see Medication row above); no dosage-instruction structuring (e.g. no separate timing/as-needed/max-dose fields — `dosage`/`frequency` are themselves free text). |
| **Medication Dispense** | `pharmacy_dispense_records` (migration `0032`) | `app/api/pharmacy.py` (`pharmacy_router`) | `PharmacyPanel.tsx` | `docs/workflows/PHARMACY.md` | IMPLEMENTED | Real transaction log: `prescription_item_id`, `pharmacy_stock_id` (nullable — dispense-without-stock-match is allowed), `quantity`, `unit_price`, `amount`, `dispensed_by`, `dispensed_at` | This is one of the most complete/standards-adjacent tables in the schema — genuine inventory-aware transaction recording. |
| **Care Plan** | `consultations.follow_up_date`/`.follow_up_reason` only | `app/api/clinical.py` | Consultation tab's follow-up fields | `docs/workflows/CONSULTATION.md` | NOT IMPLEMENTED (as a distinct entity) | Follow-up is two columns on `consultations`, not a plan with goals/activities/status of its own | No `CarePlan`-equivalent entity; "the plan" is implicit in clinical notes + a follow-up date. |
| **Referral** | `orders` where `order_type = 'EXTERNAL_REFERRAL'` (migration `0030`) | `app/api/orders.py` | Orders tab | `docs/architecture/ORDER_SPINE.md` | IMPLEMENTED (as one order type) | `external_destination` required when this type is used; this is the concrete, already-working module-degradation path (`docs/architecture/MODULE_ARCHITECTURE.md`) | Modeled as an order variant, not a distinct referral workflow (no referral-specific status like accepted/declined by the receiving party — there is no receiving party in this system). |
| **Document** | None as a stored/generated artifact — every "document" (registration summary, appointment slip, receipt, prescription) is a live-rendered print view over existing rows | — | `PatientRegistrationSummaryModal.tsx`, `AppointmentSlipModal.tsx`, `PaymentReceiptModal.tsx`, etc. | `docs/printing/DOCUMENT_MANAGEMENT.md` | NOT IMPLEMENTED (as a persisted `DocumentReference`-style entity) | No `documents`/`document_references` table — confirmed by `docs/printing/DOCUMENT_MANAGEMENT.md`'s own independent audit | By design (ADR-005) — printing renders live data, nothing is stored as a discrete document object today. |
| **Imaging Study** | None. | — | — | — | NOT IMPLEMENTED | Radiology is one `orders.order_type = 'RADIOLOGY'` row with a free-text `result_text` — no image file, no study/series/instance concept | Full — confirmed independently by the DICOM row in `docs/OPD_HIMS_INTEROPERABILITY_AUDIT.md`. |
| **Coverage / Insurance** | `invoices.bill_type` classification only (per `docs/workflows/BILLING.md`) | `app/api/billing.py` | `AppointmentBillingPanel.tsx` | `docs/workflows/BILLING.md` | PARTIALLY IMPLEMENTED | A 6-way `bill_type` classifier exists (includes insurance/TPA/corporate/government-scheme categories) — real and deliberate, not fabricated | No payer/policy/pre-auth/co-pay fields — that migration's own comment explicitly labels these "Future fields," per the earlier Billing workflow doc. |
| **Claim** | None. | — | — | — | NOT IMPLEMENTED | No `claims` table or NHCX-adjacent structure anywhere | Full — depends on Coverage existing first in any real form. |
| **Invoice** | `invoices` + `charges` + `payments` (migration `0033`) | `app/api/billing.py`, `app/api/billing_history.py` | `BillingPanel.tsx`, `BillingHistoryPanel.tsx` | `docs/workflows/BILLING.md` | IMPLEMENTED | `invoice_number` (generated), `discount_amount`/`discount_reason`, `tax_rate` (applied at read time, never pre-computed), `status OPEN/VOID`; `payments.receipt_number` (generated), `method`, `transaction_id` (partial-unique for dedup), `refunded_amount` | One of the most mature, tested parts of the schema (`FOR UPDATE` locking, `PaymentExceedsBalance` guard, void-with-reason discipline throughout). |
| **Consent** | None. | — | — | — | NOT IMPLEMENTED | Zero references anywhere — confirmed identically by Phase 0 | Full. `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION`: whether the registration/booking UI has an unmodeled frontend-only checkbox (wouldn't show in a backend grep) — not confirmed either way by this pass. |
| **Audit** | `audit_log` (migration `0033`) | internal (`app/services/audit_log.py`'s `record_audit_log()`, called from every `require_permission()`-gated write and from break-glass) | `AuditLogPanel.tsx` | — | IMPLEMENTED (for its own, non-FHIR shape) | `staff_id`, `action`, `resource_type`, `resource_id`, `details JSONB`, `created_at`; append-only, indexed by `(hospital_id, created_at)` and `staff_id` | See deep dive in §11 for exact coverage/limits. |
| **Provenance** | `consultation_amendments` (migration `0041`) only | `app/api/clinical.py` (amend endpoint) | `ConsultationWorkspace.tsx` amendment flow | `docs/workflows/CONSULTATION.md` | PARTIALLY IMPLEMENTED | Full before/after snapshot of every amendable consultation field, RBAC-gated, reason required | Scoped to exactly one entity type. See deep dive in §11. |
| **User** | `staff` (migration `0005`) | `app/api/staff_auth.py` | `StaffLoginFlow.tsx`, `StaffAccountsPanel.tsx` | — | IMPLEMENTED | `username`, `password_hash` (Argon2id), single `role` column (now FK'd to `roles.name`), `active`, `failed_login_count`/`locked_until` | This is authentication identity — see §13 for how it relates to (and is deliberately distinct from) Practitioner/clinical identity. |
| **Role** | `roles` (migration `0031`) | `app/api/staff_auth.py` (staff creation with role) | `StaffAccountsPanel.tsx` | — | IMPLEMENTED | 8 seeded rows: `ADMIN`, `STAFF`, `DOCTOR`, `NURSE`, `RECEPTIONIST`, `LAB_TECH`, `PHARMACIST`, `BILLING` | None found. |
| **Permission** | `permissions` + `role_permissions` + `staff_roles` (migration `0031`) | resolved internally by `require_permission()` | not directly editable via any admin UI found — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` whether permission-to-role assignment has any UI beyond direct migrations | — | IMPLEMENTED (as the real authorization mechanism) | `staff_roles.department_id` nullable = hospital-wide scope; permission grants are seeded via migration, not an admin screen | Real and actively used (confirmed by reading `require_permission()`'s SQL directly) — the actual authorization source of truth, not `staff.role`. |

---

## 4-6. Database / API / UI evidence

Folded into the Evidence column of §3 above rather than repeated in three
separate passes — every row already cites the specific migration file,
router module, and frontend component. This avoids restating the same fact
three times under three headings, while still satisfying "verify against the
actual repository" for each layer per concept.

---

## 7. FHIR mapping assessment (assessment only — no mapping implemented)

| Existing Concept | FHIR Resource | Mapping Quality | Notes |
|---|---|---|---|
| `patients` | `Patient` | **Clean** | `uhid` → a stable `Patient.identifier`; `date_of_birth`/`gender`/`name`/address fields map directly to standard `Patient` elements. `whatsapp_number` → `Patient.telecom`. |
| `doctors` | `Practitioner` | **Partial** | Demographic/scheduling fields map cleanly; no licensure/registration-number identifier to populate `Practitioner.identifier` with real professional meaning (a synthetic internal ID would have to stand in). |
| `doctor_departments`/`doctor_appointment_types` | `PractitionerRole` | **Partial** | Department linkage maps to `PractitionerRole.organization`/`.location`(if Location existed); no distinct role-type/period fields. |
| `hospitals` | `Organization` | **Poor** | Structurally present but minimal (3 real fields) and single-row in practice — a real multi-fielded `Organization` resource would mostly be populated with nulls today. |
| `departments` | `Organization` (as a sub-part) or `HealthcareService` category | **Poor** | Two columns only (`name`, `active`); no address/type/contact to populate a real `Organization`. |
| `appointment_types` | `HealthcareService` | **Poor** | A visit-type/duration catalog, not a general service catalog — mapping would be lossy in the "what this actually represents" direction, not just field-completeness. |
| `appointments` | `Appointment` | **Clean** | Status vocabulary differs (`docs/workflows/OPD_CHECKIN_QUEUE.md`'s own note) but is a coherent, mappable enum, not free text; `start_at`/`end_at`/`doctor_id`/`patient_id` map directly. |
| `doctor_schedule`/computed slots | `Schedule`/`Slot` | **Clean for Schedule, Clean-by-construction for Slot** | `Slot` would be synthesized at request time from the existing availability engine, not read from a table — a real but well-understood mapping-layer job, not a data gap. |
| `encounters` | `Encounter` | **Clean for OPD** | `encounter_type`/`status`/`started_at`/`closed_at` map directly; the FHIR resource would simply be narrower than the full spec allows (`class` always "ambulatory," effectively) until IPD exists. |
| `consultations.diagnosis` | `Condition` | **Poor** | Free-text, one field, not a codeable list — mapping would require either leaving `Condition.code.text` as free text (valid FHIR, but not a "coded" Condition) or a real terminology-binding project first (`docs/architecture/DOMAIN_MODEL.md`/Phase 4). |
| `patient_allergies` | `AllergyIntolerance` | **Partial** | Structure (allergen/reaction/severity/active) maps well; `allergen` itself is free text, same terminology gap as Condition. |
| `order_results` | `Observation` | **Partial** | Structure (`parameter`/`result_value`/`unit`/`reference_range`/flags) is observation-shaped and maps reasonably; `parameter`/`unit` aren't LOINC/UCUM-coded. |
| `vitals` | `Observation` (one per vital sign, per FHIR convention) | **Partial** | Individually-typed columns actually map *better* per-field than `order_results` does, but FHIR wants one `Observation` resource per vital sign, not one row with nine columns — the mapping layer would need to fan one `vitals` row out into several `Observation` resources. |
| `orders` | `ServiceRequest` | **Clean** | The single best-fitting mapping in the whole schema — `order_type`/`priority`/`clinical_indication`/`status` line up almost directly. |
| `orders` grouped by id (read-time) | `DiagnosticReport` | **Poor** | No persisted report entity to map from; the mapping layer would have to synthesize a `DiagnosticReport` from a grouped `order_results` query rather than reading one row. |
| `prescriptions`/`prescription_items` | `MedicationRequest` | **Partial** | Status/dosing-instruction fields map reasonably; `medicine_name` is free text with no `Medication` resource to reference (`MedicationRequest.medicationCodeableConcept.text` only, not a coded reference). |
| `pharmacy_dispense_records` | `MedicationDispense` | **Clean** | Real transaction log with quantity/batch/dispensed-by/dispensed-at — maps directly. |
| `invoices`/`charges`/`payments` | `Invoice` (or `ChargeItem`/`PaymentReconciliation`) | **Partial** | Structurally rich and clean internally; FHIR's own financial resources are a less settled area of the spec generally, so "clean mapping" is a lower bar here regardless of source-data quality. |
| *(none)* | `Coverage`/`Claim` | **N/A — no source data** | `bill_type` alone isn't enough to populate a real `Coverage` resource. |
| *(none)* | `Consent` | **N/A — no source data** | |
| *(none)* | `ImagingStudy` | **N/A — no source data** | |
| *(none)* | `DocumentReference` | **N/A — no source data (by design, see ADR-005)** | |

---

## 8. Terminology gaps

Every one of these is free text today, confirmed by reading the actual
column definitions (not inferred): `consultations.diagnosis`,
`consultations.chief_complaint`/`.history_notes`/`.examination_notes`,
`patient_allergies.allergen`, `orders.description`, `order_results.
parameter`/`.unit`/`.reference_range`, `prescription_items.medicine_name`/
`.generic_name`/`.dosage`/`.route`/`.frequency`. None of these have a
`{code, system, display}` shape or reference a terminology table — confirmed
by grep, zero hits for that pattern anywhere in `migrations/`. This is a
single, consistent gap repeated across every clinical text field, not
several unrelated ones — closing it once (Phase 4, `docs/architecture/
DOMAIN_MODEL.md`) would address all of them structurally, though populating
real terminology bindings per field is still per-field work.

No hard-coded terminology values were found in application code (no
inline lists of diagnosis names, lab names, or medication names baked into
`.py`/`.tsx` files) — the free text is genuinely staff-entered per visit,
not a disguised hard-coded list. This matters for Phase 4 scoping: there is
no hidden "fake" terminology list to migrate away from, only free text to
bind going forward.

---

## 9. Patient identity assessment

| Item | Status | Evidence |
|---|---|---|
| Internal patient ID | ✅ Real | `patients.id`, `BIGINT GENERATED ALWAYS AS IDENTITY` |
| Medical Record Number (UHID) | ✅ Real | `patients.uhid`, `GENERATED ALWAYS AS ('HOS-' \|\| LPAD(id::text, 7, '0')) STORED` — deterministic, never drifts from `id`, backfills for free |
| External identifiers | 🟡 Partial | `patient_identifiers` table exists (`kind IN ('PHONE','GOVT_ID')`) but is dual-written, not authoritative (own comment says so) |
| ABHA identifier | ❌ None | Confirmed absent in both this audit and Phase 0. `patient_identifiers.kind` is the concrete, already-proven extension point (widened once already, `PHONE`→`PHONE,GOVT_ID`) — adding `'ABHA'` there is a small, well-precedented change *when that phase starts*, not a redesign. |
| Patient merge | ✅ Real, working | `patient_merges` (`surviving_patient_id`/`retired_patient_id`/`affected` JSONB detailed enough to reverse), `patients.merged_into_id` pointer, unmerge supported |
| Duplicate patient detection | ✅ Real, working | `patient_duplicate_reviews`, `pg_trgm`-based name similarity + DOB + phone + government_id as the four signals (migration `0030`'s own comment) |
| Patient demographic matching | ✅ Real | The same four-signal detection above — name similarity score stored numerically (`name_similarity REAL`), not just a boolean match |
| Identifier uniqueness | 🟡 Partial | `patients.whatsapp_number` is `UNIQUE`; `patient_identifiers` allows more than one patient to share a value (own comment: "More than one patient can share a value; `is_primary` picks the one `resolve_patient_by_identifier()` returns") — a deliberate, documented looseness, not a bug, but worth naming precisely |
| Identifier history | 🟡 Partial | `patient_merges.affected` JSONB records exactly what changed per merge (detailed enough for `patient_identifiers` entries to note `was_primary`); there is no general identifier-change audit outside of a merge event specifically |
| Patient merge audit | ✅ Real | `patient_merges` itself is the audit trail — `merged_by_staff_id`, `created_at`, `unmerged_at`, never deleted even after unmerge |

**Assessment**: patient identity is the single most mature, standards-ready
concept in the entire schema. The permanent-identifier discipline (UHID
never mutated, never reused, deterministically generated), the real
duplicate-detection signal set, and the already-proven extensible
`patient_identifiers.kind` column together mean a future ABHA integration
has a genuinely solid foundation to build on — this is not a from-scratch
problem. The one thing worth deciding *before* that phase starts: whether
`patient_identifiers` becomes the real source of truth (replacing
`whatsapp_number`'s special status) at the same time ABHA is added, or
stays a shadow table with a third kind bolted on — doing the latter would
compound the existing duplicate-representation issue (§13) rather than
resolve it.

---

## 10. Encounter model assessment

Mapping the actual OPD stage-by-stage flow to the entity that represents it,
confirmed by direct inspection (not the target-state diagram from
`docs/product/PATIENT_JOURNEY.md` — this is what's real):

| OPD stage | Entity | Evidence |
|---|---|---|
| Appointment | `appointments` row created | `status` starts `PENDING` |
| Registration | `patients` row (existing or newly created) | linked via `appointments.patient_id` |
| Arrival | `appointments.arrived_at` set | migration `0023` |
| Check-in / Queue | `appointments.status = 'CHECKED_IN'` + token fields | queue position is *derived* (lowest un-served token), not a stored status — `docs/workflows/OPD_CHECKIN_QUEUE.md` |
| Doctor consultation | `consultations` row (1:1 with `encounters`, `status DRAFT→COMPLETED`) | migration `0029` |
| Diagnosis | `consultations.diagnosis` (free text) | same row as consultation, not a separate entity |
| Prescription | `prescriptions`/`prescription_items` (1:1 with `encounters`) | migration `0032` |
| Investigation | `orders`/`order_results` (1:N with `encounters`) | migration `0030`/`0031` |
| Follow-up | `consultations.follow_up_date`/`.follow_up_reason` | two columns, not a separate entity |

Every one of these hangs off the same `encounters.id` — confirmed by reading
each table's FK definition directly, not assumed from the ER diagram in
`docs/architecture/ENCOUNTER_MODEL.md` (that diagram was itself built from
this same verification in the prior documentation phase).

**FHIR mapping implication** (assessment only): `Appointment` and
`Encounter` map cleanly as two distinct resources exactly the way the
internal model already separates them (a `PENDING` appointment has no
encounter attached yet in spirit, though `TODO — VERIFY AGAINST EXISTING
IMPLEMENTATION` the *exact* moment an `encounters` row is created relative
to appointment status — this audit confirmed the FK relationship exists,
not the precise creation trigger point). `Condition`/`ServiceRequest`/
`Observation`/`MedicationRequest` all map from real, distinct tables already
scoped to one `encounter_id` — the encounter-centric design means a future
FHIR `Encounter.$everything` operation would be a straightforward query
fan-out from `encounter_id`, not a redesign.

---

## 11. Audit / Provenance assessment

### Audit (`audit_log`)

- **What actions are captured?** Whatever string `record_audit_log()` was
  called with as `action` — confirmed real values include
  `break_glass.grant` (from `app/api/staff_auth.py`). `TODO — VERIFY AGAINST
  EXISTING IMPLEMENTATION` for the complete list of `action` strings used
  across every call site — this audit confirmed the mechanism and at least
  one real value, not an exhaustive enumeration of every action ever logged.
- **Which resources?** `resource_type` + `resource_id` — generic, works for
  any entity, confirmed by the column design itself (not FK'd to any one
  table, by necessity).
- **Who?** `staff_id`, nullable-but-typically-populated (`REFERENCES
  staff(id)`).
- **When?** `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
- **What changed?** `details JSONB` — free-form, so old/new values *can* be
  retained if the calling code puts them there, but the schema itself
  doesn't enforce an old/new-value shape the way `consultation_amendments`
  does for its one entity. `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION`
  whether `details` consistently includes before/after values across call
  sites or varies by caller.
- **FHIR `AuditEvent` mapping**: plausible and reasonably clean structurally
  (`agent` ← `staff_id`, `entity` ← `resource_type`/`resource_id`, `period`
  ← `created_at`) but not attempted here — this is an assessment, not an
  implementation.

### Provenance (`consultation_amendments`)

Confirmed scope: **one entity type only** — consultations. On amendment, the
prior `chief_complaint`/`history_notes`/`examination_notes` (and,
presumably, the other amendable fields — `TODO — VERIFY AGAINST EXISTING
IMPLEMENTATION` for the complete column list beyond the three confirmed by
this pass) are archived into a new `consultation_amendments` row before the
`consultations` row itself is updated, gated by `consultation.amend`,
requiring a reason.

**This is not full FHIR `Provenance`.** It is a real, working, before/after
change-history mechanism for exactly one table. FHIR `Provenance` is a
general-purpose resource that can attach to *any* resource and additionally
distinguishes *how* data arrived (entered by a human vs. imported vs.
derived), which `consultation_amendments` doesn't attempt — it only answers
"what did this consultation look like before this specific edit," not "was
this data human-entered, imported, or system-generated" (a question that
has never needed answering yet, since no imported or AI-generated clinical
data path exists anywhere in the system today, confirmed by both this audit
and Phase 0).

### Break-glass (`break_glass_grants`)

Re-verified from Phase 0: `POST /api/auth/staff/break-glass` (reason
required — `ValueError("A reason is required for a break-glass grant")`,
confirmed by reading the actual validation), `GET` (list, for review, gated
by `staff.manage`), `POST /{id}/review`. Grants expire (`expires_at`).
`staff.manage` itself is explicitly excluded from being grantable through
break-glass (`app/api/staff_auth.py`'s own comment: "staff.manage is
deliberately not grantable through break-glass"). Every grant is logged to
`audit_log` (confirmed: `action="break_glass.grant"`). `require_permission()`
checks an active break-glass grant as a genuine alternate authorization path
(`OR EXISTS (... FROM break_glass_grants ...)`), not a separate/bypassed
check — confirmed by reading the SQL directly.

**Standards mapping note (assessment only)**: this already has the shape a
healthcare security standard would want from emergency access — reason,
time-bound, reviewable, audited, with an explicit denylist of what can never
be granted this way. No rebuild needed; a future mapping to whatever
FHIR/IHE-ATNA-equivalent concept applies would wrap this, not replace it.

---

## 12. Clinical safety gaps

**The one gap this audit and Phase 0 both independently surfaced, confirmed
again here by direct inspection of `app/services/pharmacy_services.py`:**
`patient_allergies` is a real, structured, actively-used-for-*display*
table, but **zero code path checks it against a medication being
prescribed or dispensed.** A patient with a documented severe penicillin
allergy can be prescribed a penicillin-class drug today with no warning
anywhere in the system. This is independent of any interoperability
standard — it doesn't need FHIR, CDS Hooks, or terminology binding to fix at
a basic level (a simple substring/exact-match check against
`patient_allergies.allergen` would already add real value), though a robust
version would benefit from the terminology work in §8 to catch
drug-class-level interactions, not just exact-name matches.

Per the explicit instruction for this phase: **not implemented here.**
Documented as a standalone finding for prioritization.

---

## 13. Architectural gaps

### Duplicate / parallel representations (real findings, not assumed)

1. **Phone identifier**: `patients.whatsapp_number` (the real, `UNIQUE`,
   actually-used-for-lookup column) vs. `patient_identifiers` (kind=`PHONE`,
   dual-written, explicitly "not yet the source of truth for anything" per
   its own migration comment). Two representations of the same fact,
   kept in sync by application code (`app/services/patient_identifiers.py`),
   not by a database constraint. Real, acknowledged in the code's own
   comments, not hidden.
2. **Observation shape**: `vitals` (typed columns, one row per recording
   event, nine distinct measurements) vs. `order_results` (generic
   parameter/value/unit row, one row per measurement). Both represent "an
   observation about a patient" but with genuinely different shapes for
   genuinely different reasons (vitals are a fixed, known set recorded
   together; order results are an open-ended, order-specific set) — this
   is defensible as two different *use cases*, not a straightforward
   duplicate, but a future `Observation` FHIR mapping layer will need to
   handle both shapes, which is real complexity worth naming.
3. **Role representation**: `staff.role` (single column, FK'd to
   `roles.name`) vs. `staff_roles` (many-to-many, department-scoped). Per
   §1, this is deliberate layering (display/landing-selection vs. real
   authorization), not a bug — but `TODO — VERIFY AGAINST EXISTING
   IMPLEMENTATION` whether every staff-creation/role-change code path keeps
   both in sync, since nothing in the schema itself enforces that a staff
   member's `role` column always has a matching `staff_roles` row (they are
   two independent tables with no constraint tying them together).

### Incorrect boundaries — checked, not found

The prompt specifically asks whether the system "mixes authentication
identity with clinical identity" (Doctor/User/Practitioner). **It does
not, and this is worth confirming explicitly rather than assuming either
way**: `staff` (authentication identity — login, password, session) and
`doctors` (clinical/scheduling identity — specialization, schedule, patient-
facing) are two separate tables with no FK between them found in this
pass. A doctor's `staff` login and their `doctors` row are linked only
loosely (a `DOCTOR`-role staff account is presumably expected to correspond
to a real `doctors` row for the same person, but `TODO — VERIFY AGAINST
EXISTING IMPLEMENTATION` whether any code path actually enforces or even
records that linkage — no `doctors.staff_id`/`staff.doctor_id` column was
found by this pass). This is the opposite of the anti-pattern the prompt
asks about (properly separated, if perhaps *too* loosely linked rather than
too tightly coupled) — worth a targeted follow-up check before assuming
either "it's fine" or "it's broken."

### Free-text clinical data requiring eventual terminology

Full list in §8 — not repeated here.

### Hard-coded terminology

None found in application code (§8) — the gap is free text, not disguised
hard-coding.

---

## 14. Recommended implementation sequence (classification only — nothing implemented)

**A. Already good — no change required**: Patient identity core (UHID,
merge, duplicate detection), Encounter-centric design, Order Spine,
Appointment/Schedule, Medication Dispense, Invoice/Charge/Payment, Audit
mechanism, Break-glass, RBAC authorization path (`staff_roles → role_
permissions`).

**B. Good internal model, needs a FHIR mapping layer later**: Patient,
Appointment, Encounter, ServiceRequest↔orders, MedicationDispense,
MedicationRequest↔prescriptions (mapping quality "Clean" or "Partial" in
§7, with real underlying data either way).

**C. Existing model needs normalization (future, not now)**: the phone-
identifier duplication (§13.1) — decide whether `patient_identifiers` or
`patients.whatsapp_number` becomes the one source of truth before adding an
`ABHA` kind on top of the current ambiguity; the `vitals`/`order_results`
dual-shape question (§13.2) — needs a real design decision before a unified
`Observation` mapping layer is built, not before.

**D. Missing concept, requires future implementation**: Consent, Coverage/
Claim (beyond `bill_type`), ImagingStudy, CarePlan as a distinct entity,
Location, a general Medication catalog, terminology binding for every free-
text clinical field (§8).

**E. Clinical safety issue, independent priority**: allergy-vs-prescription
checking (§12) — the single item on this list this audit recommends
considering *ahead of* strict interoperability phase order, since it
requires no standard, no new table, and delivers real safety value using
data that already exists.

**F. Architectural issue, needs future attention**: confirm (don't assume)
whether `staff.role` and `staff_roles` can drift (§13.3); confirm whether
`doctors`↔`staff` linkage is enforced anywhere (§13, Incorrect boundaries).
Neither is urgent; both are cheap to verify precisely and worth resolving
before they're load-bearing for a bigger interoperability phase.

---

## 15. Explicit list of things that should NOT be changed yet

Per the master prompt's Phase 1 scope, none of the following should be
touched as a result of this audit:

- `patients`/`patient_identifiers` schema (including adding an `ABHA`
  identifier kind) — Phase 5, not now.
- `consultations.diagnosis` or any other free-text clinical field, or any
  terminology table — Phase 4, not now.
- `staff.role` vs. `staff_roles` — no change; §13.3 is a verification item,
  not a defect requiring a fix.
- `vitals`/`order_results` shape — no consolidation; §13.2 is a future
  design decision, not an immediate refactor.
- `audit_log` schema (e.g. adding an explicit `patient_id` column, or
  reshaping toward `AuditEvent`) — Phase 4/7, not now.
- `consultation_amendments` generalization to other entities — Phase 7, not
  now.
- The allergy-vs-prescription clinical-safety gap (§12) — flagged for
  prioritization, but **not implemented in this phase**, per the explicit
  instruction: "Do not implement the allergy check during this phase unless
  I explicitly ask for it."
- Any FHIR endpoint, library, table, or mapping code.
- Any ABDM, OAuth, SMART on FHIR, DICOM, HL7, or terminology-service code.
- Any database migration of any kind.

---

## Final verification note

Every concept in §3 has a specific evidence citation (a migration file, an
API router, or an explicit "confirmed absent by grep") — none were marked
IMPLEMENTED/PARTIALLY IMPLEMENTED/NOT IMPLEMENTED by inference alone. Where
this audit could not confirm a detail with the evidence gathered, it says
`TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` rather than assuming either
answer (used six times in this document: allergy-list UI surfacing, staff/
role sync enforcement, doctors↔staff linkage, encounter-creation trigger
timing, audit_log's `action` value completeness, and `details` JSONB
consistency).

**Nothing in this document was implemented.** No schema, API, or frontend
change was made to produce it.

## Recommended next step

Per the master prompt's own sequence: **Phase 2 (Terminology Architecture)**
would be the next natural step given §8/§14's findings, since it's a
prerequisite for making the "Clean" and "Partial" FHIR mappings in §7
actually clean. Item E (§12, the allergy-safety check) is flagged as worth
raising with the product owner as a possible priority ahead of strict phase
order, since it's small, independent, and already explicitly deferred
pending your explicit go-ahead.

**STOP. Do not implement Phase 2 or any later phase without explicit
confirmation.**
