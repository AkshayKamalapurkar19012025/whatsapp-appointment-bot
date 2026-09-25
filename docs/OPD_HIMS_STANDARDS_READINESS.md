# HospitalOS — Phase 3: Standards-Readiness Design & Minimal Domain Hardening

**Design deliverable. No schema, API, or UI was changed to produce this
document.** This phase synthesizes and extends the evidence already
gathered in Phases 0-2 (`docs/OPD_HIMS_INTEROPERABILITY_AUDIT.md`,
`docs/OPD_HIMS_CANONICAL_MODEL_AUDIT.md`, `docs/OPD_HIMS_TERMINOLOGY_
AUDIT.md`) into concrete, minimal proposed changes — but proposes only,
per this phase's explicit stop condition. Two targeted verification checks
were run to confirm nothing changed since Phase 2 (no multi-diagnosis
structure exists, no `onset`/`clinical_status` columns exist anywhere,
current migration head is `0053`) — the audits themselves were not redone.

---

## 1. Executive summary

Three phases of evidence converge on the same shape of answer: **this
domain model needs a small number of targeted, additive changes, not a
redesign.** The encounter-centric spine, patient identity discipline, audit
trail, break-glass access, and typed vital signs are already standards-
adjacent and should not be touched. Exactly four areas have a *real*,
evidence-backed domain-integrity problem worth fixing before FHIR work
starts: diagnosis (single free-text field, no code slot, no multi-diagnosis
support), allergy (good shape, missing a code slot), medication (two
disconnected free-text representations — a data-modeling gap, not a
terminology gap), and the allergy-vs-prescribing clinical safety check
(P0, independent of all of the above). Every proposed change below is
additive (new nullable columns/tables), preserves every existing free-text
value unchanged, and requires no rewrite of historical data.

**What this phase explicitly does not propose**: touching `patients.
whatsapp_number`/`patient_identifiers` right now (Option assessed, decision
deferred — §4), forcing `vitals` and `order_results` into one shape (kept
distinct, per instruction and per their own real differences), any external
terminology adoption decision (RxNorm/SNOMED/LOINC/ICD — code *slots* are
proposed, not populated), or any new infrastructure (terminology server,
FHIR server, message broker, separate database, microservice).

---

## 2. Existing strengths (re-confirmed, not re-litigated)

From Phase 1/2, re-cited here because §16 (Preserve As-Is) depends on
naming them precisely: UHID (deterministic, never mutated/reused),
`patient_identifiers` (real, extensible, already widened once), duplicate
detection (`pg_trgm` + 4 signals), patient merge (reversible, fully
audited), break-glass (`break_glass_grants` — reason, expiry, audit,
explicit denylist), `audit_log` (append-only, on every gated mutation),
`consultation_amendments` (real before/after provenance for one entity),
typed `vitals` (range-`CHECK`-constrained, not free text), the Order Spine
(one `orders`/`order_results` shape for LAB/RADIOLOGY/PROCEDURE/SERVICE/
EXTERNAL_REFERRAL), and every existing DB-constrained status/priority enum.

---

## 3. Required domain changes (overview — detail in §4-9)

Four areas, evidence-backed, in priority order (full classification in
§14): allergy-vs-prescribing check (P0), medication master data (P1),
diagnosis structure (P1), patient-identifier consolidation (P1, decision
only — no change proposed yet), allergy/diagnosis/lab terminology code
slots (P2).

---

## 4. Patient identity strategy

### A. Does anything need to change to support a future ABHA identifier?

**No structural change required.** `patient_identifiers.kind` is a `TEXT
CHECK` constraint currently allowing `('PHONE', 'GOVT_ID')` (migration
`0030`, widened once from a `0029` baseline of `PHONE`-only). Adding
`'ABHA'` as a third value is the same one-line `CHECK` widening the
codebase already did once. **Evidence exists this is low-risk**: it was
already done, successfully, with no reported regression, per the
codebase's own migration history. No proposal to change this now — it's
already suitable, confirmed by re-reading the actual constraint, not
assumed.

### B. Patient identifier duplication — assessment only, no decision made

**Current**: `patients.whatsapp_number` (the real, `UNIQUE`, actually-
queried lookup key — every phone-based patient resolution, including the
WhatsApp bot's, reads this column) and `patient_identifiers` (dual-written,
`kind='PHONE'`, explicitly "not yet the source of truth for anything" per
its own migration comment).

**Should this become**: `patient_identifiers` as the single source of
truth, `patients.whatsapp_number` deprecated? Assessed, not decided:

