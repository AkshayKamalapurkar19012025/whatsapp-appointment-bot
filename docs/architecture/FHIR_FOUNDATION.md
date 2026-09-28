# FHIR Foundation

## Purpose

Establish a minimal, read-only FHIR R4 interoperability layer over this application's existing HIMS domain model — an external representation for a small, verified set of resources, not a redesign of the internal database, and not the internal source of truth. Built per the OPD/HIMS interoperability master prompt's Phase 8, hardened in Phase 9 (FHIR Foundation Hardening & ABDM Readiness — see `docs/interoperability/ABDM_READINESS_AUDIT.md` for the ABDM-specific assessment produced alongside this hardening pass).

**Phase 9 changed this layer in five ways**, each covered in its own section below: `meta.lastUpdated` on every resource with a real `updated_at` column; a source-prefixed Observation `id` (`or-`/`vt-`) now that vitals is a second Observation source; vitals → Observation mapping (the "vital signs panel" pattern); a minimal `PractitionerRole`; and a minimal FHIR search (`?patient=`, `?identifier=`) plus the standard `$everything` operation as this layer's Bundle/patient-summary answer. Nothing from Phase 8 was removed or renamed except the Observation `id` format, called out explicitly under "Breaking Change" below.

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

| Internal concept | FHIR R4 resource | Readiness | Missing information | Status |
|---|---|---|---|---|
| `patients` | Patient | High | ABHA identifier (absent — not invented) | **Implemented** |
| `doctors` | Practitioner | Medium | License/registration number, structured qualification, contact info | **Implemented** (partial) |
| `doctors` | PractitionerRole | Low-Medium | No coded specialty; no Location | **Implemented** (minimal — Phase 9) |
| `hospitals` | Organization | High (single-tenant) | Address/contact columns don't exist; no real hierarchy | **Implemented** (minimal) |
| `appointments` | Appointment | High | No structured reason field | **Implemented** |
| `encounters` | Encounter | High | Single care setting (`encounter_type = 'OPD'` only) | **Implemented** |
| `consultations.diagnosis`(+code fields) | Condition | Medium | Code usually absent (no terminology source yet); clinicalStatus not tracked | **Implemented** (code absent unless populated) |
| `patient_allergies` | AllergyIntolerance | High | No coded allergen; no separate onset date | **Implemented** |
| `medications` | Medication | High | No RxNorm/ingredient coding | **Implemented** |
| `prescriptions`+`prescription_items` | MedicationRequest | Medium-High | Dosage/frequency/duration are free text, not structured Timing | **Implemented** |
| `order_results` | Observation (`or-{id}`) | Medium | No LOINC; unit code usually absent (Phase 7 slot) | **Implemented** |
| `vitals` | Observation (`vt-{id}`, panel w/ `component[]`) | Medium | No LOINC/UCUM; composite vitals only, not per-field resources | **Implemented — Phase 9** |
| `orders` | ServiceRequest | High | No coded category/test code | **Implemented** |
| n/a | Bundle (`searchset`, `?patient=`/`?identifier=`) | — | No real pagination (hard cap, `_SEARCH_LIMIT = 50`) | **Implemented — Phase 9** |
| `patients` + everything referencing it | Bundle (`collection`, `Patient/{id}/$everything`) | — | Same per-category cap as search | **Implemented — Phase 9** |
| n/a | Location | — | No room/ward/address data exists anywhere in this schema | **NOT implemented** (see "Location" below) |

14 of 15 candidate resource/operation mappings are implemented as of Phase 9. Only `Location` is deliberately not built — see "Explicitly NOT Implemented" below.

## Implemented Resources — mapping notes

Each subsection names exactly what's real, what's absent (never invented), and any lossy translation.

