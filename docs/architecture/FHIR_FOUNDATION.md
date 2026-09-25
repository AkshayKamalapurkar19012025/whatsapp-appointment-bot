# FHIR Foundation

## Purpose

Establish a minimal, read-only FHIR R4 interoperability layer over this application's existing HIMS domain model — an external representation for a small, verified set of resources, not a redesign of the internal database, and not the internal source of truth. Built per the OPD/HIMS interoperability master prompt's Phase 8.

```
Existing HIMS Domain Model
        │
        ▼
FHIR Mapping Layer (app/services/fhir_mappers.py)
        │
        ▼
FHIR R4 Resources (GET /fhir/r4/{Resource}/{id})
```

The internal HIMS schema (`patients`, `consultations`, `orders`, `order_results`, …) is **unchanged by this phase** — no table was redesigned, forked, or shadow-copied to make resource generation easier. Every FHIR resource is computed on read, from the same rows every existing `/api/...` endpoint already reads, via a pure mapping function.

## FHIR Version

**FHIR R4.** No R5, no version-abstraction layer. Chosen because R4 is the current widely-deployed version most real-world interoperability partners (including India's ABDM ecosystem, a later phase's concern) expect; nothing in this repository suggested a different target.

## Architecture

- **`app/services/fhir_mappers.py`** — pure functions, one per resource type, each taking an already-fetched dict (the caller's own SQL query result, tenant-checked) and returning a FHIR resource dict. No database access happens in this module.
- **`app/api/fhir.py`** — the router. Each `GET /fhir/r4/{Resource}/{id}` endpoint: authenticates via the existing staff session (`Depends(get_current_staff)`), runs a tenant-scoped SQL query (`... AND hospital_id = %s`, or via a join to `encounters`/`patients` for tables that don't carry `hospital_id` directly — the same derivation `tests/test_hospital_tenant_coverage.py`'s `EXEMPT_TABLES` already documents for those tables), calls the matching mapper, and returns the result.
- Mounted at **`/fhir/r4/...`**, deliberately with **no `/api` prefix** — isolated from the application's own internal REST API (`app/main.py`'s `include_router(fhir_router)`, no `prefix="/api"`). An external FHIR client hits this router directly; the admin SPA never does, and never needs to.

## Resource Mapping Table

| Internal concept | FHIR R4 resource | Readiness | Missing information | Phase 8 action |
|---|---|---|---|---|
| `patients` | Patient | High | ABHA identifier (absent — not invented) | **Implemented** |
| `doctors` | Practitioner | Medium | License/registration number, structured qualification, contact info | **Implemented** (partial) |
| `hospitals` | Organization | High (single-tenant) | Address/contact columns don't exist | **Implemented** (minimal) |
| `appointments` | Appointment | High | No structured reason field | **Implemented** |
| `encounters` | Encounter | High | Single care setting (`encounter_type = 'OPD'` only) | **Implemented** |
| `consultations.diagnosis`(+code fields) | Condition | Medium | Code usually absent (no terminology source yet); clinicalStatus not tracked | **Implemented** (code absent unless populated) |
| `patient_allergies` | AllergyIntolerance | High | No coded allergen; no separate onset date | **Implemented** |
| `medications` | Medication | High | No RxNorm/ingredient coding | **Implemented** |
| `prescriptions`+`prescription_items` | MedicationRequest | Medium-High | Dosage/frequency/duration are free text, not structured Timing | **Implemented** |
| `order_results` | Observation | Medium | No LOINC; unit code usually absent (Phase 7 slot) | **Implemented** |
| `vitals` | Observation (×N fan-out) | Data present, mapping not built | Would need synthetic composite ids, 1 row → up to 9 resources | **NOT implemented** |
| `orders` | ServiceRequest | High | No coded category/test code | **Implemented** |

11 of 12 candidate mappings are implemented. Only vitals→Observation is deliberately deferred — see "Explicitly Not Implemented" below.

## Implemented Resources — mapping notes

Each subsection names exactly what's real, what's absent (never invented), and any lossy translation.

### Patient
`Patient.id` = internal `patients.id`. `Patient.identifier` carries the UHID (this application's real permanent identity, per `docs/decisions/ADR-001-PATIENT-IDENTITY.md`) and `government_id` when present. `Patient.active` is derived from `merged_into_id IS NULL` (a merged-away record is genuinely no longer the patient's live record — a legitimate derivation, not a fabrication). `Patient.name` is `HumanName.text` only — the internal `name` column is one free-text field with no given/family split, and inventing one by parsing the string would misrepresent names that don't split cleanly. `Patient.link` (pointing a merged record at its survivor) is **not implemented**. **ABHA is absent** — no field for it exists internally, and none is invented here.