- **Migration complexity**: Moderate-to-high, *not* low. `whatsapp_number`
  has a real `UNIQUE` constraint several code paths depend on for
  correctness (not just convenience) — `app/api/scheduling.py`'s
  `get_patient()` (per Phase 0's own citation of this exact function) and
  the WhatsApp bot's session resolution both key off it directly.
- **Existing queries affected**: every place that does `WHERE
  whatsapp_number = %s` would need to become a `JOIN patient_identifiers
  ON kind='PHONE' AND is_primary` — a real, non-trivial rewrite of hot-path
  lookup code, not a cosmetic rename.
- **Existing APIs affected**: `app/api/patients.py`'s search/lookup
  endpoints, `app/api/booking.py` (the WhatsApp flow), `app/api/patient_
  auth.py` (OTP login resolves a patient by phone number) — three
  independent subsystems, all currently correct against the `whatsapp_
  number` column specifically.
- **Existing UI affected**: none directly (the frontend never queries this
  column by name — it goes through the API), so this is a backend-only
  concern.
- **Backward compatibility**: `patient_identifiers` rows already exist for
  every patient created since migration `0029` (dual-write), but any
  patient created *before* that migration would need a backfill to have a
  `patient_identifiers` row at all — `TODO — VERIFY AGAINST EXISTING
  IMPLEMENTATION` whether migration `0029` itself backfilled historical
  rows or only started dual-writing going forward; this specific detail
  was not re-confirmed in this pass and matters for whether a cutover is
  even safe today without a backfill step first.

**Recommendation**: **do not cut over now.** The dual-write already gives
future flexibility (a consolidation can happen later without losing data,
since every new patient has both representations from `0029` onward)
without forcing a risky rewrite of three subsystems' hot-path lookup code
today. This is explicitly a P1 "domain integrity" item to revisit once the
backfill question above is answered — not a P0 blocker, since ABHA doesn't
require the cutover (per §4.A above).

---

## 5. Medication strategy

> **Update (Phase 5 of the interoperability master prompt): implemented.**
> See `docs/workflows/PHARMACY.md`'s "Medication Master" section for the
> actual, verified behavior, `migrations/0054_medication_master.sql`/
> `migrations/0055_medications_hospital_id.sql` for the schema,
> `app/services/medication_services.py` for the service layer, and
> `tests/test_medication_master.py` for the test coverage. The design
> below is kept as the historical record of what was approved before
> implementation — the table name shipped as `medications` (plural,
> matching this codebase's existing table-naming convention) rather than
> the singular `medication` sketched below, `strength`/`dosage_form`
> stayed free text exactly as proposed, and the backfill strategy was
> tightened to *exact* case/whitespace-normalized text matches only
> (never near-duplicate/fuzzy merging, and never guessing on an ambiguous
> match) — see that migration's own comments for why. Everything else
> below matches what was built, including deliberately not adding the
> `terminology_code`/`.system`/`.display` columns.

### Current (from Phase 2, re-confirmed)

`prescription_items.medicine_name`/`.generic_name` (free text) and
`pharmacy_stock.medicine_name` (free text, independent) — no FK, bridged
only by a pharmacist reading both strings and fuzzy `ILIKE` search.

### Problem

Not a terminology gap — a **domain-modeling** gap. The same real-world
medicine has two independent spellings-of-record with nothing tying them
together, so neither analytics ("how much of drug X did we prescribe this
month") nor safety checking (§7) can reliably treat them as the same thing.

### Proposed minimal model (design only)

```
Medication  (new table)
    │
    ├── id                    (internal PK — the stable reference point)
    ├── generic_name          (required — the clinically meaningful name)
    ├── brand_name             (nullable)
    ├── strength               (nullable, e.g. "500mg" — free text initially,
    │                           not a parsed numeric+unit pair; see §9 for
    │                           why this stays simple for now)
    ├── dosage_form            (nullable, e.g. "Tablet" — free text initially,
    │                           same reasoning)
    ├── default_route          (nullable — a sensible default, not a constraint
    │                           on what a prescriber can choose per-item)
    ├── active                 (soft-delete flag, matching every other master
    │                           table's convention — departments,
    │                           appointment_types)
    ├── created_by / created_at / updated_at
    └── (future) terminology_code / terminology_system / terminology_display
                                (nullable, unpopulated until a real RxNorm-or-
                                 equivalent decision is made — not proposed now)
```

`prescription_items` gains a nullable `medication_id BIGINT REFERENCES
medication(id)`, **alongside** the existing `medicine_name`/`generic_name`
text columns, not replacing them. `pharmacy_stock` gains the same nullable
`medication_id` FK, alongside its own existing `medicine_name`.

### Required fields

`generic_name` only (matching how `patient_allergies.allergen` and
`consultations.diagnosis` are each single required text fields today — this
is consistent with the codebase's existing minimalism, not a new pattern).

### Existing data migration strategy

**Additive, not a cutover.** New table starts empty. Existing
`prescription_items`/`pharmacy_stock` rows keep `medication_id = NULL`
forever unless explicitly backfilled — no historical prescription becomes
invalid or unreadable. A backfill (matching existing free-text names to new
`Medication` rows, likely requiring human review for near-duplicates like
"Paracetamol" vs. "Panadol") is a separate, later, optional step — not
part of this design.

### Prescription / pharmacy stock / dispensing relationship

`prescription_items.medication_id` and `pharmacy_stock.medication_id`
being nullable, both-optional FKs to the *same* table is what actually
closes the Phase 2 gap: once both are populated for a given medicine, a
dispense screen can query "stock for this medication" by ID instead of
fuzzy name matching — but the fuzzy-matching path continues to work
unchanged for any medicine not yet in the master table, so nothing breaks
on day one.

### Search behavior

Existing `ILIKE` fuzzy search over `pharmacy_stock.medicine_name`/
`prescription_items.medicine_name` continues to work exactly as today —
this is additive, not a replacement of the existing search UX
(`PharmacyPanel.tsx`, `PrescriptionPanel.tsx` need no immediate change).

### Historical prescription behavior

Unaffected. A historical `prescription_items` row with `medication_id =
NULL` displays exactly as it does today (reading its own free-text
columns) — nothing about rendering an old prescription depends on the new
table existing.

### Explicitly not proposed

RxNorm or any other external medication terminology adoption — per
instruction, and because Phase 2 found no concrete Indian-context standard
already evaluated. The `terminology_code`/`.system`/`.display` columns
above are named to show *where* that would eventually slot in, not to
implement it now.

---

## 6. Diagnosis strategy

> **Update (Phase 6 of the interoperability master prompt): Stage 1
> implemented.** See `docs/workflows/CONSULTATION.md`'s "Diagnosis
> coding" section for the actual, verified behavior,
> `migrations/0056_consultation_diagnosis_coding.sql` for the schema,
> and `tests/test_clinical.py`/`tests/test_consultation_amendments.py`
> for the test coverage. The design below is kept as the historical
> record of what was approved before implementation — it matches what
> was built, with one naming note: the shipped columns are
> `diagnosis_code_system`/`diagnosis_code`/`diagnosis_code_display`
> (system before code, so the API's cross-field validator can read
> `diagnosis_code_system` from already-validated field data when
> checking `diagnosis_code` — a Pydantic v2 field-declaration-order
> mechanic, not a design change) rather than the `diagnosis_code`/
> `diagnosis_code_system`/`diagnosis_code_display` order sketched below.
> Re-verified against the repository in Phase 6: still exactly one
> `consultations` row per encounter, still exactly one diagnosis field,
> still no existing UI/workflow/report anywhere assumes more than one —
> **Stage 2 (the `conditions` table below) remains not built, and this
> phase found no new evidence that it should be.** The coding fields are
> deliberately backend-only this phase — not exposed in
> `ConsultationWorkspace.tsx` — since no terminology catalog exists for
> a clinician to actually pick from (see `docs/workflows/CONSULTATION.md`
> for the full reasoning).

### Current

`consultations.diagnosis` — one `TEXT` field per consultation, no
structure, no multiplicity.

### Understanding the actual existing workflow first (per instruction)

`consultations` is `encounter_id BIGINT ... UNIQUE REFERENCES encounters
(id)` — **one consultation per encounter**, confirmed in Phase 1. Today, a
doctor writes one diagnosis string per visit. There is no existing UI
affordance for "add a second diagnosis" (`ConsultationWorkspace.tsx`'s
Consultation tab has one diagnosis field, per Phase 1's screen-map work) —
so multi-diagnosis is a **new capability**, not a structural fix to
something already half-supported.

### Proposed minimal model (design only)

**Two-stage proposal, not one leap**, because the evidence supports the
first stage confidently and the second only conditionally:

**Stage 1 (P1, evidence-backed now)** — keep one diagnosis per consultation
structurally, but give it a code slot, preserving the human-readable value
exactly as instructed:

```
consultations gains (nullable, additive):
    diagnosis_code            TEXT
    diagnosis_code_system     TEXT   (e.g. 'SNOMED-CT' — a label, not yet validated)
    diagnosis_code_display    TEXT   (the terminology's own canonical display string)

-- diagnosis (existing TEXT column) is UNCHANGED and remains the
-- authoritative human-readable value; the code fields are a parallel,
-- optional annotation, exactly per instruction #3 ("Do NOT remove the
-- human-readable diagnosis... preserve display alongside any standardized code").
```

**Stage 2 (P2, conditional — not proposed for immediate implementation)** —
a `conditions` table (`consultation_id`, `code`/`system`/`display`, `is_
primary BOOLEAN`, `status`, `onset_date`) supporting genuine multiple
diagnoses with a primary/secondary distinction, mirroring the "one row per
fact" pattern `patient_allergies` already proved out. **This audit does not
recommend building Stage 2 yet** — there is no existing evidence (no UI
affordance, no workflow doc, no user request in any prior phase) that
multiple diagnoses per visit is a real, asked-for requirement today. Stage
1 alone already closes the terminology-readiness gap Phase 2 identified;
Stage 2 should wait for a concrete requirement, per this phase's own
"smallest, safest change" framing.

### Does diagnosis need onset / status / clinical notes?

**Onset**: not evidenced as a current need — no existing field or workflow
step captures "when did symptoms start" as structured data (`chief_
complaint` is free text and may contain this informally). Not proposed.
**Status** (active/resolved/remission): not evidenced — a `COMPLETED`
consultation's diagnosis is not currently tracked as ongoing-vs-resolved
across future visits (each consultation is independent). Not proposed —
would require a cross-encounter diagnosis-history concept that doesn't
exist today and isn't asked for by this phase. **Clinical notes**: already
covered by the existing separate `clinical_notes` field on `consultations`
— no new field needed.

---

## 7. Allergy strategy

### Current (from Phase 1/2)

`patient_allergies` — real table, `allergen` (free text), `severity`
(already a `CHECK` enum: `MILD`/`MODERATE`/`SEVERE`), `active`/`resolved_*`
lifecycle (already real and controlled-void, matching charges/payments'
own pattern).

### Proposed minimal model (design only)

The requested `Patient → Allergy → Substance → Reaction → Severity →
Status` shape, evaluated against what already exists:

- **Patient → Allergy**: already real (`patient_allergies.patient_id` FK).
- **Allergy → Substance**: **this is the one genuine gap** — `allergen` is
  a bare string with no substance identity of its own. Proposed: add
  nullable `allergen_code`/`allergen_code_system`/`allergen_code_display`
  directly on `patient_allergies` (Option A from Phase 2 §14 — not a
  separate `substances` master table, since Phase 2 found no evidence a
  reusable substance catalog is needed beyond what one nullable code slot
  per allergy row provides).
- **Allergy → Reaction**: already real (`patient_allergies.reaction`, free
  text — appropriately narrative, per Phase 2 §4's distinction; not
  proposed to change).
- **Severity**: already real, already an enum. Not proposed to change.
- **Status**: already real (`active`/`resolved_by`/`resolved_reason`/
  `resolved_at`). Not proposed to change.

**This is deliberately the smallest proposal in this document** — three
new nullable columns on an already-correct table, nothing else. Avoids the
"unnecessary complexity" the instruction explicitly warns against for this
section.

### Designing for the future prescribing check without a second redesign

The three-column addition above is sufficient: a future allergy-check
service (§17) can match on `allergen` (free text, today) or, once
populated, on `allergen_code` (exact-code match, more reliable) — adding
the check later needs no further schema change to `patient_allergies`
beyond what's proposed here.

---

## 8. Laboratory strategy

### Current (from Phase 2 §6, re-confirmed)

```
Lab Order   → orders (order_type='LAB')          [free-text description]
Lab Test    → same free-text field (no separate Test entity)
Lab Result  → order_results.result_value          [TEXT, not numeric]
Observation → order_results row itself
```

### Can existing tables support the target structure without merging unrelated concepts?

**Yes — mostly already does, with one real gap.** The Order Spine
(`orders`/`order_results`) already separates Order from Result cleanly.
The one missing link is "Lab Test" as its own concept distinct from "Lab
Order" — today an order's free-text `description` conflates "what was
ordered" (e.g. "CBC") with the order event itself. This audit does **not**
propose a `lab_tests` master-catalog table now — Phase 2 found no evidence
of a real, asked-for catalog requirement (no UI affordance for searching a
test catalog exists anywhere), and inventing one speculatively would be
exactly the premature complexity §12 warns against. If a future phase adds
one, it should follow the same "new nullable `test_id` FK, existing free-
text `description` unchanged" pattern as every other proposal in this
document — not designed further here without that concrete trigger.

### Do NOT force vitals into this model

**Confirmed, not proposed.** `vitals` and `order_results` remain two
distinct tables. Reasoning re-stated from Phase 2: vitals are a fixed,
always-recorded-together set (nine specific measurements, one row per
triage event); lab/radiology results are open-ended and order-specific (an
unbounded set of possible parameters, one row per parameter per order).
Merging them would force one of these two real shapes to distort to fit
the other, for no workflow benefit — `ConsultationWorkspace.tsx`'s Triage
tab and Orders tab are already, correctly, two different UI surfaces over
two different tables, and should stay that way.

---

## 9. Observation / vitals & unit strategy (UCUM readiness)

### Current

Vitals: units implicit in column naming (`weight_kg`, `temperature_
celsius`), never stored as data. `order_results.unit`: stored, free text,
unvalidated.

### Proposed: metadata, not new columns per row

Per the instruction's own framing ("existing units can remain represented
by metadata/configuration rather than adding unnecessary columns
everywhere"), the minimal change is **not** adding a `weight_unit`/
`height_unit`/etc. column to every `vitals` row (which would be redundant —
every row would always say "kg" since the column name already fixes it,
adding no real information, only storage overhead and a place for drift
between the column's fixed meaning and a per-row value that should never
differ from it).

Instead, propose a small **application-level constant/config mapping**
(not a new table, not a schema change) — e.g. a single Python dict in
`app/services/` naming each vitals field's fixed UCUM code:

```python
VITALS_UNITS = {
    "weight_kg": {"code": "kg", "system": "UCUM", "display": "kilogram"},
    "height_cm": {"code": "cm", "system": "UCUM", "display": "centimeter"},
    "temperature_celsius": {"code": "Cel", "system": "UCUM", "display": "degree Celsius"},
    "bp_systolic": {"code": "mm[Hg]", "system": "UCUM", "display": "millimeter of mercury"},
    # ...
}
```

This is a **code-only change** (not a schema/API/UI change, so strictly
speaking outside even this phase's "design, don't implement" scope — noted
here only as the recommended shape for when it is picked up) — it makes
UCUM codes available to a future FHIR mapping layer (fan `vitals` out to N
`Observation` resources, per Phase 1 §7's own mapping note) without
touching the `vitals` table at all. This is the smallest possible "future
UCUM compatibility" step and matches the instruction's explicit preference
against unnecessary schema complexity.

`order_results.unit` is different — it's genuinely per-row variable data
(different orders measure different things with different units), so it
correctly needs the code-slot treatment other free-text fields get:
proposed nullable `unit_code`/`unit_system` columns alongside the existing
`unit` text column, same Option-A pattern as everywhere else in this
document.

---

## 10. Clinical statuses — mapping strategy, not replacement

Per instruction, **no existing enum is proposed to change.** The Phase 2
finding stands: `consultations.status`, `orders.status`, `prescriptions.
status`, `appointments.status`, `invoices.status`, `payments.status` are
all already DB-constrained, already correctly modeling this application's
own workflow. A future FHIR mapping layer needs a **pure translation
table** (in code, not the database) for each:

```
Internal orders.status        FHIR ServiceRequest.status
─────────────────────         ──────────────────────────
ORDERED                    →  active
IN_PROGRESS                →  active  (FHIR doesn't distinguish
                                        "ordered" from "in progress" the
                                        same way — a real, one-way lossy
                                        mapping to note, not fix)
COMPLETED                  →  completed
CANCELLED                  →  revoked
```

This table (and its four-or-so siblings, one per status enum) belongs in a
future FHIR mapping-layer module (`docs/decisions/ADR-002-ENCOUNTER-
CENTRIC-DESIGN.md`'s own "FHIR should be an interoperability representation
layer" principle, restated by this phase's own instructions) — not
proposed as a schema or API change here, since it's pure application code
that would live entirely inside whatever Phase 3-of-the-original-sequence
(FHIR Foundation) eventually builds.

---

## 11. Canonical domain boundary — existing vs. proposed

```
Patient                                    [EXISTING]
 ├── Identifiers                           [EXISTING — patient_identifiers,
 │                                          source-of-truth question open, §4.B]
 ├── Allergies                             [EXISTING — patient_allergies,
 │                                          + proposed allergen_code (§7)]
 ├── Conditions                            [PROPOSED — does not exist as a
 │                                          cross-visit concept; today only
 │                                          consultations.diagnosis per-visit]
 └── Encounters                            [EXISTING]
      │
      ├── Diagnoses (Dx)                   [EXISTING as 1 field, + proposed
      │                                     diagnosis_code (§6 Stage 1);
      │                                     multi-diagnosis is §6 Stage 2,
      │                                     NOT proposed now]
      ├── Observations                     [EXISTING — vitals (typed) +
      │                                     order_results (generic), kept
      │                                     distinct per §8]
      ├── Procedures                       [EXISTING — as one orders.order_type]
      ├── Service Requests                 [EXISTING — orders generally]
      ├── Medication Requests (Rx)         [EXISTING — prescriptions/
      │        │                            prescription_items]
      │        ▼
      │    Medication                      [PROPOSED — new master table, §5]
      │        ▼
      │    Pharmacy (stock/dispense)       [EXISTING — pharmacy_stock/
      │                                     pharmacy_dispense_records,
      │                                     + proposed medication_id FK]
      └── Documents                        [NOT IMPLEMENTED — by design,
                                             ADR-005; not proposed here]
```

---

## 12. FHIR mapping readiness (per-resource, informed by §4-10's proposals)

| Current Domain | Canonical Concept | Future FHIR Resource | Fields already available | Fields missing | Fields needing transformation | Fields that stay internal-only |
|---|---|---|---|---|---|---|
| `patients` | Patient | `Patient` | name, DOB, gender, address, identifiers | none structural | `whatsapp_number`/`patient_identifiers` → `Patient.telecom`/`.identifier` (mapping-layer job) | `merged_into_id` (internal merge bookkeeping) |
| `doctors` | Practitioner | `Practitioner` | name, specialization | license/registration number (Phase 1 gap, not addressed by this phase — no evidence of a current need) | `specialization` (free text) → `Practitioner.qualification` (would need its own code slot if ever pursued — not proposed here, out of this phase's evidenced scope) | `default_duration_minutes`/`buffer_minutes` (scheduling-only) |
| `appointments`+`encounters` | Encounter | `Encounter` | status, start/end, type | none for OPD | internal status enum → `Encounter.status` (§10) | token/queue fields |
| `consultations.diagnosis` (+ proposed code fields) | Condition | `Condition` | display text (always); code (once §6 Stage 1 ships and is populated) | multi-diagnosis (§6 Stage 2, not proposed now) | free text → `Condition.code.text`; proposed code fields → `Condition.code.coding` | `clinical_notes`/`history_notes`/`examination_notes` (narrative, stays as `Encounter` notes or similar, not `Condition`) |
| `patient_allergies` (+ proposed code fields) | AllergyIntolerance | `AllergyIntolerance` | allergen text, severity, reaction, status | none structural after §7's proposal | `severity` enum → `AllergyIntolerance.criticality`-adjacent (lossy, 3-value to FHIR's own value set — a real mapping decision, not attempted here) | none |
| `vitals` | Observation (×N) | `Observation` | every measurement, already typed | UCUM codes (§9's proposed config mapping) | one `vitals` row → N `Observation` resources (fan-out, mapping-layer job) | none |
| `order_results` | Observation | `Observation` | parameter/value/flags | LOINC code, UCUM unit code (proposed nullable columns, not built) | `is_abnormal`/`is_critical` booleans → `Observation.interpretation` (lossy — two booleans can't express High vs. Low, a real gap noted in Phase 2, not closed by any proposal here) | none |
| `orders` grouped by id | DiagnosticReport | `DiagnosticReport` | implicit (read-time grouping) | a persisted report-level status/conclusion (not proposed — no evidenced need beyond what grouping already provides) | grouped `order_results` → `DiagnosticReport.result` references (mapping-layer job) | none |
| `prescriptions`/`prescription_items` (+ proposed `medication_id`) | MedicationRequest | `MedicationRequest` | status, dosage instructions | coded medication reference (once §5 ships and is populated) | free-text medicine name → `MedicationRequest.medicationCodeableConcept.text`; `medication_id` (once populated) → a coded reference | `notes`/cancel reason (administrative) |

---

## 13. Backward compatibility (per proposal, explicit)

Every proposal in §5-9 is additive-only (new nullable columns/tables). The
following holds for **all of them, verified against the actual current
implementation, not assumed**:

- **Existing data**: unaffected. Every historical row keeps its existing
  free-text value exactly as-is; new columns default to `NULL`.
- **Existing API**: unaffected unless/until a response model is
  deliberately extended to include a new field — additive-only response
  changes (per Phase 2 §12's own precedent: `app/error_handling.py` added
  its envelope alongside the pre-existing `detail` field with zero breaking
  changes to any caller).
- **Existing frontend**: unaffected. No proposal here requires any
  `.tsx` file to change to keep working — `ConsultationWorkspace.tsx`,
  `PrescriptionPanel.tsx`, `PharmacyPanel.tsx` all continue reading/writing
  the same free-text fields they do today.
- **Existing reports**: unaffected — `BillingHistoryPanel.tsx`/
  `PaymentHistoryPanel.tsx`/`WaitingTimeAnalyticsPanel.tsx` don't read any
  of the fields proposed here.
- **Existing workflows** (OPD/consultation/prescription/pharmacy/
  laboratory/billing/printing): every workflow doc under `docs/workflows/`
  describes the current free-text fields as load-bearing; none of those
  fields are removed or renamed by any proposal in this document — every
  workflow continues to function identically, with new optional
  capabilities available only once explicitly used.

---

## 14. Migration strategy (per proposal)

All four real proposals (§5 Medication, §6 Diagnosis Stage 1, §7 Allergy,
§9 `order_results` unit codes) follow the same shape:

```
Current (free text, unchanged)
        ↓
Compatibility Layer: new nullable column(s)/table added by an additive
                      migration; existing INSERT/UPDATE statements in
                      app/services/*.py continue to work completely
                      unmodified (they simply never populate the new
                      column, same as any other nullable column added to
                      an existing table in this codebase's own history)
        ↓
New Model: application code is updated, in a later, separate change, to
           optionally populate the new field(s) going forward
        ↓
Migration: N/A for existing rows — no backfill is proposed as part of
           this design; each proposal above states explicitly that
           historical rows simply keep NULL
        ↓
Validation: new tests for the new optional field(s), existing tests
            required to pass completely unchanged (same discipline as
            every migration in this codebase's history, per `docs/
            implementation/TESTING_STRATEGY.md`)
        ↓
Old Data Retained: always — nothing here deletes, renames, or
                    type-changes an existing column
```

Migration type per proposal: **additive** for all four (§5/§6/§7/§9). No
proposal in this document requires dual-write, backfill, cutover, or
deprecation — those strategies are relevant only to §4.B (the patient-
identifier consolidation question), which this phase explicitly does not
propose implementing yet, precisely because it's the one item that would
need a heavier strategy (dual-write already exists; backfill status is
unconfirmed; cutover is not recommended now).

---

## 15. Explicitly rejected premature complexity

Per instruction, evaluated and rejected for this phase, each against the
specific evidence that would have justified it:

- **Terminology server**: rejected — no evidence of multi-hospital scale,
  high terminology-query volume, or frequent code-system updates that
  would justify infrastructure beyond nullable code columns (Phase 2 §14's
  own conclusion, re-affirmed here).
- **FHIR server**: rejected — this phase is explicitly the step *before*
  any FHIR endpoint work; no proposal here creates one.
- **Kafka / message broker**: rejected — no evidence of an event-driven
  requirement anywhere in this codebase; the existing in-app notification
  center (`docs/OPD_HIMS_INTEROPERABILITY_AUDIT.md`'s own finding) already
  serves every current notification need without one.
- **Separate interoperability database**: rejected — directly contradicts
  `docs/decisions/ADR-004-OPD-IPD-CONTINUITY.md`'s own established
  reasoning (a separate database was tried once for IPD, in the superseded
  `ipd-service/` sketch, and reversed specifically because it breaks the
  connected-patient-journey guarantee); the same reasoning applies here.
- **Microservices**: rejected — no proposal in this document needs its own
  deployment lifecycle, failure domain, or scaling profile distinct from
  the existing FastAPI monolith.
- **Distributed event architecture**: rejected — same reasoning as Kafka.
- **External terminology service**: rejected — same as "terminology
  server" above; this is the same rejection stated from the consumption
  side rather than the hosting side.
- **External identity provider**: rejected — no proposal here touches
  authentication; `docs/OPD_HIMS_INTEROPERABILITY_AUDIT.md`'s own OAuth
  2.0 row already recommends layering, not replacing, the existing session
  auth, and only once SMART on FHIR is actually being built (Phase 9 of
  the original sequence, not this one).

---

## 16. Priority classification

### P0 — Clinical safety

- Allergy-vs-prescription checking (design in §17; **not implemented in
  this phase**).

### P1 — Domain integrity

- Medication master data (`medications` table + nullable FKs, §5;
  **implemented in Phase 5**).
- Diagnosis structure, Stage 1 only (`diagnosis_code_system`/`.diagnosis_code`/
  `.diagnosis_code_display` on `consultations`, §6; **implemented in
  Phase 6**).
- Patient-identifier source-of-truth decision (§4.B — a decision to make,
  not a schema change to build yet; classified P1 because the *ambiguity*
  itself is a domain-integrity concern even before any migration happens).

### P2 — Standards readiness

- Allergy code slot (`allergen_code`/`.system`/`.display`, §7).
- `order_results` unit code slots (`unit_code`/`.system`, §9).
- Vitals UCUM metadata mapping (application-code-only, §9).
- Clinical-status → FHIR-status translation tables (§10 — belongs inside
  the eventual FHIR mapping layer, not built now).
- Diagnosis structure, Stage 2 (multi-diagnosis, §6 — conditional on a
  real future requirement).

### P3 — Future interoperability

- ABDM, SMART on FHIR, DICOM, HL7, IHE, NHCX (unchanged from Phase 0 —
  none of this phase's proposals are prerequisites blocking any of these
  from being *evaluated* later, but P1/P2 items should land first so P3
  work has real code/terminology slots to map from rather than only free
  text).

---

## 17. Clinical safety gap — design only, not implemented

> **Update (Phase 4 of the interoperability master prompt): implemented.**
> See `docs/workflows/PHARMACY.md`'s "Allergy Safety Check" section for the
> actual, verified behavior, `app/services/allergy_check_service.py` for
> the matching logic, and `tests/test_allergy_check.py` for the test
> coverage. The design below is kept as the historical record of what was
> approved before implementation — it matches what was built.

> Patient allergies are stored but are not currently checked against
> prescriptions. **Classified P0 — Clinical Safety.**

### Future implementation design (not built)

```
Prescription (doctor adds a prescription_item)
     ↓
Medication  (once §5 ships: the item's medication_id, if set;
             until then, falls back to matching prescription_items.
             medicine_name/.generic_name text directly)
     ↓
Allergen relationship  (match against patient_allergies.allergen_code
                         if populated [§7], else fall back to a text-
                         similarity match against patient_allergies.allergen)
     ↓
Patient Allergy  (the patient's own active allergy list, filtered to
                   active=TRUE, exactly the existing query pattern
                   patient_allergies_patient_id_idx already optimizes for)
     ↓
Match  (a service function — new, e.g. app/services/allergy_check_
        service.py — returns a list of potential matches, does not
        block anything by itself)
     ↓
Warning  (surfaced in ConsultationWorkspace.tsx's Prescription tab as an
          inline, dismissible warning at the moment a matching medicine
          is entered — not a hard block on saving/prescribing)
     ↓
Clinician decision  (the prescribing doctor sees the warning and chooses
                      to proceed or change the prescription — the system
                      never overrides or silently blocks; this directly
                      follows the instruction "Do not make an automated
                      clinical decision on behalf of the clinician" and
                      matches this codebase's own established pattern of
                      warning-then-let-the-human-decide, e.g. duplicate-
                      patient detection surfaces possible_duplicates
                      without blocking registration)
     ↓
Audit  (the warning-shown event, and whether the clinician proceeded
        anyway, is written to audit_log — resource_type='prescription_
        item', action='allergy_warning_shown'/'allergy_warning_
        overridden' — using the existing, already-proven audit mechanism,
        not a new logging path)
```

This design deliberately reuses three things that already exist and work
(`patient_allergies`'s existing active-filtered index, `audit_log`'s
existing write path, and the existing duplicate-detection "warn, don't
block" UX precedent) rather than inventing new mechanisms — consistent
with this whole phase's "smallest, safest change" framing. **Not
implemented in this phase.**

---

## 18. Proposed target model

```
                                Patient                                  [EXISTING]
                                   │
                  ┌────────────────┼────────────────┐
                  │                │                │
            Identifiers        Allergies         Conditions
          [EXISTING, decision   [EXISTING +      [PROPOSED —
           pending §4.B]         proposed code    cross-visit concept,
                                  slot §7]         not built; today only
                                                    per-encounter diagnosis]
                                   │
                              Encounters                                  [EXISTING]
                                   │
         ┌──────────┬─────────────┼─────────────┬─────────────┐
         │          │             │             │             │
        Dx          Rx      Observations   Procedures    Documents
   [EXISTING 1  [EXISTING]  [EXISTING —    [EXISTING —   [NOT IMPLEMENTED,
    field +           │      vitals +       one orders    by design —
    proposed          │      order_results, order_type]   ADR-005]
    code slot         │      kept distinct]
    §6 Stage 1]       ▼
                  Medication
              [PROPOSED — new
               master table §5]
                       │
                       ▼
                  Pharmacy
              [EXISTING — stock/
               dispense, +
               proposed FK]
```

Every `[EXISTING]` label above was verified against a real migration file
in Phases 1-2; every `[PROPOSED]` label is a design in this document only,
not yet built.

---

## 19. Implementation backlog

| Priority | Change | Why | Existing Evidence | Files/Tables Affected | Risk | Dependencies |
|---|---|---|---|---|---|---|
| P0 | Allergy-vs-prescription check (design §17) | Real, unaddressed clinical-safety gap, confirmed absent in 3 consecutive audits | `app/services/pharmacy_services.py` (zero allergy references, re-confirmed 3×) | new `app/services/allergy_check_service.py`; `ConsultationWorkspace.tsx` Prescription tab (new UI warning); `audit_log` (new action values, no schema change) | Low (additive, non-blocking warning) | None — can ship independent of every other item in this backlog |
| P1 | Medication master table + nullable FKs — **implemented, Phase 5** | Two independent free-text medicine representations, no shared identity (Phase 2) | `app/services/pharmacy_services.py`'s `ILIKE` fuzzy-match confirmed | `medications` table (`migrations/0054`); `prescription_items`/`pharmacy_stock` gain nullable `medication_id` | Low (additive; existing free-text columns untouched) | None |
| P1 | Diagnosis code slot (Stage 1 only) — **implemented, Phase 6** | Free-text diagnosis has no terminology-binding point; codebase's own migration comment already flagged this as deferred work | Migration `0029`'s own comment | `consultations` gains 3 nullable columns (`migrations/0056`); `consultation_amendments` gains matching `previous_*` archive columns | Low (additive) | None |
| P1 | Patient-identifier source-of-truth decision | Two representations of the phone identifier, one not authoritative (Phase 1/2) | `patient_identifiers`' own migration comment: "not yet the source of truth for anything" | Decision only in this phase; if acted on later: `app/api/scheduling.py`, `app/api/patient_auth.py`, `app/api/patients.py`, `patients.whatsapp_number` | Medium-high (hot-path lookup rewrite, 3 subsystems) if/when actually migrated; the decision itself is zero-risk | Backfill-status question (§4.B) must be answered first |
| P2 | Allergy code slot | Closes the terminology gap on an already-correct table | Phase 2 §5 | `patient_allergies` gains 3 nullable columns | Low (additive) | None |
| P2 | `order_results` unit code slots | Unit field exists but uncoded/unvalidated (Phase 2 §7) | Migration `0031`'s `unit TEXT` | `order_results` gains 2 nullable columns | Low (additive) | None |
| P2 | Vitals UCUM metadata mapping | Units implicit in column names only, no coded form available to a future mapping layer | Phase 2 §7 (zero `unit` columns found on `vitals`) | New `app/services/` constant, no schema change | Zero (code-only, additive) | None |
| P2 | Clinical-status → FHIR-status translation tables | Needed by any future FHIR mapping layer; internal enums correctly stay unchanged | Phase 2 §3 domain #18 | New mapping module, when the FHIR-foundation phase actually starts | Zero (doesn't exist yet, no current code affected) | The eventual FHIR Foundation phase itself |
| P2 (conditional) | Diagnosis Stage 2 (multi-diagnosis `conditions` table) | No current evidence of a real requirement | §6 | New `conditions` table, `ConsultationWorkspace.tsx` UI | Medium (real new UI/workflow, not just a schema slot) | A concrete product requirement — not evidenced yet, do not build speculatively |
| P3 | ABDM / SMART / DICOM / HL7 / IHE / NHCX | Unchanged from Phase 0 | `docs/OPD_HIMS_INTEROPERABILITY_AUDIT.md` | — | — | P1 items ideally land first so there's a real code/terminology slot to map from |

---

## 20. Preserve As-Is (verified against the repository, not assumed)

- **UHID** (`patients.uhid`, `GENERATED ALWAYS AS`) — deterministic,
  already correct, confirmed by direct read of migration `0024`.
- **Duplicate detection** (`patient_duplicate_reviews`, `pg_trgm`) —
  4-signal, working, confirmed migration `0030`.
- **Patient merge** (`patient_merges`, reversible, fully audited) —
  confirmed migration `0030`.
- **Break-glass** (`break_glass_grants`) — reason-required, expiring,
  audited, explicit denylist (`staff.manage` excluded) — confirmed by
  reading `app/api/staff_auth.py` directly in Phase 1.
- **Audit logging** (`audit_log`) — append-only, on every
  `require_permission()`-gated write — confirmed migration `0033` +
  `app/services/audit_log.py`.
- **Consultation amendment tracking** (`consultation_amendments`) — real
  before/after snapshot, RBAC-gated, reason-required — confirmed migration
  `0041`.
- **Typed vital signs** (`vitals`) — range-`CHECK`-constrained, already
  well-structured, confirmed migration `0029`; explicitly **not** proposed
  to gain per-row unit columns (§9) or to merge with `order_results` (§8).
- **Existing workflow enums** (`consultations.status`, `orders.status`,
  `prescriptions.status`, `appointments.status`, `invoices.status`,
  `payments.status`, every `*.priority`/`.severity`) — all already
  DB-constrained, all confirmed via direct `CHECK` constraint reads across
  Phases 1-2; **not** proposed to change, only to eventually gain an
  external translation table (§10) that lives entirely outside the
  database.
- **The Order Spine** (`orders`/`order_results`, one shape per concept
  across LAB/RADIOLOGY/PROCEDURE/SERVICE/EXTERNAL_REFERRAL) — confirmed
  already unified in Phase 1/2; not proposed to fork by order type.
- **`patient_allergies`'s existing structure** (allergen/reaction/
  severity/active/resolved) — confirmed already well-shaped in Phase 2;
  only a code-slot addition proposed (§7), no structural change.

---

## Final verification statement

Every proposal in §5-10 was checked against a specific piece of Phase 0-2
evidence (cited inline) before being proposed, following the required
format:

- **Evidence exists**: cited per section (migration numbers, service-file
  reads, or a specific audit-doc section reference).
- **Current implementation**: stated per section, matching the audits
  exactly, not restated differently.
- **Problem**: stated per section, distinguishing a real domain-integrity
  problem (medication, diagnosis, patient-identifier ambiguity) from a
  smaller terminology-readiness gap (allergy/lab code slots) from a
  non-problem correctly left alone (vitals structure, existing enums).
- **Why change is necessary**: tied to a concrete downstream consequence
  (clinical safety for §17; unreliable stock/prescription matching for
  §5; no terminology-binding point for §6/§7/§9) — never "because a
  standard recommends it."
- **What should change**: stated as specific, additive, nullable schema
  additions or new tables, per section.
- **What should NOT change**: stated explicitly per section (existing
  free-text columns, existing enums, existing vitals shape, existing
  APIs/UI) and consolidated in §20.

No proposal in this document was made merely because a healthcare standard
recommends it — every one traces to a specific finding already
independently confirmed across three prior audit phases.

---

## STOP CONDITION

**Nothing in this document was implemented.** No migration was written, no
API was changed, no UI component was touched, no terminology code was
added. This is a design/readiness assessment only, per instruction.

**Do not implement Phase 3's proposals, Phase 4 (FHIR Foundation), ABDM,
or any terminology service without explicit confirmation. Waiting for the
next instruction.**
