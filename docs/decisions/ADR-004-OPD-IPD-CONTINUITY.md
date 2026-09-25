# ADR-004: OPD-IPD Continuity

## Status

Accepted (architectural boundary decision only — IPD itself is not implemented). Supersedes an earlier, different decision (see Alternatives considered).

## Context

An early exploration of IPD (in-patient) functionality was sketched as its own service, with its own database (`ipd-service/schema/0001_baseline_ipd_schema.sql`), referencing the OPD app's `patients.uhid` only as a loose string value with no live cross-database foreign key. That sketch's own stated reasoning was legitimate on its own terms: OPD is slot/appointment-shaped and largely stateless between visits, while IPD is occupancy-shaped and stateful for days/weeks per admission, with different failure-mode isolation needs (a stuck bed transfer must never be able to take down appointment booking).

A subsequent Phase 0 audit (`docs/OPD_HIMS_P0_AUDIT.md` §3) flagged that this separate-service boundary conflicts directly with this project's own Principle 3/4 (shared orders/results/billing/timeline across OPD/IPD/Emergency) and master spec section 76 ("IPD Compatibility") — a separate database makes "one patient, one connected timeline" impossible to enforce structurally; it would depend on two services staying in sync by convention, not by constraint.

## Problem

Should IPD, when built, live in its own service/database (isolated failure domain, but structurally disconnected from OPD's patient/encounter model), or in the same database as OPD (guaranteed connectivity to Patient 360/orders/billing, but sharing a failure domain with appointment booking)?

## Decision

**IPD data — and, when IPD is actually built, IPD data itself — lives in the OPD app's own database, not a separate service.** `encounters.encounter_type` (migration `0028`) already reserves room for this: its CHECK constraint is deliberately narrow today (`'OPD'` only) specifically so it can widen later without a redesign.

The original `ipd-service/schema/0001_baseline_ipd_schema.sql` sketch is kept in the repository, explicitly marked **SUPERSEDED**, as a record of the alternative considered and rejected — not as a schema to build against. Its *table design* (`wards`/`beds`/`admissions`/`bed_transfers`/`discharge_summaries`) is still a reasonable starting point for the shape of IPD-specific tables; only the separate-database/separate-service *boundary* decision was reversed. Its `clinical_orders` table, however, should not be carried over as-is — it predates the Order Spine decision (`docs/decisions/ADR-002-ENCOUNTER-CENTRIC-DESIGN.md`'s sibling, `docs/architecture/ORDER_SPINE.md`) being applied to IPD, and a real IPD implementation should extend the existing `orders` table (new `order_type` values) instead of reintroducing a second order table.

## Alternatives considered

1. **Separate IPD service/database** (rejected, was the original decision, since reversed) — clean failure isolation, but structurally breaks "one connected patient journey," the project's own most important stated principle. A patient's OPD and IPD history would only ever be joinable via an API call between services, at read time, rather than being enforced connected by the database itself.
2. **Same database, same decision as taken** (accepted) — patient identity and encounter model stay singular and DB-enforced; IPD's higher write-volume/longer-lived-state character is handled by good schema design (separate tables, appropriate indexes) within the same Postgres instance, not by a separate deployment.
3. **Same database, but IPD reuses OPD's `appointments` table for admissions** (rejected, not seriously considered) — an admission isn't an appointment (no start/end slot, can last days/weeks); this would badly overload a table already carrying real scheduling-concurrency guarantees IPD doesn't need and shouldn't risk destabilizing.

## Consequences

- IPD implementation, whenever scoped, adds new tables to *this* repository's migrations, not a new service/repository.
- `admissions.patient_id` (or equivalent) will be a real FK to `patients.id`, not a loose string reference — the loose-string design in the superseded sketch existed only because that version assumed no live cross-database FK was possible.
- IPD's failure-isolation concern (a stuck bed transfer must not take down booking) must be addressed by database-level discipline (separate tables, careful transaction scoping, appropriate indexing) rather than by physical service separation — a real cost of this decision, worth remembering if IPD's write volume ever becomes large enough to threaten OPD's own performance in practice.

## Future implications

Any future Emergency module should follow the same reasoning: a new `encounter_type`, new setting-specific tables in the same database, reusing `orders`/`prescriptions`/`charges` rather than forking them. See `docs/architecture/OPD_TO_IPD.md` for the concrete implementation checklist.
