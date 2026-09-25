# HospitalOS — Phase 2: Terminology Architecture Audit

Read-only discovery deliverable. **No schema, API, UI, or clinical workflow
was changed to produce this audit.** Every finding below cites the actual
migration file, service function, or grep result it came from. Where the
repository was searched and nothing was found, that's reported as
verified-absent. Where evidence was insufficient to state something with
confidence, it's marked `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION`
rather than assumed either way.

This is Phase 2, following `docs/OPD_HIMS_INTEROPERABILITY_AUDIT.md`
(Phase 0: zero external standards implemented) and
`docs/OPD_HIMS_CANONICAL_MODEL_AUDIT.md` (Phase 1: the internal domain model
is encounter-centric and largely FHIR-mappable, but every clinical text
field is free text). This document goes one level deeper on exactly that
free-text finding: which fields are genuinely free text, which look free
text but are actually DB-constrained enums, where the same concept is
represented more than once, and what a future terminology layer would
actually need to touch.

---

## 1. Executive summary

**Three different things are currently lumped together under "free text" in
casual conversation, and this audit found it's important to keep them
separate:**

1. **Genuinely free clinical narrative** (`clinical_notes`, `history_notes`,
   `examination_notes`) — correctly free text, should stay free text. No
   terminology binding is appropriate here (per this phase's own §4
   instruction not to try).
2. **Structured clinical concepts stored as a single free-text string**
   (`diagnosis`, `patient_allergies.allergen`, `prescription_items.
   medicine_name`, `orders.description`, `order_results.parameter`/`.unit`)
   — these are the real terminology-binding candidates. Each represents one
   discrete, nameable clinical thing, entered as prose today only because no
   coded alternative exists yet.
3. **Things that look like free text but are actually already
   DB-constrained enums** — `encounters.encounter_type`, every `*.status`
   column, `*.priority` columns, `patient_allergies.severity`. These are
   **already structured**, just not against an external standard. This
   distinction matters: introducing SNOMED/LOINC/ICD doesn't touch these at
   all; they'd map to FHIR's own fixed value sets (e.g. `Encounter.status`),
   not to a clinical terminology.

**One new finding this phase surfaced that Phase 1 didn't**:
`pharmacy_stock.medicine_name` and `prescription_items.medicine_name` are
two **independent** free-text fields, with no shared medication master
table and no FK between them — confirmed by reading
`app/services/pharmacy_services.py` directly: stock search is `ILIKE
'%medicine_name%'` fuzzy text matching, and `record_dispense_service` takes
an explicit, manually-chosen `pharmacy_stock_id`, not one resolved
automatically from the prescription. A pharmacist is the bridge between the
two strings today, not the software. This is a third duplicate-
representation finding, alongside Phase 1's phone-identifier and
vitals/order_results findings.

**Vital signs are the one clinical-measurement area that's already well-
structured** — individually typed, range-`CHECK`-constrained columns, not
free text — and don't need the same terminology-binding treatment
`order_results` does; they need a UCUM binding for their (currently
implicit) units, which is a smaller, different kind of gap.

---

## 2. Existing terminology architecture

There isn't one. Confirmed identically to Phase 0: zero references to
"snomed," "loinc," "icd," "ucum," "rxnorm," or any `{code, system, display}`
shape anywhere in `migrations/`, `app/`, or `frontend/src/` (full-repo grep,
re-run for this phase, same zero result as Phase 0/1). What exists instead,
confirmed by direct table inspection, is described domain-by-domain below.

---

## 3. Terminology domain audit (all 25, verified individually)