### Practitioner
Sourced from `doctors`, not `staff` — see "Staff vs. Practitioner" below for why that distinction matters throughout this layer. `qualification[].code.text` carries the free-text `qualifications` column (CodeableConcept's documented free-text fallback). No `identifier` (no license/registration number column exists). **`specialization` is deliberately not mapped** — in real FHIR that's a `PractitionerRole` concept, not `Practitioner` itself, and `PractitionerRole` is not implemented this phase.

### Organization
One row exists today (`hospitals.id = 1`) since this deployment is single-tenant. `Organization.identifier` carries `hospitals.code`. No address/telecom (no such columns exist).

### Appointment
Status mapping is mostly exact — FHIR's own value set happens to include `checked-in`, a direct match for this app's own `CHECKED_IN` status:

| Internal | FHIR |
|---|---|
| PENDING | pending |
| CONFIRMED | booked |
| CHECKED_IN | checked-in |
| COMPLETED | fulfilled |
| CANCELLED | cancelled |
| REJECTED | cancelled *(lossy — FHIR has no distinct "rejected" value)* |

`participant[].status` is hardcoded to `"accepted"` for both patient and practitioner — this application has no per-participant accept/decline concept once an appointment exists, so this is the closest honest default, not a tracked fact.

### Encounter
`Encounter.class` maps the internal `encounter_type = 'OPD'` to the standard HL7 v3 ActEncounterCode `AMB` (ambulatory) — a structural/administrative translation of an internal enum to its established external representation, the same kind of "pure translation table" `docs/OPD_HIMS_STANDARDS_READINESS.md` §10 already endorsed for clinical statuses, not a clinical coding decision. `Encounter.appointment` resolves the one appointment whose `encounter_id` points at this encounter (assumed 1:1, the same assumption `app/services/order_services.py`'s worklist query already relies on elsewhere in this codebase).

### Condition
Sourced from `consultations` (one per encounter — Phase 6 re-confirmed no multi-diagnosis model exists or is needed). **Returns no resource (404) when `diagnosis` is `NULL`** — a DRAFT consultation with nothing documented has nothing to represent. `code.coding` is only present when `diagnosis_code` is set (still rare — no terminology source exists yet); `Coding.system` passes through whatever free text `diagnosis_code_system` holds (e.g. "ICD-10") as-is, which is **not a real URI** — this is flagged explicitly as a limitation, not silently presented as conformant. `clinicalStatus`/`verificationStatus` are omitted entirely: this schema doesn't track a diagnosis's own resolution state separately from the consultation's DRAFT/COMPLETED documentation status, and inferring one from the other would assert clinical meaning that isn't actually tracked.

### AllergyIntolerance
`clinicalStatus` is a direct, evidenced mapping from `patient_allergies.active` (`true`→`active`, `false`→`resolved`) — the internal model already tracks exactly this. `reaction[].severity` is a direct, exact-vocabulary match (`MILD`/`MODERATE`/`SEVERE` → `mild`/`moderate`/`severe`, lowercased). `recordedDate` (not `onsetDateTime`) uses `recorded_at`, since that column means "when this was documented," not a separately-tracked clinical onset — the two are genuinely different concepts and conflating them would misrepresent the data. **No `recorder`/`asserter`** — `recorded_by` is a `staff_id`; see "Staff vs. Practitioner" below. **The Phase 4 allergy-to-medication safety check is completely unaffected by this phase** — FHIR exposure here is read-only and downstream of that check, never upstream of it.

### Medication
Sourced from Phase 5's Medication Master (`medications`), reusing that module's own `display_name` computation (`app/services/medication_services.py`) for `code.text` rather than re-deriving the same formatting a second time. No RxNorm/ingredient coding (Phase 5's own deliberate scope limit, unchanged). `strength` (free text, e.g. "500mg") is folded into the display text rather than forced into `Medication.ingredient[].strength`, which real FHIR expects as a structured `Ratio` this data cannot honestly supply.

### MedicationRequest
One resource per `prescription_items` row. Uses **`medicationReference`** when Phase 5's `medication_id` link is set, and **`medicationCodeableConcept`** (free text) otherwise — exactly mirroring the optional-link semantics Phase 5 built into prescribing itself. `status` is derived: `DRAFT`→`draft`, `CANCELLED`→`cancelled`, `PRESCRIBED`→`completed` once `quantity_dispensed >= quantity` else `active` (a direct derivation from already-present fields, not a guess). `dosageInstruction[].text` joins the free-text dosage/frequency/duration/food-instructions fields — **never parsed into a structured FHIR `Timing`** (e.g. turning "1-0-1" into a real dosing schedule would be a guess about clinical intent this module refuses to make).

### Observation (from `order_results` only)
`status` is always `"final"` — a result only exists once its order reaches `COMPLETED`, in the same transaction (`app/services/order_services.py`), and there is no amendment/correction workflow (confirmed in Phase 7) that would ever produce `amended`/`corrected`. **Value typing is a syntactic transformation, not a clinical one**: if `result_value` parses as a number, it becomes `valueQuantity` (carrying `unit`/`unit_system`/`unit_code` when present, from Phase 7's own coding slot); otherwise it stays `valueString`. `interpretation` is included only when `is_abnormal`/`is_critical` is actually `true` — never asserted as `"Normal"` when both are false, matching the existing result table/UI's own restraint (it shows nothing, not a "Normal" label, when neither flag is set). No LOINC (still absent, confirmed in Phase 7).

### ServiceRequest
One resource per `orders` row. `category` carries `order_type` (LAB/RADIOLOGY/PROCEDURE/SERVICE/EXTERNAL_REFERRAL) as free text, not a coded system. `authoredOn` (not `occurrenceDateTime`) uses `ordered_at`, since this schema doesn't separately track a planned/scheduled service time.

## Staff vs. Practitioner

A recurring, easy-to-get-wrong distinction handled carefully throughout this layer: `staff` (ADMIN/STAFF/DOCTOR/NURSE/RECEPTIONIST/LAB_TECH/PHARMACIST/BILLING login accounts) and `doctors` (the actual clinical-provider entity) are two **different tables** in this application's own domain model. Only columns that are genuinely a `doctor_id` (`appointments.doctor_id`, `consultations.doctor_id`, `orders.ordering_doctor_id`, `prescriptions.doctor_id`) become a `Practitioner` reference. Columns that are a `staff_id` (`patient_allergies.recorded_by`, `order_results.recorded_by`, `vitals.recorded_by`, `consultations.created_by`) are **never** mapped to `Practitioner` — a nurse or receptionist recording an allergy is not a clinician, and representing them as one would misrepresent who did what.

## Explicitly NOT Implemented

- **Vitals → Observation.** `vitals` is data-complete (every measurement, already typed) but structurally different from `order_results`: one `vitals` row holds up to nine distinct measurements, so a faithful mapping needs a 1-row-to-N-resources fan-out with synthetic composite identifiers (e.g. `vitals-{id}-bp_systolic`) — real design work `docs/OPD_HIMS_STANDARDS_READINESS.md` §9 itself already flagged as "a mapping-layer job" for a dedicated pass, not something to rush inside this foundation phase. Deferred, not abandoned.
- **`PractitionerRole`** (doctor specialization, department affiliation).
- **LOINC** on `order_results.parameter`/`orders.description` — no test/parameter catalog exists internally to anchor a code to (`docs/workflows/LABORATORY.md`'s own Phase 7 finding, re-confirmed here); coding per free-text instance without a catalog would let the same real-world test get inconsistently coded across orders.
- **RxNorm / any external medication terminology.**
- **DiagnosticReport, `Bundle`/patient-summary, FHIR search beyond `GET /Resource/{id}`.** Only single-resource reads are implemented; no query parameters, no `_include`, no pagination.
- **Write operations** (`POST`/`PUT`/`PATCH`/`DELETE`) — this is a read-only layer, full stop. No external system can create or modify a clinical record through FHIR in this phase.
- **`AuditEvent`, `Consent`, `Subscription`, CDS Hooks.**
- **SMART on FHIR, OAuth2/OIDC.** See "Authentication" below.
- **ABDM profiles.** Base R4 resources only — no custom profile, no ABDM-specific extension or identifier system.

## Identifiers & References

`Resource.id` is this application's own internal integer primary key, stringified (`str(patients.id)`, etc.) — the **same exposure level** every existing `/api/...` endpoint already uses for `patient_id`/`appointment_id`/etc. in its own URLs, behind the same staff-session authentication and `hospital_id` tenant check. FHIR introduces no new identifier scheme, no UUIDs, no public/opaque-id layer. The clinically meaningful identifier (UHID) is carried separately, in `Patient.identifier`, matching FHIR's own distinction between a resource's technical `id` and its clinical `identifier`.

References between resources (`Patient/{id}`, `Encounter/{id}`, `Practitioner/{id}`, `Medication/{id}`) are always constructed from a real, already-fetched internal foreign key. A reference is **never fabricated** — if the related row doesn't exist or the field is `NULL`, the reference is simply absent from the resource.

## Tenant Isolation

Every query filters by `hospital_id`, exactly matching the convention `app/api/patients.py`'s `get_patient` (and every other single-record `GET` in this codebase) already established: `WHERE id = %s AND hospital_id = %s` (or, for tables that don't carry `hospital_id` directly — `consultations`, `patient_allergies`, `prescription_items`, `orders`, `order_results` — a join through `encounters`/`patients`, the same derivation `tests/test_hospital_tenant_coverage.py`'s `EXEMPT_TABLES` documents for those tables elsewhere in this app). A resource that doesn't exist and a resource that exists in a **different** hospital both return the identical 404 — never a 403, which would confirm the record exists somewhere. Verified in `tests/test_fhir.py::test_all_fhir_endpoints_isolate_by_tenant` against a real second hospital, not just asserted.

## Authentication

**FHIR authentication = existing HIMS authentication.** Every endpoint depends on the same `get_current_staff` (bearer session token) every other `/api/...` endpoint already uses — no second auth mechanism, no API key scheme, no service account model. `SMART on FHIR` and `OAuth2/OIDC` are explicitly future interoperability-phase work, not built or simulated here.

## Read-Only Scope

Only `GET /fhir/r4/{Resource}/{id}` exists. No create/update/delete route. This is deliberate: an interoperability boundary that only reads never becomes a second, uncontrolled write path into clinical data alongside the application's own existing, carefully RBAC-gated write endpoints.

## Error Handling

Resource-level errors (not found; exists in a different hospital) return a FHIR `OperationOutcome` body, constructed and returned directly as a `JSONResponse` — bypassing `app/error_handling.py`'s global exception handler, which wraps ordinary `HTTPException`s in this application's own `{success, errorCode, message, details}` envelope. Authentication failures (401) are raised by the shared `get_current_staff` dependency itself, before any FHIR endpoint code runs, and keep that dependency's existing response shape — per "FHIR authentication = existing HIMS authentication" above, its error format is inherited too, not re-wrapped.

## Content Type

Every successful and `OperationOutcome` response is returned with `media_type="application/fhir+json"`, distinct from the plain `application/json` every `/api/...` endpoint uses.

## Resource Validation

No FHIR library dependency was added (`requirements.txt` gains nothing from this phase) — evaluated and rejected for this minimal foundation: a full FHIR resource library (e.g. `fhir.resources`) is a large, version-pinned dependency, and this application has no existing precedent for schema-validation libraries beyond Pydantic (used here only for the framework's own request/response typing, not FHIR-specific validation). Structural correctness is instead enforced directly in `app/services/fhir_mappers.py`: every resource always carries `resourceType` and `id`; references are always `{"reference": "ResourceType/id"}`; dates are always ISO 8601 (`.isoformat()` on real `datetime`/`date` values, never a hand-built string); status/coding fields are drawn from fixed, reviewed translation dicts, never free-typed at the call site. This is **not a claim of full FHIR conformance** — no resource here has been validated against the actual FHIR R4 StructureDefinitions, and `docs/architecture/FHIR_FOUNDATION.md` (this file) makes no such claim anywhere.

## Audit

**FHIR reads are not audit-logged in Phase 8.** `app/services/audit_log.py`'s `record_audit_log` is used exclusively for RBAC-gated *write* actions everywhere else in this codebase (confirmed by inspection — every one of its ~20 existing call sites is a mutation); no `GET` endpoint anywhere in this application, FHIR or otherwise, is currently audited. Adding read-auditing only for FHIR would be a first-of-its-kind departure from that convention, done without a concrete driving requirement. This is a decision to revisit, not an oversight — if/when FHIR access needs stronger accountability than the rest of this API currently has, that's real, separately-scoped future work, not invented here.

## FHIR Compliance Claim

**FHIR R4 read-only resource mapping implemented for:** Patient, Practitioner, Organization, Appointment, Encounter, Condition, AllergyIntolerance, Medication, MedicationRequest, Observation (from `order_results`), ServiceRequest.

This is **not** a claim of "FHIR compliant," "FHIR certified," "ABDM compliant," or "SMART on FHIR" — none of those are true of this phase, and none is claimed. It is a claim about exactly the list above: a working, tenant-isolated, read-only mapping from real internal data to FHIR R4 JSON shapes, verified by `tests/test_fhir.py` and a live end-to-end patient journey (see the Phase 8 verification report for the exact run).

## Future Work

- ABDM FHIR profiles (a distinct future phase, per the master prompt series).
- SMART on FHIR / OAuth2/OIDC authentication.
- Write support (`POST`/`PUT` for external systems to create/update records) — a materially larger trust and validation problem than this phase's read-only scope.
- FHIR Subscriptions, CDS Hooks, `AuditEvent`, `Consent`.
- Vitals → Observation fan-out.
- `PractitionerRole` (specialization, department affiliation).
- A real FHIR search API (`GET /Patient?identifier=...`, `_include`, pagination) beyond single-resource `GET /Resource/{id}`.
- A `Bundle`-based patient summary, once the above resources are individually stable and a concrete consuming use case exists.
