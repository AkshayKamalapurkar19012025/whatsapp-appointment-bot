# ABDM Readiness Audit

**Phase 9 deliverable** (OPD/HIMS interoperability master prompt, "FHIR Foundation Hardening & ABDM Readiness"). This document is an **evidence-based assessment**, not a build plan and not a claim of readiness. **No ABDM connectivity of any kind exists in this codebase after this phase**: no ABHA creation/linking, no consent-manager integration, no HIU/HIP API, no sandbox or production network call to any ABDM service. Every "Implemented" line below points at real, already-existing code or schema — nothing here was added to make this audit look more complete.

**Path note.** `CLAUDE.md`'s documented doc structure is `docs/{product,architecture,ux,workflows,printing,implementation,decisions}/` — no `docs/interoperability/` directory. Phase 8 already made this same call for `FHIR_FOUNDATION.md` (placed under `docs/architecture/`, not a new `docs/interoperability/`); this document follows the same precedent for consistency rather than introducing a new top-level doc category for one file.

## How to read this table

Three ratings only, per the instruction's own constraint — no partial credit invented beyond what's listed:

- **IMPLEMENTED** — real, working, verified (by test and/or live check).
- **PARTIALLY IMPLEMENTED** — some real, evidenced capability exists, but it is materially short of what the labeled requirement needs.
- **NOT IMPLEMENTED** — nothing exists. Stated plainly, not softened.

"A code field exists" is not the same claim as "the requirement is met" — this distinction is applied consistently below, the same way `docs/architecture/FHIR_FOUNDATION.md`'s terminology readiness matrix applies it to LOINC/ICD/UCUM.

---

## 1. Identity

| Item | Rating | Evidence |
|---|---|---|
| Permanent internal patient identity | **IMPLEMENTED** | `patients.uhid` (format `HOS-NNNNNNN`, DB-generated), the real permanent identity per `docs/decisions/ADR-001-PATIENT-IDENTITY.md`. Mobile number is a searchable/contact attribute only. |
| Duplicate detection / patient merge | **IMPLEMENTED** | `app/services/patient_duplicate_detection.py`, `app/services/patient_merge.py` — pre-existing, unrelated to ABDM, but relevant groundwork for any future identity-linking. |
| ABHA number / ABHA address | **NOT IMPLEMENTED** | No column, table, or field anywhere in this schema stores or references an ABHA identifier. `patient_to_fhir` explicitly asserts this negative (`tests/test_fhir.py::test_fhir_patient` checks no `identifier[].system` contains "abha"). |
| ABHA creation/verification (Aadhaar/mobile OTP, demographic auth) | **NOT IMPLEMENTED** | No such flow, endpoint, or third-party SDK integration exists. |
| ABHA-UHID linking | **NOT IMPLEMENTED** | No linking table or field exists; `patients.uhid` and a hypothetical ABHA number have no relationship modeled anywhere. |

## 2. FHIR