### Patient
`Patient.id` = internal `patients.id`. `Patient.identifier` carries the UHID (this application's real permanent identity, per `docs/decisions/ADR-001-PATIENT-IDENTITY.md`) and `government_id` when present. `Patient.active` is derived from `merged_into_id IS NULL` (a merged-away record is genuinely no longer the patient's live record — a legitimate derivation, not a fabrication). `Patient.name` is `HumanName.text` only — the internal `name` column is one free-text field with no given/family split, and inventing one by parsing the string would misrepresent names that don't split cleanly. `Patient.link` (pointing a merged record at its survivor) is **not implemented**. **ABHA is absent** — no field for it exists internally, and none is invented here.

### Practitioner
Sourced from `doctors`, not `staff` — see "Staff vs. Practitioner" below for why that distinction matters throughout this layer. `qualification[].code.text` carries the free-text `qualifications` column (CodeableConcept's documented free-text fallback). No `identifier` (no license/registration number column exists). **`specialization` is deliberately not mapped** on `Practitioner` itself — in real FHIR that's a `PractitionerRole` concept.

### PractitionerRole (Phase 9)
One `PractitionerRole` per `doctors` row, id reused from the doctor's own id (a genuine 1:1 mapping — no separate identifier scheme needed). Carries only `practitioner` (→ `Practitioner/{id}`), `organization` (→ `Organization/{hospital_id}`), and `active`. **`specialty` is deliberately omitted** — `doctors.specialization` is still uncoded free text (the same gap `Practitioner` already documents), and moving an uncoded string to a different resource doesn't make it any more coded. **`location` is deliberately omitted** — `doctor_departments` is a real many-to-many relationship, but a scheduling "department" is an operational grouping in this application, not a physical FHIR `Location` (no room/ward/address data exists anywhere in this schema — see "Location" below), and PractitionerRole has no native "department" element to force it into instead.

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

### Observation (from `order_results`) — id `or-{order_results.id}`
`status` is always `"final"` — a result only exists once its order reaches `COMPLETED`, in the same transaction (`app/services/order_services.py`), and there is no amendment/correction workflow (confirmed in Phase 7) that would ever produce `amended`/`corrected`. **Value typing is a syntactic transformation, not a clinical one**: if `result_value` parses as a number, it becomes `valueQuantity` (carrying `unit`/`unit_system`/`unit_code` when present, from Phase 7's own coding slot); otherwise it stays `valueString`. `interpretation` is included only when `is_abnormal`/`is_critical` is actually `true` — never asserted as `"Normal"` when both are false, matching the existing result table/UI's own restraint (it shows nothing, not a "Normal" label, when neither flag is set). No LOINC (still absent, confirmed in Phase 7).

### Observation (from `vitals`) — id `vt-{vitals.id}` (Phase 9)
**One Observation resource per `vitals` row**, not one per measurement — the standard real-world FHIR "vital signs panel" pattern, and the one that was actually evaluated and rejected in Phase 7/8 as "would need synthetic composite ids, 1 row → up to 9 resources" (see the now-superseded line in the Phase 8 mapping table). Every non-`NULL` measurement among `bp_systolic`, `bp_diastolic`, `pulse`, `temperature_celsius`, `spo2`, `respiratory_rate`, `weight_kg`, `height_cm`, `bmi` (a `GENERATED` column, passed through like any other value, never recomputed here), and `pain_score` becomes one entry in `component[]`. **This single design choice also satisfies the "blood pressure is one clinical measurement, not two unrelated observations" requirement** — `bp_systolic`/`bp_diastolic` are simply two components of the same panel, exactly like every other vital, with no special-casing needed.

`category` carries the real, standard HL7 `observation-category`/`vital-signs` code — a structural/administrative classification of *what kind* of Observation this is (parallel to `Encounter.class`'s `v3-ActCode`/`AMB`), not a clinical judgment. **No LOINC or UCUM anywhere** — every `component[].code` is `{"text": "<human-readable label>"}` (e.g. "Systolic blood pressure", "Oxygen saturation") and every `valueQuantity.unit` is a plain string (e.g. `"mmHg"`, `"°C"`, `"%"`), never a `system`/`code` binding. This was a specific, repeatedly-considered decision: the individual LOINC codes for vitals (8480-6, 8462-4, 8867-4, …) are extremely well-known, but they are still the exact class of external clinical terminology Phase 9's own instruction singled out as never to hardcode without an authoritative source — unlike `v3-ActCode`/`observation-category`, which are HL7's own structural/workflow vocabularies, not a claim about what a specific measurement *means* clinically. `chief_complaint`/`priority`/`nursing_notes` are deliberately excluded from this Observation — they are not vital-sign measurements (closer to an Encounter/Condition-reason concept and free narrative, respectively), and folding them in would misrepresent what kind of data they are.

**Returns no resource (404) when every measurement field is `NULL`** — a triage row that only records `chief_complaint` (no actual vital taken yet) has nothing to represent as an Observation, the same "nothing documented, so no resource" rule `Condition` already established for a diagnosis-less consultation.

### ServiceRequest
One resource per `orders` row. `category` carries `order_type` (LAB/RADIOLOGY/PROCEDURE/SERVICE/EXTERNAL_REFERRAL) as free text, not a coded system. `authoredOn` (not `occurrenceDateTime`) uses `ordered_at`, since this schema doesn't separately track a planned/scheduled service time.

## Staff vs. Practitioner

A recurring, easy-to-get-wrong distinction handled carefully throughout this layer: `staff` (ADMIN/STAFF/DOCTOR/NURSE/RECEPTIONIST/LAB_TECH/PHARMACIST/BILLING login accounts) and `doctors` (the actual clinical-provider entity) are two **different tables** in this application's own domain model. Only columns that are genuinely a `doctor_id` (`appointments.doctor_id`, `consultations.doctor_id`, `orders.ordering_doctor_id`, `prescriptions.doctor_id`) become a `Practitioner` reference. Columns that are a `staff_id` (`patient_allergies.recorded_by`, `order_results.recorded_by`, `vitals.recorded_by`, `consultations.created_by`) are **never** mapped to `Practitioner` — a nurse or receptionist recording an allergy is not a clinician, and representing them as one would misrepresent who did what.

## Explicitly NOT Implemented

- **`Location`.** No room/ward/bed/address data exists anywhere in this schema (`departments` has no location columns at all — re-verified against `information_schema.columns` in Phase 9) — inventing room numbers or a physical hierarchy would fabricate data this application has never collected. Deferred until real location data exists to map, not built as a stub.
- **`Organization` hierarchy.** This deployment is single-tenant (one `hospitals` row) with no department/sub-organization structure in the data — `departments` is a scheduling grouping, not a legal/organizational sub-unit, and forcing it into a fabricated parent/child `Organization` hierarchy was considered and rejected in Phase 9.
- **`DiagnosticReport`.** No internal concept groups multiple `order_results` into one authored, signed-off report distinct from the order itself.
- **LOINC** on `order_results.parameter`/`orders.description`/vitals measurements — no test/parameter catalog exists internally to anchor a code to (`docs/workflows/LABORATORY.md`'s own Phase 7 finding, re-confirmed in Phase 9's terminology readiness review below); coding per free-text instance without a catalog would let the same real-world test get inconsistently coded across orders.
- **RxNorm / ATC / any external medication terminology.**
- **UCUM on vitals.** `order_results.unit_system`/`unit_code` (Phase 7) are real, human-entered coding slots and are used as-is; vitals has no equivalent column, and Phase 9 deliberately did not manufacture one from column names (e.g. inferring `Cel`/`kg`/`mmHg` UCUM codes because a column is named `temperature_celsius`) — see the terminology readiness matrix below.
- **Real pagination.** Every search/`$everything` endpoint below applies a hard `LIMIT 50` (`_SEARCH_LIMIT`) and returns everything in one response — no `link[]`/`next`/`_count` cursor. This repo's actual data volumes (dozens of rows per table, confirmed live) don't yet justify building real pagination.
- **Write operations** (`POST`/`PUT`/`PATCH`/`DELETE`) — this is a read-only layer, full stop. No external system can create or modify a clinical record through FHIR.
- **`AuditEvent`.** No read-auditing of FHIR access exists (see "Audit" below, unchanged since Phase 8).
- **`Consent`.** No consent model of any kind exists in this codebase (verified in Phase 9 by grepping every migration and every `app/` module for "consent" — zero matches). Not implemented, and the existing break-glass/emergency-access pattern (if any exists elsewhere in this application) is never treated as a substitute for a real FHIR `Consent` resource or an ABDM consent artifact.
- **`Subscription`, CDS Hooks, NHCX, HL7 v2, DICOM, IHE.**
- **SMART on FHIR, OAuth2/OIDC.** See "Authentication" below.
- **ABDM profiles, ABHA identity, ABDM consent exchange, HIU/HIP APIs, ABDM sandbox/network connectivity.** Base R4 resources only — no custom profile, no ABDM-specific extension or identifier system, and no network call to any ABDM service anywhere in this codebase. See `docs/interoperability/ABDM_READINESS_AUDIT.md` for the full, evidence-based readiness assessment and gap analysis — that document explicitly does not implement any of this either.

## Identifiers & References

`Resource.id` is this application's own internal integer primary key, stringified (`str(patients.id)`, etc.) — the **same exposure level** every existing `/api/...` endpoint already uses for `patient_id`/`appointment_id`/etc. in its own URLs, behind the same staff-session authentication and `hospital_id` tenant check. FHIR introduces no new identifier scheme, no UUIDs, no public/opaque-id layer. The clinically meaningful identifier (UHID) is carried separately, in `Patient.identifier`, matching FHIR's own distinction between a resource's technical `id` and its clinical `identifier`.

References between resources (`Patient/{id}`, `Encounter/{id}`, `Practitioner/{id}`, `Medication/{id}`) are always constructed from a real, already-fetched internal foreign key. A reference is **never fabricated** — if the related row doesn't exist or the field is `NULL`, the reference is simply absent from the resource.

**Re-verified in Phase 9** (the instruction explicitly asked this to be re-checked, not assumed still true): every table this layer reads a primary key from (`patients`, `doctors`, `hospitals`, `appointments`, `encounters`, `consultations`, `patient_allergies`, `medications`, `prescription_items`, `order_results`, `vitals`, `orders`) is backed by its own single **global** Postgres identity/sequence — confirmed via `pg_get_serial_sequence` and by inspecting real rows across multiple `hospital_id` values — so no two hospitals can ever produce the same internal id for the same table. The bare-integer id scheme from Phase 8 remains safe to keep for every resource except Observation (below); it was not redesigned.

### Observation `id` format — breaking change (Phase 9)

Phase 9 added `vitals` as a second source for the `Observation` resource type. `order_results.id` and `vitals.id` are two **independent** auto-increment sequences — without a prefix, `order_results` row 5 and `vitals` row 5 would both have produced `Observation/5`, a real identifier collision Phase 8 never had to consider because only one source table existed. The fix: every `Observation.id` now carries a two-letter source prefix — **`or-{id}`** for `order_results`, **`vt-{id}`** for `vitals` — applied to both sources for consistency, not just the new one. `GET /fhir/r4/Observation/{id}` parses this prefix and queries the matching table; an id with neither prefix, or a non-numeric suffix, is a 404. This is a **breaking change** from Phase 8's bare-integer Observation ids, made deliberately: no external consumer of this not-yet-released interoperability layer exists yet to break.

## Meta and Versioning (Phase 9)

`meta.lastUpdated` is populated on every resource whose source table has a genuine, actively-maintained `updated_at` column (`Patient`, `Practitioner`, `PractitionerRole` (reusing `doctors.updated_at`, the same column `Practitioner` reads), `Organization`, `Appointment`, `Encounter`, `Condition`, `Medication`, `MedicationRequest`, `ServiceRequest`) — built from that real column, never a fabricated timestamp. Resources sourced from tables with **no** `updated_at` column (`AllergyIntolerance` ← `patient_allergies`; `Observation` ← `order_results` or `vitals`) simply have **no `meta`** at all, rather than a guessed or borrowed timestamp.

**`meta.versionId` is never populated, on any resource.** `lastUpdated` and `versionId` are independently optional FHIR elements — omitting one says nothing false about the other. No table anywhere in this schema tracks a real, incrementing per-row version counter (re-verified across every source table in Phase 9), so inventing a `versionId` — even a fake "1" for every resource — would assert a version-history guarantee (that this exact representation is stable and retrievable by version) this application cannot actually honor.

## Tenant Isolation

Every query filters by `hospital_id`, exactly matching the convention `app/api/patients.py`'s `get_patient` (and every other single-record `GET` in this codebase) already established: `WHERE id = %s AND hospital_id = %s` (or, for tables that don't carry `hospital_id` directly — `consultations`, `patient_allergies`, `prescription_items`, `orders`, `order_results` — a join through `encounters`/`patients`, the same derivation `tests/test_hospital_tenant_coverage.py`'s `EXEMPT_TABLES` documents for those tables elsewhere in this app). A resource that doesn't exist and a resource that exists in a **different** hospital both return the identical 404 — never a 403, which would confirm the record exists somewhere. Verified in `tests/test_fhir.py::test_all_fhir_endpoints_isolate_by_tenant` against a real second hospital, not just asserted.

## Authentication

**FHIR authentication = existing HIMS authentication.** Every endpoint depends on the same `get_current_staff` (bearer session token) every other `/api/...` endpoint already uses — no second auth mechanism, no API key scheme, no service account model. `SMART on FHIR` and `OAuth2/OIDC` are explicitly future interoperability-phase work, not built or simulated here.

## Read-Only Scope

Only `GET` routes exist (single-resource reads, search, and `$everything` — all three read-only). No create/update/delete route. This is deliberate: an interoperability boundary that only reads never becomes a second, uncontrolled write path into clinical data alongside the application's own existing, carefully RBAC-gated write endpoints.

## Search (Phase 9)

The smallest useful search set, not a general FHIR search engine:

- **`GET /fhir/r4/Patient?identifier={uhid}`** — exact match on the one identifier this system treats as a real, permanent identity (`patients.uhid`, per `docs/decisions/ADR-001-PATIENT-IDENTITY.md`). No fuzzy name/DOB search — that already exists as this application's own internal `/api/patients` search and is a different, non-FHIR concern.
- **`GET /fhir/r4/{Appointment,Encounter,Condition,AllergyIntolerance,MedicationRequest,Observation}?patient={id}`** — every one of these resource types already carries (directly, or via its encounter) a `patient_id` internally, so `?patient=` is an honest search parameter for each. `Observation?patient=` searches **both** sources (`or-`/`vt-`) and merges the results into one Bundle.

Every search query reuses the exact same `hospital_id`-scoped SQL shape (same joins, same tenant filter) as the matching single-resource `GET` above it — no new query pattern, no new index. A `patient` id that belongs to a different hospital, or doesn't exist at all, silently produces an **empty Bundle** rather than a 404 or 403 (the join itself yields zero rows) — this matches real-world FHIR search semantics (an unmatched search is empty, not an error) and leaks nothing a 404 wouldn't already leak less of. Every result set is capped at `_SEARCH_LIMIT = 50` with no further pagination (see "Explicitly NOT Implemented"). Results are returned as a `Bundle` of `type: "searchset"` with `total` and `entry[].resource` only — no `search.mode`, no `fullUrl` (this server assigns no absolute URL to a resource that would make one honest).

**Assessed and not built:** search by any other parameter (name, date range, status, `_include`/`_revinclude`, `_sort`, chained search). None of these had a concrete evidenced need in this phase; adding them speculatively would be exactly the kind of premature generality this codebase's own engineering conventions (see root `CLAUDE.md`) already discourage.

## $everything / Bundle (Phase 9)

**`GET /fhir/r4/Patient/{id}/$everything`** — FHIR's own standard operation name (not an invented one) for "every resource this server can produce about this patient." Returns a `Bundle` of `type: "collection"` containing the `Patient` plus every `Appointment`, `Encounter`, `Condition`, `AllergyIntolerance`, `MedicationRequest`, `ServiceRequest`, and `Observation` (both sources) for that patient, each capped at the same `_SEARCH_LIMIT`. This one operation deliberately serves both the "minimal Bundle" and "Patient Summary" asks from Phase 9's instruction, rather than building two separate, overlapping endpoints (one real FHIR standard operation beats one real operation plus one invented `$summary`-style name).

Every entry is a **reference**, never a `contained` resource — this application's real data volumes are small enough that a client can simply dereference each entry with its own `GET`, so there is no genuine need to inline resources and no risk of the `contained`-resource id-scoping pitfalls that come with it. A patient id from a different hospital, or one that doesn't exist, returns the same 404 `OperationOutcome` as every other single-resource lookup in this layer.

No new database table, no new "patient summary" cache, no denormalized read model — `$everything` is computed on read from the same per-resource queries the search endpoints above already use, called against one `patient_id` and assembled into one Bundle.

## Error Handling

Resource-level errors (not found; exists in a different hospital) return a FHIR `OperationOutcome` body, constructed and returned directly as a `JSONResponse` — bypassing `app/error_handling.py`'s global exception handler, which wraps ordinary `HTTPException`s in this application's own `{success, errorCode, message, details}` envelope. Authentication failures (401) are raised by the shared `get_current_staff` dependency itself, before any FHIR endpoint code runs, and keep that dependency's existing response shape — per "FHIR authentication = existing HIMS authentication" above, its error format is inherited too, not re-wrapped.

## Content Type

Every successful and `OperationOutcome` response is returned with `media_type="application/fhir+json"`, distinct from the plain `application/json` every `/api/...` endpoint uses.

## Resource Validation

No FHIR library dependency was added (`requirements.txt` gains nothing from this phase) — evaluated and rejected for this minimal foundation: a full FHIR resource library (e.g. `fhir.resources`) is a large, version-pinned dependency, and this application has no existing precedent for schema-validation libraries beyond Pydantic (used here only for the framework's own request/response typing, not FHIR-specific validation). Structural correctness is instead enforced directly in `app/services/fhir_mappers.py`: every resource always carries `resourceType` and `id`; references are always `{"reference": "ResourceType/id"}`; dates are always ISO 8601 (`.isoformat()` on real `datetime`/`date` values, never a hand-built string); status/coding fields are drawn from fixed, reviewed translation dicts, never free-typed at the call site. This is **not a claim of full FHIR conformance** — no resource here has been validated against the actual FHIR R4 StructureDefinitions, and `docs/architecture/FHIR_FOUNDATION.md` (this file) makes no such claim anywhere.

## Audit

**FHIR reads are not audit-logged in Phase 8.** `app/services/audit_log.py`'s `record_audit_log` is used exclusively for RBAC-gated *write* actions everywhere else in this codebase (confirmed by inspection — every one of its ~20 existing call sites is a mutation); no `GET` endpoint anywhere in this application, FHIR or otherwise, is currently audited. Adding read-auditing only for FHIR would be a first-of-its-kind departure from that convention, done without a concrete driving requirement. This is a decision to revisit, not an oversight — if/when FHIR access needs stronger accountability than the rest of this API currently has, that's real, separately-scoped future work, not invented here.

## Terminology Readiness Matrix (Phase 9)

A precise readiness assessment per terminology system — "a code *field* exists" is explicitly **not** the same claim as "terminology is *supported*," and this matrix distinguishes them:

| Terminology | Where a slot exists | Populated today? | Readiness |
|---|---|---|---|
| **SNOMED CT** | Nowhere — no column anywhere in this schema is scoped to SNOMED specifically | No | **NOT implemented** — no catalog, no ingestion path, no evidenced need identified |
| **ICD (10/11)** | `consultations.diagnosis_code_system`/`diagnosis_code`/`diagnosis_code_display` (Phase 6) — generic, not ICD-specific; happens to hold `"ICD-10"` when a human enters it | Rarely — free-text entry, not validated against a real ICD catalog | **PARTIALLY implemented** — the slot is real and passed through honestly (`Condition.code.coding`), but `Coding.system` is whatever free text was typed (not a real URI), and nothing here validates the code against an actual ICD-10 code list |
| **LOINC** | Nowhere — `order_results.parameter`, `orders.description`, and every vitals label are free text | No | **NOT implemented** — no lab/vitals test catalog exists internally to anchor a code to; Phase 7 and Phase 9 both independently confirmed this and both declined to hardcode well-known LOINC codes without one |
| **UCUM** | `order_results.unit_system`/`unit_code` (Phase 7) — generic, not UCUM-specific; happens to hold `"UCUM"` when a human enters it | Sometimes — depends on data entry | **PARTIALLY implemented** — same pattern as ICD-10 above: passed through honestly when present, never validated, never manufactured for vitals (which has no such column at all) |
| **RxNorm / ATC** | Nowhere — `medications` (Phase 5's Medication Master) has no terminology-code column | No | **NOT implemented** — `docs/OPD_HIMS_STANDARDS_READINESS.md` §18 documents this as a readiness gap to evaluate, not a Phase 9 build item |

The through-line: this application's terminology-*readiness* work (Phases 6/7) built honest, optional, human-populated coding *slots* next to existing free text — never a code assigned by inference from another field, and never a hardcoded lookup table pretending to be a real terminology binding. Phase 9 made no change to this pattern; it only re-confirmed it while extending the FHIR layer that reads from it.

## FHIR Compliance Claim

**FHIR R4 read-only resource mapping implemented for:** Patient, Practitioner, PractitionerRole, Organization, Appointment, Encounter, Condition, AllergyIntolerance, Medication, MedicationRequest, Observation (from `order_results` and `vitals`), ServiceRequest — plus a minimal search (`?patient=`, `?identifier=`) and the standard `$everything` operation.

This is **not** a claim of "FHIR compliant," "FHIR certified," "ABDM compliant," or "SMART on FHIR" — none of those are true of this phase, and none is claimed. It is a claim about exactly the list above: a working, tenant-isolated, read-only mapping from real internal data to FHIR R4 JSON shapes, verified by `tests/test_fhir.py` and a live end-to-end patient journey (see the Phase 9 verification report for the exact run). See `docs/interoperability/ABDM_READINESS_AUDIT.md` for why "this FHIR layer exists" is explicitly not treated as "this system is ABDM-ready."

## Future Work

- ABDM connectivity itself (ABHA creation/linking, consent exchange, HIU/HIP APIs, sandbox/network integration) — a distinct future phase; see `docs/interoperability/ABDM_READINESS_AUDIT.md` for the gap analysis and proposed future adapter architecture this would build against.
- SMART on FHIR / OAuth2/OIDC authentication.
- Write support (`POST`/`PUT` for external systems to create/update records) — a materially larger trust and validation problem than this phase's read-only scope.
- FHIR Subscriptions, CDS Hooks, `AuditEvent`, `Consent`.
- `DiagnosticReport`, `Location`, a real `Organization` hierarchy — once real data exists to map, not before.
- Real FHIR search pagination (`link[]`/`_count`) and additional search parameters, once actual data volumes or a concrete consuming use case justify them.
- SNOMED CT / LOINC / RxNorm ingestion (a genuine terminology-catalog project, not a mapping-layer change).
