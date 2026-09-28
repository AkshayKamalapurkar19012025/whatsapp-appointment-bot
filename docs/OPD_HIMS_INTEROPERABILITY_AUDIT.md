# HospitalOS — Interoperability & Standards: Phase 0 Audit

Read-only discovery deliverable, per the "Healthcare Interoperability & Standards
Implementation — Master Prompt." **No application code was changed to produce
this audit.** Every row below was checked against the actual repository (grep
across `app/`, `frontend/src/`, `migrations/`, `docs/`, `requirements.txt`) —
not recalled from memory or assumed. Where something adjacent-but-not-equivalent
exists, it's named explicitly rather than folded into a false "implemented."

## Method

For each requirement: searched case-insensitively for the standard's own name
and closely related terms across the whole repo; where a hit suggested a real
implementation, read the actual source (migration, service file, API router)
to confirm scope before writing a status. A blank search result is reported as
verified-absent, not assumed-absent.

## Audit table

| Requirement | Existing Implementation | Status | Evidence | Gap | Proposed Change |
|---|---|---|---|---|---|
| **FHIR** | None. | NOT IMPLEMENTED | Zero references to "fhir" anywhere in `app/`, `frontend/src/`, `migrations/`, `docs/` (full-repo grep). | No FHIR resource model, mapping layer, or API namespace. The internal domain model itself (`docs/architecture/DOMAIN_MODEL.md`) is well-connected and encounter-centric — a reasonable thing to map *from* once this phase starts, per this prompt's own "don't make FHIR the internal model" principle. | Phase 2 (FHIR Foundation) — not started. Do not begin without Phase 1's canonical-model verification first. |
| **ABDM** | None. | NOT IMPLEMENTED | Zero references to "abdm"/"abha"/"nhcx" anywhere in the repo. | No ABHA linkage on `patients`, no HIP/HIU concept, no care-context model. | Phase 5 — not started; depends on FHIR + Consent existing first, per the prompt's own priority order. |
| **SMART on FHIR** | Session-token auth exists (see OAuth 2.0 row) but is not SMART. | NOT IMPLEMENTED | Current auth: `app/services/staff_auth.py` (username+password, Argon2id, opaque Bearer session token) and `app/services/patient_auth.py` (mobile+OTP, same token pattern). No FHIR scopes, no launch context. | Full SMART launch/scope architecture absent. | Phase 9 — explicitly sequenced after a stable FHIR API + OAuth2/OIDC layer; do not build before those exist. |
| **DICOM** | None. Radiology is one `order_type = 'RADIOLOGY'` row in the generic Order Spine. | NOT IMPLEMENTED | Zero references to "dicom"/"pacs". `orders`/`order_results` (migration `0030`) have no imaging-file/study concept — see `docs/architecture/ORDER_SPINE.md`. | No imaging protocol, no PACS integration, no `ImagingStudy` resource. | Per this prompt's own instruction: implement only when the existing radiology module has a concrete imaging-integration requirement — not speculatively. |
| **HL7 v2** | None. | NOT IMPLEMENTED | Zero references. The only "webhook" in the codebase is the WhatsApp Business API webhook (`app/api/booking.py`, `app/api/scheduling.py` comments) — an unrelated, pre-existing integration, not a healthcare-messaging gateway. | No HL7 adapter/gateway exists for LIS/RIS/blood-bank/legacy-HIS integration. | Only when a concrete legacy-system integration is contracted — do not build speculatively per the prompt's own guidance. |
| **IHE** | None. | NOT IMPLEMENTED | Zero references to "ihe"/"pix"/"pdq"/"xds"/"xca"/"atna". | No profile evaluated or implemented. | Evaluate only once a concrete cross-enterprise use case exists, per the prompt's own "do not implement a profile merely because it exists." |
| **SNOMED CT** | None. Clinical text fields are free text. | NOT IMPLEMENTED | `consultations.diagnosis`/`chief_complaint`/`history_notes`/`examination_notes` are plain `TEXT` (migration `0029`). That migration's **own comment already states**: "diagnosis is a [free-text field]... a structured, codeable diagnosis list is real future work" — this gap was identified by the team before this audit, not discovered by it. | No terminology table, no `{code, system, display}` model anywhere in the schema (confirmed by grep — zero hits for that pattern). | Phase 4 (Terminology Service) — needed before `Condition`/`Observation` FHIR resources can be properly coded in Phase 2/3. |
| **LOINC** | None. `order_results` stores parameter names as free text. | NOT IMPLEMENTED | Same table/evidence as SNOMED CT — no terminology binding on lab/observation parameters. | No LOINC code list or binding. | Phase 4, alongside SNOMED CT. |
| **ICD** | None. Same free-text `diagnosis` field as SNOMED CT. | NOT IMPLEMENTED | `consultations.diagnosis` — plain `TEXT`, no code list. | No ICD-10/11 binding for diagnosis/billing/reporting. | Phase 4. Needs an explicit design decision on SNOMED-for-clinical vs. ICD-for-billing/reporting dual-coding when this phase starts — not assumed here. |
| **UCUM** | None. Units are implicit app convention (e.g. vitals always stored as kg/cm/°C), not validated codes. | NOT IMPLEMENTED | `order_results.unit` and vitals columns store/imply units as free text/convention, not a validated unit-code. | No unit-code validation anywhere. | Phase 4. |
| **OAuth 2.0** | A real, working, deliberately-chosen alternative: DB-backed opaque Bearer session tokens for both staff and patients, server-enforced on every route via `require_permission()`. | PARTIALLY IMPLEMENTED (real auth exists; not the OAuth 2.0 protocol) | `app/services/staff_auth.py`, `app/services/patient_auth.py` — `secrets.token_urlsafe(32)`, SHA-256-hashed at rest, Argon2id for passwords. The opaque-token-over-JWT choice is a documented architectural decision (`docs/WEB_P0_...md` §7): instant revocability matters more than statelessness for a PHI-adjacent system. | No `/authorize`/`/token` endpoints, no client registration, no OAuth2 grant types. | Needed only when third-party app authorization (SMART on FHIR, Phase 9) becomes a real requirement. **Do not replace the existing session auth for the first-party staff/patient web UIs** — add an OAuth2 layer alongside it for third-party/app use cases, the same "map, don't replace" principle this prompt applies to FHIR. |
| **OpenID Connect** | None. | NOT IMPLEMENTED | No `id_token`, no `/.well-known/openid-configuration`, no OIDC dependency in `requirements.txt` (full dependency list has 6 packages, none auth-protocol-related beyond `argon2-cffi`). | Full. | Phase 9, alongside OAuth 2.0. |
| **Consent** | None found. | NOT IMPLEMENTED | Zero references to "consent" anywhere in `app/`, `frontend/src/`, `migrations/`. `patient_duplicate_reviews`/`patient_merges` are a *workflow approval* (is this a duplicate patient?), not patient-authorized data-sharing consent — not a substitute. | No consent model of any kind for external data sharing. `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION`: whether the registration/booking UI has an unmodeled consent checkbox (a frontend-only checkbox with no backend row wouldn't surface in this grep) — worth a direct UI check before assuming even that doesn't exist. | Phase 6 — as its own first-class model. Do not conflate with the RBAC permission system, which governs *staff* access, not patient-authorized *external* sharing — these are different concerns. |
| **AuditEvent** (FHIR-shaped audit) | A real, broad, working audit trail — not FHIR-shaped, but genuinely used everywhere. | PARTIALLY IMPLEMENTED | `audit_log` table (migration `0033`): `staff_id`, `action`, `resource_type`, `resource_id`, `details` (JSONB), `created_at`, append-only. Written by `record_audit_log()` from **every `require_permission()`-gated write endpoint, and from break-glass grant/review** (confirmed via `app/services/audit_log.py`'s own header comment and direct call-site grep). | Not shaped as FHIR `AuditEvent` (no `agent`/`entity`/`source`/`outcome` structure); no first-class `patient_id` column (linkage would need a `resource_type`+`resource_id` join); `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` whether `READ`/`EXPORT`/`PRINT`/`SHARE` actions are logged at all today (current `action` values observed are mutation-oriented, e.g. `break_glass.grant` — reads are very likely not logged, consistent with most systems, but not confirmed either way by this pass). | Phase 4/7 — add a thin `AuditEvent` mapping layer over this existing table (same "mapping layer, not a parallel system" principle as FHIR generally), rather than building a second logging path. |
| **Provenance** | A real, working, but narrowly-scoped precedent. | PARTIALLY IMPLEMENTED (one entity type only) | `consultation_amendments` (migration `0041`) — a full before/after snapshot on every consultation edit, RBAC-gated (`consultation.amend`), reason required. This is genuinely provenance-like, just not generalized. | No equivalent for any other entity; no schema-level distinction anywhere between human-entered / system-generated / imported / AI-generated data — moot today since there is no AI-generated or externally-imported clinical data path at all yet. | Phase 7 — generalize the amendment-history pattern this codebase already proved out for consultations, rather than inventing a new mechanism. |
| **CDS Hooks** | Not the spec. A real, adjacent precedent exists for a *different* purpose. | NOT IMPLEMENTED | The Exception Engine (`app/services/exception_engine.py`) is a genuine, working, rule-based alerting system — but every rule is **operational** (waiting for triage, waiting for doctor, order pending, prescription not dispensed, billing not started, payment pending), not clinical. Confirmed directly: `patient_allergies` exists as a real data table (migration `0042`) but is **not referenced anywhere in `pharmacy_services.py`** — no allergy check runs at prescribing time today, despite the data being available. | No CDS Hooks protocol; no medication-interaction or allergy-warning service; existing allergy data is captured but unused for safety checking. | Phase 14 in strict sequence — but flagged here as a candidate for earlier prioritization: wiring the already-existing `patient_allergies` table into the prescribing workflow as a simple warning is small, high clinical-safety value, and needs no new standard or infrastructure to start delivering value, well ahead of a full CDS Hooks implementation. Worth raising with the product owner rather than silently deferring to Phase 14. |
| **FHIR Subscriptions** | A real, working, but non-standards in-app mechanism. | NOT IMPLEMENTED | `notification_center_service.py` + `notifications` table (migration `0044`) — a genuine bell-icon/list/mark-read notification system, polled/in-app only. No webhook delivery, no external subscriber concept. | No event bus, no FHIR `Subscription` resource, no way for an external system to register interest in an event. | Phase 13 — likely built on top of the notification-center's existing "something worth notifying about just happened" call sites rather than instrumenting a second set from scratch. |
| **NHCX** | None directly; a real, intentionally-scoped foundation exists. | NOT IMPLEMENTED | Zero references to "nhcx". `invoices.bill_type` (a 6-way classification including insurance/TPA/corporate/government-scheme — see `docs/workflows/BILLING.md`) is a genuine, deliberate extension point; payer/policy/pre-auth/co-pay/claim fields are explicitly deferred as "Future fields" in that migration's own comment, not silently missing. | No `Coverage`/`Claim` FHIR resources, no NHCX gateway integration. | Phase 15 — and **only after directly verifying the current NHCX specification**, per this prompt's own explicit instruction not to implement against outdated examples. This audit does not attempt to describe NHCX's current spec for that reason. |

## Summary

**Zero of the 18 requirements are implemented as their named standard.** This is
an honest, expected finding for a system that grew from a WhatsApp booking bot
into an OPD system — none of this phase's requirements were ever in scope
before now. What the audit found instead, and what matters for sequencing:

- **Real, reusable foundations exist for five of them** — audit trail
  (`audit_log`), a provenance precedent (`consultation_amendments`), an
  operational-alerting precedent (Exception Engine), a deliberate billing
  extension point (`bill_type`), and a considered, documented auth architecture
  (opaque session tokens) that should be *extended alongside*, not replaced by,
  a future OAuth2/OIDC layer.
- **The clearest, most consequential single gap** is that `patient_allergies`
  data exists but isn't used at prescribing time — a real clinical-safety gap
  today, independent of any standards work, and cheap to close.
- **Free-text clinical fields were already flagged as future work by the team
  itself** (migration `0029`'s own comment on `diagnosis`) before this audit —
  terminology binding (Phase 4) is not a surprise finding, it's a known,
  named, deferred decision now being picked back up.

## Recommended next step

Per the master prompt's own priority sequence and this audit's findings:
**Phase 1 (Canonical Healthcare Data Model verification)** is next — confirm
which of `Patient`/`Practitioner`/`PractitionerRole`/`Organization`/`Location`/
`HealthcareService`/`Appointment`/`Schedule`/`Slot`/`Encounter`/`Condition`/
`AllergyIntolerance`/`Observation`/`DiagnosticReport`/`ServiceRequest`/
`Procedure`/`Medication`/`MedicationRequest`/`MedicationDispense`/`CarePlan`/
`DocumentReference`/`ImagingStudy`/`Coverage`/`Claim`/`Consent` concepts already
have a clean internal analog (most do — see `docs/architecture/DOMAIN_MODEL.md`)
versus which have no analog at all (`Consent`, `Coverage`/`Claim` beyond
`bill_type`, `ImagingStudy`, `CarePlan`) before any FHIR mapping work starts.

**DO NOT IMPLEMENT PHASE 1 OR LATER YET.** This document is the Phase 0
deliverable only, per the master prompt's own first rule.