| Domain | Current Representation | Source | Standard Candidate | Status | Evidence | Gap |
|---|---|---|---|---|---|---|
| **1. Diagnoses** | `consultations.diagnosis` — one `TEXT` field per consultation | `app/api/clinical.py` | SNOMED CT (clinical) / ICD (classification — see §8) | NOT IMPLEMENTED | Migration `0029`'s own comment: "diagnosis is a [free-text field]... a structured, codeable diagnosis list is real future work" | No code, no list (one diagnosis string, not multiple discrete, individually-trackable diagnoses per visit) |
| **2. Clinical conditions** | Same field as Diagnoses — no separate `conditions` concept | same | SNOMED CT | NOT IMPLEMENTED | No `conditions` table found anywhere (grep) | "Diagnosis" and "Condition" are the same one field today — see §4 for why this matters for scoping |
| **3. Symptoms** | `consultations.chief_complaint` (`TEXT`), `vitals.chief_complaint` (`TEXT`, duplicated field name across two tables — see note below) | `app/api/clinical.py` | SNOMED CT (findings/symptoms hierarchy) | NOT IMPLEMENTED | Both are plain `TEXT`, confirmed by migration `0029` | Free text narrative-adjacent — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` whether `vitals.chief_complaint` and `consultations.chief_complaint` are meant to be the same value duplicated, or genuinely independent (triage's chief complaint vs. the doctor's own framing) — this wasn't disambiguated by this pass and matters for whether it's a fourth duplicate-representation finding or two legitimately different facts |
| **4. Allergies** | `patient_allergies.allergen` (`TEXT`), `.severity` (`CHECK ('MILD','MODERATE','SEVERE')` — already an enum) | `app/api/patients.py` | SNOMED CT / a substance terminology | PARTIALLY IMPLEMENTED | Structure real (migration `0042`); `allergen` itself free text | `severity` is already standard-adjacent (a fixed 3-value enum, easy to map to FHIR `AllergyIntolerance.criticality`-like concepts); `allergen` is the actual terminology gap |
| **5. Medications** | `prescription_items.medicine_name`/`.generic_name` (`TEXT`) **and, independently**, `pharmacy_stock.medicine_name` (`TEXT`) | `app/api/pharmacy.py` | RxNorm-adjacent (see §9 — not assumed to be the right choice without more input) | NOT IMPLEMENTED | Both confirmed free text, migration `0032`; no shared table | Two independent free-text representations of "this medicine" (prescribing vs. stock) with no FK — see §9 |
| **6. Medication forms** | None found. | — | — | NOT IMPLEMENTED | Grep for a `form`/`dosage_form` column on `prescription_items` or `pharmacy_stock` — zero hits | No distinct tablet/capsule/syrup/injection field exists; if captured at all, it's informally embedded in the free-text `medicine_name` or `dosage` string — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` via the actual frontend form (`PrescriptionPanel.tsx`) whether staff are informally instructed to include form in one of the existing free-text fields; this audit did not find a dedicated column either way |
| **7. Medication routes** | `prescription_items.route` (`TEXT`) | `app/api/pharmacy.py` | An administration-route value set (small, could be a simple internal enum before any external terminology) | NOT IMPLEMENTED | Free text, migration `0032` | Small, bounded real-world value set (oral/IV/IM/topical/etc.) — a good candidate for a lightweight internal enum even before a full terminology-service investment, see §14 |
| **8. Laboratory tests** | `orders.description` (`TEXT`) where `order_type = 'LAB'` | `app/api/orders.py` | LOINC | NOT IMPLEMENTED | Free text, migration `0030`; no test catalog anywhere (`docs/architecture/ORDER_SPINE.md`) | No searchable test catalog at all — every order is staff-typed |
| **9. Laboratory parameters** | `order_results.parameter` (`TEXT`) | `app/api/orders.py` | LOINC | NOT IMPLEMENTED | Free text, migration `0031` | Same gap as #8, one level down (the individual analyte within a test, e.g. "Hemoglobin" within a CBC) |
| **10. Laboratory results** | `order_results.result_value` (`TEXT`, not even numeric-typed) | `app/api/orders.py` | N/A (results are values, not terminology) — but `is_abnormal`/`is_critical` flags are structured | PARTIALLY IMPLEMENTED | `result_value TEXT` — stores a numeric lab value as a string; `is_abnormal BOOLEAN`, `is_critical BOOLEAN` are real, structured flags | Result value itself isn't typed as numeric — can't be range-validated or aggregated/trended without parsing free text first; this is a data-quality gap independent of terminology |
| **11. Vital signs** | `vitals` — nine individually typed, range-`CHECK`-constrained columns (`bp_systolic SMALLINT CHECK (... > 0)`, etc.) | `app/api/clinical.py` | UCUM (units only — the measurements themselves are already structured) | IMPLEMENTED (structurally) / NOT IMPLEMENTED (unit coding) | Migration `0029`, full column list confirmed in Phase 1 | This is the one measurement domain that's already well-structured. The only real gap is that units are implicit in column naming (`weight_kg`, `temperature_celsius`), not stored as an explicit, codeable value — see §7 |
| **12. Procedures** | `orders.description` where `order_type = 'PROCEDURE'` | `app/api/orders.py` | SNOMED CT (procedure hierarchy) | NOT IMPLEMENTED | Same generic `orders` row/free-text description as every other order type | No procedure-specific fields (technique, body site — see #22) |
| **13. Investigations** | Same as Laboratory Order/Radiology Order — `orders` generically | `app/api/orders.py` | LOINC/SNOMED depending on type | NOT IMPLEMENTED | — | "Investigation" isn't a distinct concept from Order in this schema — it's the same table, same gap |
| **14. Units of measurement** | Implicit (vitals: column-name convention) or free text (`order_results.unit`) | `app/api/clinical.py`, `app/api/orders.py` | UCUM | NOT IMPLEMENTED | Confirmed: no `unit` column exists on `vitals` at all (grep of migration `0029` — zero hits for "unit"); `order_results.unit` is `TEXT`, nullable | Two different unit problems: vitals' units aren't stored as data at all (they're a naming convention only a developer reading the schema would know); `order_results.unit` is stored but uncoded free text — see §7 |
| **15. Departments / specialties** | `departments.name` (`TEXT`, master table); `doctors.specialization`/`.sub_specialization` (`TEXT`, independent free text) | `app/api/departments.py`, `app/api/doctors.py` | A specialty taxonomy (no single obvious standard named in this prompt; not assumed) | PARTIALLY IMPLEMENTED | `departments` is a real master table (migration `0001`); `doctors.specialization` is separate free text with **no FK or even naming convention tying it to `departments.name`** (migration `0014`) | A doctor's free-text `specialization` and the `departments` a doctor is actually assigned to (`doctor_departments`) are two independent facts about the same doctor today — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` whether any UI/workflow keeps them consistent by convention; not confirmed either way by this pass |
| **16. Appointment types** | `appointment_types.name` (`TEXT`, master table) | `app/api/appointment_types.py` | N/A — this is a scheduling concept (visit-type), not a clinical terminology domain | IMPLEMENTED (as an internal master table; not externally coded, and doesn't need to be) | Migration `0001` | None — this is correctly internal-only; no external standard applies to "what kind of visit is this" the way one applies to "what is the diagnosis" |
| **17. Encounter types** | `encounters.encounter_type` — `CHECK (encounter_type IN ('OPD'))` | `app/services` (internal) | FHIR `Encounter.class`-adjacent fixed value set | IMPLEMENTED (as a DB-enforced enum, not free text) | Migration `0028`, confirmed in Phase 1 | **This is not a free-text gap at all** — it's a narrow-by-design enum (one value today), correctly structured; the "gap" is that it only has one value, which is an encounter-*model* question (`docs/architecture/OPD_TO_IPD.md`), not a terminology one |
| **18. Clinical statuses** | `consultations.status` (`DRAFT`/`COMPLETED`), `orders.status` (`ORDERED`/`IN_PROGRESS`/`COMPLETED`/`CANCELLED`), `prescriptions.status` (`DRAFT`/`PRESCRIBED`/`CANCELLED`), `appointments.status` (7 values) | throughout `app/api/` | FHIR's own per-resource status value sets (`Condition.clinicalStatus`, `ServiceRequest.status`, etc.) | IMPLEMENTED (as internal DB-enforced enums) | Every one confirmed via direct `CHECK` constraint reading | Not a terminology gap — a future FHIR mapping layer would translate these internal enums to FHIR's required status vocabularies (a mapping-layer job, not a schema change) |
| **19. Observation statuses** | None. | — | FHIR `Observation.status` (preliminary/final/amended/corrected) | NOT IMPLEMENTED | `order_results` has no `status` column at all (confirmed: migration `0031`'s full column list has no status field); `vitals` has none either | A result is either present or not — there's no preliminary-vs-final distinction, no amendment/correction tracking for a result once entered (unlike `consultations`, which has a real amendment mechanism via `consultation_amendments`) |
| **20. Result interpretation** | `order_results.is_abnormal`/`.is_critical` (two independent `BOOLEAN` flags) | `app/api/orders.py` | FHIR `Observation.interpretation` (a coded value set: Normal/High/Low/Critical/Abnormal, etc.) | PARTIALLY IMPLEMENTED | Migration `0031` | Two booleans can't express "High" vs. "Low" (both would just be `is_abnormal = true`) — a real, if narrow, structural gap against what a coded interpretation value set would capture |
| **21. Severity** | `patient_allergies.severity` (`MILD`/`MODERATE`/`SEVERE`), `vitals.priority` (`ROUTINE`/`URGENT`/`EMERGENCY`), `orders.priority` (`ROUTINE`/`URGENT`/`STAT`) | throughout | Each maps reasonably to a small FHIR-adjacent value set already | IMPLEMENTED (as internal enums) | All three confirmed via `CHECK` constraints | Not a gap — already structured; note the three enums use **different value sets for a conceptually similar idea** (severity/priority/urgency) — worth naming as a minor inconsistency, not a defect |
| **22. Body sites** | None. | — | SNOMED CT (body structure hierarchy) | NOT IMPLEMENTED | Zero references to "body_site"/"bodysite" anywhere (grep) | Full — no procedure or examination field captures anatomical location today |
| **23. Specimens** | None. | — | SNOMED CT (specimen hierarchy) | NOT IMPLEMENTED | Zero references to "specimen" anywhere (grep) | Full — consistent with Phase 1's independent finding that no sample-collection tracking exists (`docs/workflows/LABORATORY.md`) |
| **24. Imaging studies** | None. | — | DICOM (not a terminology, a protocol — see Phase 0) | NOT IMPLEMENTED | Confirmed absent identically in Phase 0/1 | Full |
| **25. Imaging modalities** | None. | — | DICOM modality codes | NOT IMPLEMENTED | `orders.order_type = 'RADIOLOGY'` has no sub-field for CT/MRI/X-Ray/Ultrasound — confirmed by the full `orders` column list (migration `0030`) | A radiology order doesn't even record *which kind* of imaging was requested as a structured field — it's inside the free-text `description` if captured at all |

---

## 4. Structured clinical concept vs. clinical narrative — the explicit distinction

Per this phase's own instruction not to treat all free text as wrong:

**Genuinely narrative, should stay free text** (confirmed by reading each
field's actual use): `consultations.history_notes`, `.examination_notes`,
`.clinical_notes` — these are prose fields where a doctor writes in their
own words; there is no realistic "coded" version of a clinical narrative,
and this phase does not recommend one. `prescription_items.
special_instructions`/`.food_instructions` — patient-facing prose, same
reasoning. `vitals.nursing_notes` — same. `charges`/`orders`/`prescriptions`
cancel/void `reason` fields — administrative narrative, not clinical
terminology at all.

**Structured clinical concept, single free-text field today, real
terminology-binding candidate**: `consultations.diagnosis`,
`patient_allergies.allergen`, `orders.description` (for LAB/RADIOLOGY/
PROCEDURE types specifically — `SERVICE`/`EXTERNAL_REFERRAL` descriptions
are more administrative), `order_results.parameter`, `prescription_items.
medicine_name`/`.generic_name`.

**In between, worth naming explicitly**: `consultations.chief_complaint` —
this is often a patient's own words ("chest hurts when I breathe") rather
than a clean clinical concept, so forcing it into a coded field the way
`diagnosis` might be coded would lose information a narrative field
preserves. A real terminology phase should decide whether chief complaint
gets a parallel *coded* field alongside the free text (Option-A-style, see
§14) rather than being converted outright — this audit flags the question,
does not answer it.

---

## 5. SNOMED CT assessment

**Candidate concepts** (from §3): Diagnosis, Condition (same field),
Allergen, Procedure description, and — if a coded chief-complaint field is
ever added per §4 — Symptoms.

- **Which tables need terminology references?** `consultations` (a new
  nullable code column alongside `diagnosis`, per Option A in §14 — not
  replacing it), `patient_allergies` (alongside `allergen`), `orders`
  (alongside `description`, for LAB/RADIOLOGY/PROCEDURE order types).
- **Which APIs expose these values?** `app/api/clinical.py` (consultations),
  `app/api/patients.py` (allergies), `app/api/orders.py` (orders) — all
  three already return the free-text value today; adding a parallel coded
  field is additive to each response shape, not a breaking change (see
  §12).
- **Which UI screens create/edit them?** `ConsultationWorkspace.tsx`
  (Consultation and Orders tabs), `PatientFormModal.tsx`/wherever allergies
  are entered (`TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` the exact
  allergy-entry screen — not pinned down precisely by this pass).
- **Do existing values have stable internal IDs?** Yes for the *row*
  (`consultations.id`, `patient_allergies.id`, `orders.id` are all stable
  primary keys) — but the *value itself* (the diagnosis string) has no
  identity beyond being text in that row; two consultations with
  `diagnosis = 'Diabetes'` share no linkage today (no shared reference
  row), confirmed by there being no `conditions`/`diagnoses` master table.
- **Can terminology be added without breaking existing records?** Yes, by
  construction, if Option A (§14) is used: a new nullable column added to
  an existing row never invalidates historical data, since the row already
  has its free-text value and simply wouldn't have the new code populated
  retroactively — no migration of historical text required.

## 6. LOINC assessment

Mapping the requested pipeline to actual entities, confirmed by direct
schema reading (not assumed from the diagram in the prompt):

```
Lab Order        → orders (order_type = 'LAB')             [TEXT description]
     ↓
Lab Test         → same orders.description                  [same free-text field — no separate "test" entity]
     ↓
Lab Parameter    → order_results.parameter                   [TEXT]
     ↓
Result           → order_results.result_value                [TEXT, not numeric-typed]
     ↓
Unit             → order_results.unit                         [TEXT, nullable]
     ↓
Reference Range  → order_results.reference_range              [TEXT]
     ↓
Interpretation   → order_results.is_abnormal / is_critical    [two BOOLEANs, not a coded value]
```

**Re-checking Phase 1's finding** ("lab parameters and observations
currently have free-text / different representations") **against the actual
tables**:

- **Duplicate observation models?** Confirmed: `vitals` (typed columns) and
  `order_results` (generic parameter/value row) are genuinely two different
  shapes for "an observation about a patient," for a defensible reason —
  vitals are a fixed, always-recorded-together set; lab/radiology results
  are open-ended and order-specific. **Not a bug, a real design tradeoff.**
  A future `Observation` FHIR mapping layer needs to handle both shapes
  (fan `vitals` out to N resources, map `order_results` rows more directly)
  — real complexity, correctly not merged by this audit.
- **Separate order-result models?** No — confirmed there is exactly one
  `order_results` table shared by every `order_type` (LAB, RADIOLOGY,
  PROCEDURE, SERVICE all write to the same table). This part is already
  unified, contrary to what "duplicate models" might suggest — the Order
  Spine principle (`docs/architecture/ORDER_SPINE.md`) is holding here.
- **Duplicated test definitions?** No test/service catalog exists at all
  (confirmed, §3 #8) — there's nothing to duplicate because there's no
  master list in the first place. This is a "doesn't exist" gap, not a
  "exists twice" gap.

**Not merged in this phase**, per instruction — documented only.

## 7. UCUM assessment

| Question | Finding |
|---|---|
| Where are units stored? | `order_results.unit` (`TEXT`, nullable). Vitals: nowhere as data — implied by column name only (`weight_kg`, `height_cm`, `temperature_celsius`, `bp_systolic`/`bp_diastolic` implicitly mmHg, `pulse` implicitly bpm, `spo2` implicitly %, `respiratory_rate` implicitly breaths/min) |
| Are units free text? | `order_results.unit` — yes, unconstrained `TEXT`. Vitals — units aren't text, they're not stored as a value *at all* (a developer/UI has to know the convention) |
| Are units hard-coded? | Vitals' units are effectively hard-coded *in the column name*, not configurable — this is actually a stronger (if less flexible) guarantee than free text would give: `weight_kg` can never accidentally be entered as pounds, because there's no unit field to mistype |
| Are units validated? | No format/UCUM validation anywhere for `order_results.unit` — any string is accepted |
| Can the same measurement use different unit strings? | For `order_results` — yes, in principle, since it's unconstrained text (e.g. "mg/dL" vs. "mg/dl" vs. "mgdl" could all appear across different entries with no normalization). `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` whether this has actually happened in practice (no data was inspected, only schema) |
| Does unit conversion exist? | Not found anywhere — no conversion function/table in `app/services/` |
| Do historical records retain their original unit? | Yes, trivially, for `order_results` (the `unit` value is stored per-row, so it's never lost even if a later row uses a different string for the same concept). For vitals, "retaining the original unit" isn't a meaningful question — the unit was never a data field, it's fixed by the column's own definition, so there's nothing to lose or drift. |

**Assessment**: vitals need a UCUM binding added as new (currently
nonexistent) metadata, not a fix to a broken existing field. `order_results.
unit` needs validation/normalization against a real UCUM code list — the
column exists, it's just uncoded.

## 8. ICD assessment

The prompt's own important distinction — clinical terminology (SNOMED CT)
vs. classification/reporting (ICD) — **cannot currently be evaluated against
separate real requirements, because there is only one diagnosis field.**
`consultations.diagnosis` is used for the clinical record; there is no
separate billing-diagnosis, reporting-diagnosis, discharge-diagnosis, or
insurance-diagnosis field anywhere in the schema (confirmed: `invoices`/
`charges` have no diagnosis-linked column at all — billing is charge-
source-linked via `source_type`/`source_order_id`/`source_dispense_id`, not
diagnosis-linked). **These are not "currently mixed together" — they simply
don't exist as separate concepts yet, because nothing downstream of
diagnosis (billing, reporting) currently reads it at all.**

This matters for scoping: introducing ICD coding has no existing consumer
to serve today (no insurance claim, no reporting export reads `diagnosis`
anywhere in the code — confirmed by grep, `consultations.diagnosis` is
written by the consultation-save endpoint and read only by the Patient 360
timeline and the print views). A real ICD requirement would need to come
from a concrete future need (an insurance claim process, a regulatory
report) — not introduced speculatively "because ICD exists," per this
phase's own repeated instruction.

## 9. Medication terminology assessment

| Field | Table | Free text? | Master-data-based? | Internally coded? | Externally coded? |
|---|---|---|---|---|---|
| `medicine_name` | `prescription_items` | Yes | No | No | No |
| `generic_name` | `prescription_items` | Yes (nullable) | No | No | No |
| `dosage` | `prescription_items` | Yes | No | No | No |
| `route` | `prescription_items` | Yes | No | No | No |
| `frequency` | `prescription_items` | Yes | No | No | No |
| `duration` | `prescription_items` | Yes | No | No | No |
| `medicine_name` | `pharmacy_stock` | Yes | No | No | No |

**The real finding**: these are two *independent* free-text fields for the
same real-world concept ("this medicine"), confirmed by reading
`app/services/pharmacy_services.py` directly — `list_pharmacy_stock_
service` matches stock by `medicine_name ILIKE '%...%'` (fuzzy text search,
not an exact/coded match), and `record_dispense_service` takes an explicit
`pharmacy_stock_id` parameter that the pharmacist chooses after searching —
**a human bridges "what the doctor typed" and "what's in stock," the
software does not.** This works today because a pharmacist reads both
strings and uses judgment; it would silently break (or require the same
human bridge forever) if either side were coded without the other.

**Per instruction, this audit does not recommend RxNorm or any other
specific medication terminology.** The Indian hospital context this system
targets would need its own evaluation (RxNorm is US-centric; India doesn't
have a single universally-adopted equivalent library the way it does for
SNOMED CT/ICD via national programs) — that evaluation is explicitly out of
scope for this audit and should not be assumed.

## 10. Existing master data

| Concept | Table | Source of truth | Who can modify | Historical records reference IDs? | Deletable? | Versioned? |
|---|---|---|---|---|---|---|
| Departments | `departments` | itself | ADMIN (via `app/api/departments.py`) | Yes — `doctor_departments.department_id` FK | Soft (`active` flag), not hard-deleted — confirmed no DELETE statement found for this table in the services layer | No |
| Specialties | **None** — `doctors.specialization` is free text, not a master table | n/a | n/a (free text per doctor) | n/a | n/a | n/a |
| Appointment Types | `appointment_types` | itself | ADMIN | Yes — `appointments.appointment_type_id` FK | Soft (`active`) | No |
| Lab Tests | **None** | n/a | n/a | n/a | n/a | n/a |
| Lab Parameters | **None** | n/a | n/a | n/a | n/a | n/a |
| Medications | **None** — see §9, two independent free-text fields, neither is a master table | n/a | n/a | n/a | n/a | n/a |
| Procedures | **None** | n/a | n/a | n/a | n/a | n/a |
| Units | **None** | n/a | n/a | n/a | n/a | n/a |
| Diagnosis | **None** | n/a | n/a | n/a | n/a | n/a |
| Allergy | **None** (the allergen *value* has no master list; `patient_allergies` itself is a real per-patient table, not a master/reference table of possible allergens) | n/a | n/a | n/a | n/a | n/a |

**Assessment**: exactly two real master/reference tables exist in the
entire clinical domain (`departments`, `appointment_types`) — both
scheduling/organizational, neither clinical. Every clinical concept
(diagnosis, allergy, medication, lab test, procedure, unit) has **zero**
master data — confirmed, not assumed, by the absence of any matching
`CREATE TABLE` across all migrations. This is consistent with, and explains,
why every one of those fields is free text: there is nothing to select from.

**Why this matters for historical-data safety** (§11): since none of these
concepts have a master table today, introducing one is purely additive —
there's no existing master-data table to migrate, version, or reconcile
against. The risk profile is "add a new empty catalog and start
referencing it going forward," not "migrate an existing catalog."

## 11. Historical data compatibility strategy

For every structured-concept candidate in §3/§4, the same shape applies and
is evaluated, not implemented:

```
Existing free text (kept, unchanged)
          +
Optional standardized code (new, nullable, populated only going forward)
```

Concretely: `consultations.diagnosis` stays exactly as it is; a new
nullable column (e.g. `diagnosis_code`, `diagnosis_code_system`,
`diagnosis_code_display` — see §14 for the storage-shape options) would be
added alongside it. Every historical row simply has `NULL` in the new
column(s) — not wrong, not requiring backfill, just "coded diagnosis wasn't
captured for this visit." This is the same pattern the codebase already
uses successfully for additive migrations throughout its history (per
`docs/OPD_HIMS_MASTER_SPEC_AUDIT.md`'s own confirmation: "every migration
this session wrote was additive... zero destructive migrations").

**This audit does not recommend rewriting/backfilling historical free text
into codes** — that would require either manual clinical review of every
past record (expensive, error-prone, and arguably falsifies the historical
record by attributing a code a clinician never actually selected) or an
automated NLP-based mapping (a real capability, but a separate, much larger
project with its own accuracy/liability considerations, not assumed as
part of this phase).

## 12. API / backward compatibility assessment

Current response/request shapes that would be affected (all confirmed by
reading the actual Pydantic models/response-building code referenced in
`docs/architecture/*` and `docs/workflows/*`, not re-read line-by-line in
this pass — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` for the exact
Pydantic field names if/when this is actually implemented):

- `app/api/clinical.py`'s consultation create/update/read: `diagnosis:
  string` today.
- `app/api/patients.py`'s allergy endpoints: `allergen: string` today.
- `app/api/orders.py`'s order/result endpoints: `description: string`,
  `parameter: string`, `unit: string` today.
- `app/api/pharmacy.py`'s prescription endpoints: `medicine_name: string`
  today.

**Impact of eventually adding `{code, system, display}` alongside each**:
additive at the API contract level if done as new, optional response
fields (e.g. `diagnosis: string` stays, a new optional `diagnosisCoding:
{code, system, display} | null` is added beside it) — existing frontend
code reading `diagnosis` as a string continues to work unchanged; new UI
that wants to show/search by code reads the new field. This is the standard
non-breaking API-evolution pattern and matches how `app/error_handling.py`
already added the `{success, errorCode, message, details}` envelope
alongside the pre-existing `detail` field without breaking any existing
caller (confirmed working precedent, `docs/product/PRODUCT_VISION.md`).

**Not implemented or even drafted as a real schema in this pass** — this is
a compatibility risk assessment only, per instruction.

## 13. Terminology architecture (proposed, not built)

Adapted from the prompt's own diagram to what this repository actually
has evidence for needing:

```
                    Terminology Layer (future)
                              │
           ┌──────────────────┼──────────────────┐
           │                  │                  │
      SNOMED CT             LOINC               ICD
   (diagnosis,          (lab parameters,    (only if a real
    allergen,             units via UCUM)     billing/reporting
    procedure)                                 consumer exists —
           │                  │                 see §8)
           └──────────────────┼──────────────────┘
                              │
                            UCUM
                    (vitals units [new],
                     order_results.unit [existing, uncoded])
                              │
                 Internal Clinical Data Model
          (consultations.diagnosis, patient_allergies.allergen,
           order_results.parameter/.unit, prescription_items.medicine_name
           — each keeps its existing free-text column, unchanged)
```

**Explicitly not proposed**: a medication terminology layer (RxNorm or
otherwise) as part of this diagram — §9 found a real, structural gap
(prescribing vs. stock as two disconnected free-text fields) that a
terminology *code* wouldn't fix on its own; the more urgent, more tractable
fix there is a shared internal medication master table (linking the two
existing free-text fields to one row), which is a data-modeling change, not
a terminology-adoption one — worth sequencing before or independent of
RxNorm, not assumed to need RxNorm to solve.

**Do not build this merely because the diagram looks good** (per
instruction) — §16/§19 govern what's next.

## 14. Terminology storage strategy — options assessed, none chosen

**Option A — store terminology directly on the clinical record** (e.g.
`consultations.diagnosis_code`/`.diagnosis_code_system`/`.diagnosis_code_
display` as three new nullable columns):

- *Pros*: simplest to implement given the current schema shape (one row
  already represents one diagnosis-per-visit fact); trivially satisfies
  §11's historical-compatibility requirement (new nullable columns); no new
  table, no new join, no new failure mode.
- *Cons*: if a future encounter genuinely needs *multiple* coded diagnoses
  (very plausible clinically — a visit often has more than one diagnosis),
  this option doesn't scale without either repeating columns or moving to a
  child table anyway.

**Option B — internal master IDs with terminology mappings** (e.g. a new
`diagnoses` master table with its own `id` + SNOMED code, and `consultations`
gains a `diagnosis_id` FK, while keeping the free-text `diagnosis` column
too):

- *Pros*: supports multiple coded diagnoses per visit cleanly (a join
  table `consultation_diagnoses(consultation_id, diagnosis_id)`); the
  master table becomes reusable master data (§10) with real versioning
  potential; matches the shape `patient_allergies` already proved works
  well for "one row per fact, not a blob" (its own migration comment).
- *Cons*: real new schema surface (at least two new tables for diagnosis
  alone, more if applied to allergy/medication/lab-parameter too); a
  genuinely bigger, if more correct, change.

**Option C — a dedicated external terminology service** (a separate
process/database that owns the full SNOMED CT/LOINC/ICD code systems, the
application queries it for lookup/validation/autocomplete):

- *Pros*: the only option that handles full terminology versioning,
  hierarchy queries (e.g. "is this diagnosis a subtype of diabetes"), and
  keeping up with periodic code-system updates without re-deploying the
  main application.
- *Cons*: real new infrastructure (a service, likely its own database,
  a data-loading/update pipeline for the code systems themselves) — the
  kind of complexity `CLAUDE.md`'s "do not over-engineer prematurely"
  principle and this prompt's own "unnecessary microservices" warning
  both caution against introducing without a concrete need.

**Assessment for this application's current scale**: **Option A is
sufficient and appropriate to start with**, for the reasons the historical-
compatibility section (§11) and the current single-tenant, moderate-volume
scale (`docs/architecture/MODULE_ARCHITECTURE.md`'s own framing) both
support — it's the smallest change that closes the most-cited gap
(diagnosis/allergen as free text) without new infrastructure. **Option B is
the more architecturally correct answer specifically for diagnosis** once
multi-diagnosis-per-visit becomes a real, asked-for requirement — not
assumed to be needed yet, since today's schema only supports one diagnosis
string per consultation regardless of terminology. **Option C is not
warranted at the current scale** — there's no evidence in this repository
of the multi-hospital, high-terminology-query-volume, frequent-code-update
scenario that would justify a dedicated service, and adopting it now would
be exactly the premature complexity this phase's own instructions warn
against ("Do not assume the most complex option is the best option").

This is an assessment, not a decision — the actual choice should be made
explicitly when Phase 4-equivalent implementation is scoped, informed by
which concept (diagnosis vs. allergy vs. lab parameter) is tackled first,
since they may not all want the same option.

## 15. Clinical safety finding (re-confirmed)

Re-verified for this phase, same method as Phase 1 (direct read of
`app/services/pharmacy_services.py`): **prescribing still does not check
`patient_allergies` against a medicine being prescribed.** No new call site
was introduced since Phase 1 (there have been no code changes between these
audits) — this is a re-confirmation, not a new finding, but the instruction
asked for it to be re-verified rather than assumed still true, so it was.

> **Clinical Safety Priority — Independent of Terminology Implementation.**
> This does not require SNOMED CT, LOINC, or any terminology work to fix at
> a basic level — a substring/exact-text match between `patient_allergies.
> allergen` and `prescription_items.medicine_name`/`.generic_name` would
> already catch the clearest cases (e.g. a documented "Penicillin" allergy
> against a prescription for "Penicillin" or "Amoxicillin" if the generic-
> name convention is followed). **Not implemented in this phase**, per
> instruction.

**Would the existing allergy representation support a future standardized
allergy concept?** Yes, well — `patient_allergies` already has the right
*shape* (one row per allergy, `severity` already enumerated, active/resolved
lifecycle already real) for Option A (§14) to apply directly: add
`allergen_code`/`allergen_code_system`/`allergen_code_display` alongside
the existing `allergen` text column, exactly the same pattern as diagnosis.
No structural rework of `patient_allergies` itself would be needed first.

---

## 16-17. No implementation / documentation

Confirmed: no SNOMED CT, LOINC, ICD, UCUM, or RxNorm code was added; no
terminology table was created; no existing clinical table, API, or UI
component was modified; no historical data was migrated; no terminology
server was implemented. This document itself, at `docs/OPD_HIMS_
TERMINOLOGY_AUDIT.md`, is the only artifact this phase produced.

## 18. Recommended future implementation sequence

Not a commitment, an ordering suggestion based on what this audit found to
be smallest-and-most-valuable first:

1. **Allergy terminology binding** (Option A, §14) — smallest surface
   (`patient_allergies` alone), highest safety payoff when paired with the
   independently-flagged prescribing check (§15), and the table shape
   already fits Option A with zero rework.
2. **Medication master data** (a shared table both `prescription_items`
   and `pharmacy_stock` can reference) — not a terminology-standard
   question at all (§9's real finding is a data-modeling gap, not a
   missing external code system); worth doing before or independent of any
   medication terminology decision.
3. **Diagnosis terminology binding** — Option A first; revisit Option B if
   multi-diagnosis-per-visit becomes a real requirement.
4. **Lab parameter/unit binding (LOINC + UCUM)** — larger surface
   (`order_results` is shared across every order type), and directly
   unblocks a cleaner future `Observation` FHIR mapping (`docs/OPD_HIMS_
   CANONICAL_MODEL_AUDIT.md` §7).
5. **Vitals UCUM binding** — smaller, more mechanical (units are
   implicit today; making them explicit is additive metadata, not a
   redesign).
6. **ICD** — only once a real downstream consumer exists (a billing/
   reporting requirement) — §8 found none today; do not build speculatively.

## Point-by-point verification statement

Every one of the 25 domains in §3 has an individual evidence citation (a
migration filename, a specific grep result, or a direct code read) and an
individual gap statement — none were summarized as a blanket "terminology
is currently free text." Six items are explicitly marked `TODO — VERIFY
AGAINST EXISTING IMPLEMENTATION` where this pass's evidence was
insufficient to state something with confidence (medication form/strength
capture convention, `vitals.chief_complaint` vs. `consultations.
chief_complaint` relationship, `doctors.specialization` vs. `departments`
consistency enforcement, unit-string drift in practice, exact Pydantic
field names for §12, and the exact allergy-entry UI screen).

**Nothing was implemented. Nothing was assumed where evidence was
insufficient.**

## Recommended next step

Per the master prompt's sequence, Phase 3 would be the FHIR Foundation —
but per this audit's own §18, the clinical-safety finding in §15 remains
the one item repeatedly flagged (Phase 0, Phase 1, and now Phase 2) as
worth considering independently of strict phase order, since it needs
none of the terminology or FHIR work ahead of it to deliver value.

**STOP. Do not implement Phase 3 (or the terminology sequence in §18)
without explicit confirmation.**