| Item | Rating | Evidence |
|---|---|---|
| FHIR R4 resource mapping (11 clinical/administrative resource types + PractitionerRole) | **IMPLEMENTED** | `app/services/fhir_mappers.py` + `app/api/fhir.py`, Phases 8–9. See `docs/architecture/FHIR_FOUNDATION.md`'s Resource Mapping Table for the full, current list. |
| Minimal search (`?patient=`, `?identifier=`) | **IMPLEMENTED** | Phase 9, `app/api/fhir.py`'s search endpoints, capped at `_SEARCH_LIMIT = 50`, no real pagination. |
| Patient-centric Bundle / summary (`$everything`) | **IMPLEMENTED** | Phase 9, `GET /fhir/r4/Patient/{id}/$everything` — the real, standard FHIR operation, not an invented one. |
| `meta.lastUpdated` | **IMPLEMENTED** | Phase 9, on every resource whose source table has a real `updated_at`. |
| `meta.versionId` / resource version history | **NOT IMPLEMENTED** | No table tracks a real per-row version counter — deliberately not fabricated. A `Bundle` history operation or `vread` is therefore also not possible and not attempted. |
| ABDM-specific FHIR Implementation Guide profiles (India's National Health Authority publishes profiles for `Patient`, `Encounter`, `Condition`, `Bundle`/`DocumentReference` "health records," etc., distinct from base R4) | **NOT IMPLEMENTED** | This layer emits **base FHIR R4 only**. No ABDM profile URL is declared in `meta.profile` on any resource (correctly — declaring one would be false), and no ABDM-required extension exists anywhere in the mapper code. See the Profile Gap Analysis below for specifics. |
| `DocumentReference` / structured "health record" artifact (the actual unit ABDM's Health Information exchange moves — typically a `Bundle` wrapped as a signed, encrypted FHIR document) | **NOT IMPLEMENTED** | No document-assembly, signing, or encryption capability exists. `$everything`'s `collection` Bundle is a read convenience for this application's own API consumers, not a produced "care context" artifact in ABDM's sense. |

## 3. Consent

| Item | Rating | Evidence |
|---|---|---|
| FHIR `Consent` resource | **NOT IMPLEMENTED** | No mapper, no endpoint. Correctly listed as such in `docs/architecture/FHIR_FOUNDATION.md`'s "Explicitly NOT Implemented." |
| Any internal consent model (patient opting in/out of data sharing, scoped/time-boxed/purpose-boxed access grants) | **NOT IMPLEMENTED** | Verified by exhaustive search: `grep -rli "consent" migrations/ app/` returns **zero matches** anywhere in this codebase. There is no consent table, no consent column, no consent service, no consent UI. |
| Break-glass emergency access (`POST /api/auth/staff/break-glass`) | **IMPLEMENTED, but is NOT a consent mechanism** | `app/api/staff_auth.py` — this is an internal RBAC feature: an authenticated staff member requesting a temporary elevated *permission grant* within this hospital's own system, reviewed after the fact (`staff.manage`-gated review). It has no patient-facing consent semantics, no scope tied to a specific external requester, and no relationship to ABDM's HIU-initiated, patient-approved consent-artifact flow. **This document explicitly does not treat it as a substitute for `Consent`** — doing so was considered and rejected per this phase's own instruction. |
| Consent Manager (CM) integration | **NOT IMPLEMENTED** | No such concept, client, or callback endpoint exists. |

## 4. Health records (HIU/HIP exchange)

| Item | Rating | Evidence |
|---|---|---|
| HIP (Health Information Provider) API surface — `/v0.5/hip/*` callbacks ABDM's gateway calls | **NOT IMPLEMENTED** | No route matching any ABDM HIP callback contract exists in `app/api/`. |
| HIU (Health Information User) API surface | **NOT IMPLEMENTED** | Same — no route, no client. |
| Care context linking (the ABDM concept of associating a specific visit/encounter with a `careContextReference` for later retrieval) | **NOT IMPLEMENTED** | `encounters` (this schema's real care-episode concept, migration `0028`) has no ABDM care-context identifier column, and none should be added speculatively — see Profile Gap Analysis. |
| Data-push / data-pull encrypted health-record exchange (ABDM's actual wire protocol, using ECDH key exchange over the HIU/HIP contract) | **NOT IMPLEMENTED** | No cryptographic key-exchange code, no ABDM gateway client, no network call to any `abdm.gov.in`/sandbox host anywhere in this codebase (verified by inspection of `app/` — no such hostname or SDK dependency exists in `requirements.txt`). |

## 5. Security

| Item | Rating | Evidence |
|---|---|---|
| Authentication (staff-facing) | **IMPLEMENTED** (for this application's own scope) | `app/services/staff_auth.py`: argon2id password hashing, bearer session tokens with a hard TTL (`SESSION_TTL_HOURS = 24`) and an idle timeout (`SESSION_IDLE_TIMEOUT_MINUTES = 30`), server-side revocation. This is a real, working auth system — **not** ABDM-flavored (no ABHA-linked login, no OTP-based patient auth). |
| RBAC | **IMPLEMENTED** | `require_permission(...)` gates every write endpoint (WEB P1.a/P6), checked server-side, never trusting a frontend role check. |
| Audit logging | **PARTIALLY IMPLEMENTED** | `app/services/audit_log.py`'s `record_audit_log` covers every RBAC-gated *write* action (~20 call sites, confirmed by inspection) — a real, working write-audit trail. It does **not** cover reads, including every FHIR `GET` in this layer (unchanged from Phase 8's own documented decision in `FHIR_FOUNDATION.md`'s "Audit" section). ABDM's own accountability model expects read/access auditing for health-information exchange specifically — this gap is real and not yet closed. |
| Tenant isolation | **IMPLEMENTED** | Every FHIR endpoint (single-resource, search, `$everything`) is `hospital_id`-scoped, verified live against a real second hospital in this phase (`tests/test_fhir.py::test_all_fhir_endpoints_isolate_by_tenant`, `test_fhir_search_by_patient_isolates_by_tenant`, `test_fhir_patient_everything_excludes_other_patients_and_tenants`, plus a manual live-server run in this phase's own verification). |
| Transport encryption (TLS) | **NOT VERIFIABLE FROM APPLICATION CODE** | TLS termination is a deployment-layer concern (reverse proxy/load balancer), not something this FastAPI application does itself — `app/config.py` has no TLS certificate/key configuration, which is expected and correct for how this app is deployed, but means this document cannot claim TLS is or isn't enforced without knowing the actual deployment topology. Not scored IMPLEMENTED or NOT IMPLEMENTED for that reason — genuinely unknown from the code alone. |
| Encryption at rest | **NOT IMPLEMENTED** (at the application layer) | No column-level or field-level encryption exists for any clinical data; whatever encryption-at-rest exists (if any) is a Postgres/infrastructure-level concern outside this codebase's control, same caveat as TLS above. |
| SMART on FHIR / OAuth2/OIDC (the actual auth model ABDM's HIU/HIP ecosystem and most external FHIR consumers expect) | **NOT IMPLEMENTED** | Confirmed in `docs/architecture/FHIR_FOUNDATION.md`'s "Authentication" section — FHIR access reuses the existing staff bearer-session auth, which has no concept of scoped, delegated, patient-authorized third-party access. This is the single largest security-model gap between "this FHIR layer" and "an ABDM-ready HIP." |

## 6. Interoperability (general, beyond ABDM specifically)

| Item | Rating | Evidence |
|---|---|---|
| Base FHIR R4 read API | **IMPLEMENTED** | As above. |
| FHIR write API | **NOT IMPLEMENTED** | Deliberately — see `FHIR_FOUNDATION.md`'s "Read-Only Scope." Any future ABDM HIP flow that needs to *receive* data (not just serve it) would need this, and it does not exist. |
| HL7 v2 | **NOT IMPLEMENTED** | No HL7 v2 parser, generator, or interface engine anywhere in this codebase. |
| DICOM | **NOT IMPLEMENTED** | No DICOM handling — this application has no imaging-modality integration at all (radiology orders are text-described `ServiceRequest`/`order_results` rows, never image data). |
| IHE profiles (XDS, PIX/PDQ, etc.) | **NOT IMPLEMENTED** | No IHE actor or transaction exists. |
| NHCX (National Health Claims Exchange, India's insurance-claims interoperability layer) | **NOT IMPLEMENTED** | No claims-exchange integration of any kind. |
| CDS Hooks | **NOT IMPLEMENTED** | No clinical-decision-support hook service. |
| FHIR Subscriptions | **NOT IMPLEMENTED** | No subscription/notification mechanism for FHIR resource changes. |

---

## ABDM Profile Gap Analysis

**"This system has a FHIR R4 layer" is explicitly not the same claim as "this system is ABDM-ready," and this section exists specifically to reject that equivalence with evidence.** ABDM's own FHIR Implementation Guide (published by India's National Health Authority) layers real, specific requirements on top of base R4 that this codebase does not meet. Per resource:

| Resource | What Phase 8/9 built | What an ABDM profile additionally requires | Gap |
|---|---|---|---|
| `Patient` | UHID as a local `identifier` | An `identifier` slice for the ABHA number/address, using ABDM's specific system URI | No ABHA field exists to populate it from (Identity §1) |
| `Encounter` | `class = AMB` (structural HL7 code) | ABDM "care context" linkage (an identifier ABDM's gateway uses to request just this visit's records later) | No care-context identifier concept exists internally |
| `Condition`/`Observation`/`MedicationRequest`/etc. | Base R4 shapes, honest uncoded-or-coded-when-present terminology | ABDM profiles generally expect (not always strictly require) real terminology bindings (SNOMED CT/LOINC) for interoperable clinical meaning across HIPs | Terminology readiness matrix (`FHIR_FOUNDATION.md`) already shows SNOMED CT/LOINC as **NOT implemented** — this gap was known before this audit, not newly discovered |
| `Bundle` (the actual exchanged artifact) | Read-convenience `collection`/`searchset` Bundles for this application's own API consumers | ABDM's HIP flow produces a *signed* structured document (often a `DocumentReference` wrapping a `Bundle`) for a specific consent-approved data request, encrypted for the requesting HIU using an ECDH key exchange | No signing, no encryption-for-transfer, no consent-scoping of *which* Bundle content is allowed out — this is a materially different artifact than `$everything`'s Bundle |
| All resources | `id` = internal integer PK | ABDM doesn't care about internal ids at all — it cares about the `careContextReference`/ABHA-linked identifiers surfaced through the HIP callback contract, a workflow this system has no endpoint for | Not a mapping gap — an entirely separate API surface (HIP callbacks) this codebase doesn't have |
| — | Bearer session auth | ABDM gateway calls are authenticated via a specific X-CM-ID/X-HIP-ID header + JWT contract between HIP/HIU/gateway, unrelated to this application's own staff login | Entirely separate authentication model, not an extension of existing auth |
| — | No consent model | Every ABDM data flow is consent-artifact-gated (a specific, ABDM-issued consent id scoping exactly what can be shared, with whom, for how long) | Would need to be built from nothing — see Consent §3 |

**Conclusion:** the FHIR layer built in Phases 8–9 is real, working, internally useful groundwork (a hospital-side read API for its own data, correctly R4-shaped) — but it answers a different question than "is this HIP-ready." Becoming ABDM-ready requires, at minimum: an ABHA identity layer, a real consent model, a HIP callback API surface, document signing/encryption, and a materially different authentication contract — none of which exist, and none of which this phase built.

## Future ABDM Architecture (documentation only — not built)

If a future phase pursues ABDM connectivity, the natural shape — consistent with how this FHIR layer was kept a read-only *view* over the existing domain model, never a redesign of it — is a **separate adapter layer**, not fields scattered through existing HIMS tables:

```
Existing HIMS Domain Model
        │
        ▼
FHIR Mapping Layer (app/services/fhir_mappers.py)   <- Phase 8/9, unchanged
        │
        ▼
FHIR R4 Resources (GET /fhir/r4/...)                <- Phase 8/9, unchanged
        │
        ▼
┌─────────────────────────────────────────────┐
│  ABDM Adapter Layer (future, NOT built)      │
│  - ABHA identity linking (new table, FK'd    │
│    to patients.id -- never replacing uhid)   │
│  - Consent artifact store (new table)        │
│  - HIP callback endpoints (new router,       │
│    separate auth contract from staff login)  │
│  - Document assembly + signing + ECDH        │
│    encryption for HIU data-push              │
│  - ABDM FHIR profile extensions applied at   │
│    this layer only -- base mappers stay      │
│    profile-agnostic                          │
└─────────────────────────────────────────────┘
        │
        ▼
ABDM Gateway / Sandbox / HIU-HIP Network
```

The two guardrails this diagram is meant to enforce, both direct continuations of principles already in `CLAUDE.md` and `FHIR_FOUNDATION.md`: (1) an ABHA number is a *link*, stored in its own table FK'd to `patients.id` — it never becomes a second patient-identity path or replaces `uhid` as this application's own permanent identifier (per ADR-001, unchanged); (2) ABDM-specific concerns (consent artifacts, signing, the HIP callback contract) live in their own adapter module, never mixed into `app/services/fhir_mappers.py`'s existing profile-agnostic base-R4 mapping functions — the same separation-of-concerns reasoning that kept FHIR itself out of the internal domain tables in Phase 8.

## Future SMART on FHIR / OAuth2 Requirement (documentation only — not built)

Any future ABDM HIP flow, and any serious external FHIR consumer beyond this application's own staff, needs an authentication model this codebase does not have: **delegated, scoped, patient- (or consent-artifact-) authorized access**, not a staff member's own bearer session. The realistic future chain:

```
External requester (ABDM gateway / SMART app)
        │  OAuth2/OIDC authorization request, scoped to a specific
        │  patient + consent artifact + time window
        ▼
Authorization Server (future -- not this app's existing staff-session auth)
        │  issues a scoped access token
        ▼
FHIR R4 Layer (app/api/fhir.py)
        │  Depends(get_current_staff) REPLACED for this path by a new
        │  Depends(get_scoped_external_token) -- never bolted onto the
        │  existing staff dependency, since the trust models are
        │  fundamentally different (an internal employee's own session
        │  vs. a third party's consent-scoped, time-boxed grant)
        ▼
Same existing mappers/queries -- unchanged
```

This is **not built in Phase 9** (explicitly out of scope per this phase's hard boundary) and is not simulated, stubbed, or partially wired in — this section exists only so a future phase has a concrete, evidenced starting point rather than needing to re-derive "what would this even require" from scratch.

---

## Final Verification Table

The 25-row assessment this phase's instruction requires, IMPLEMENTED / PARTIALLY IMPLEMENTED / NOT IMPLEMENTED only:

| # | Item | Rating |
|---|---|---|
| 1 | Permanent internal patient identity (UHID) | IMPLEMENTED |
| 2 | Patient duplicate detection / merge | IMPLEMENTED |
| 3 | ABHA number / address | NOT IMPLEMENTED |
| 4 | ABHA creation/verification flow | NOT IMPLEMENTED |
| 5 | ABHA–UHID linking | NOT IMPLEMENTED |
| 6 | FHIR R4 resource mapping (core set) | IMPLEMENTED |
| 7 | FHIR minimal search | IMPLEMENTED |
| 8 | FHIR Bundle / `$everything` | IMPLEMENTED |
| 9 | `meta.lastUpdated` | IMPLEMENTED |
| 10 | `meta.versionId` / version history | NOT IMPLEMENTED |
| 11 | ABDM FHIR Implementation Guide profiles | NOT IMPLEMENTED |
| 12 | `DocumentReference` / signed health-record artifact | NOT IMPLEMENTED |
| 13 | FHIR `Consent` resource | NOT IMPLEMENTED |
| 14 | Internal consent model | NOT IMPLEMENTED |
| 15 | Break-glass (internal RBAC, not a consent substitute) | IMPLEMENTED (for its own, non-ABDM purpose) |
| 16 | Consent Manager integration | NOT IMPLEMENTED |
| 17 | HIP API surface | NOT IMPLEMENTED |
| 18 | HIU API surface | NOT IMPLEMENTED |
| 19 | Care-context linking | NOT IMPLEMENTED |
| 20 | ABDM data exchange (encrypted push/pull) | NOT IMPLEMENTED |
| 21 | Staff authentication + RBAC (this application's own scope) | IMPLEMENTED |
| 22 | Read/access audit logging (FHIR-specific) | PARTIALLY IMPLEMENTED |
| 23 | Tenant isolation | IMPLEMENTED |
| 24 | SMART on FHIR / OAuth2/OIDC | NOT IMPLEMENTED |
| 25 | Terminology readiness (SNOMED CT/LOINC/RxNorm) | NOT IMPLEMENTED (ICD-10/UCUM slots: PARTIALLY IMPLEMENTED — see `FHIR_FOUNDATION.md`) |

**Bottom line:** this application has a real, tested, internally-consistent FHIR R4 read layer and a real, working internal identity/auth/RBAC system — genuine groundwork. It has **no** ABDM-specific capability of any kind. Becoming ABDM-ready is a distinct, substantial future phase (identity linking, consent, HIP callbacks, document signing/encryption, a new authentication model), not a natural extension of what exists today, and this phase deliberately did not begin building it.
