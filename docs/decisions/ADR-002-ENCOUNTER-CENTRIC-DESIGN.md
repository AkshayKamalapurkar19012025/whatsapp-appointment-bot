# ADR-002: Encounter-Centric Design

## Status

Accepted. Implemented for OPD.

## Context

A patient's clinical and financial records (vitals, consultation notes, orders, prescriptions, charges) all happen in the context of a specific visit, not in the abstract. Early in this codebase's history, some of this data risked being modeled as hanging directly off `appointments` — which is a *scheduling* concept (it has a start/end time, a status like `NO_SHOW` that a clinical record shouldn't inherit) and doesn't naturally extend to non-appointment-based care (a future IPD admission has no "appointment").

## Problem

What should every clinical/financial table's foreign key point to — the appointment, or something else — so that (a) the model stays coherent as new visit types (walk-in, follow-up, and eventually IPD/Emergency) are added, and (b) a patient's full history reads as one connected story rather than one thread per visit-creation-mechanism?

## Decision

**Encounter is the central clinical context.** `encounters` (migration `0028`) sits between `patients` and every clinical/financial event. `appointments` links to `encounters` (an appointment *creates or attaches to* an encounter), not the other way around. Every table added since — `vitals`, `consultations`, `orders`, `prescriptions`, `charges`, `invoices` — FKs to `encounter_id`, confirmed by direct inspection of the migrations, not assumed.

`encounters.encounter_type` exists specifically to generalize beyond OPD later (`CHECK (encounter_type IN ('OPD'))` today, by design meant to widen) — this column was added *in anticipation of* IPD/Emergency, per its own migration comment, even though those aren't built yet.

## Alternatives considered

1. **FK everything to `appointment_id` directly** (rejected) — conflates scheduling state with clinical context, and has no answer for IPD (an admission isn't an appointment).
2. **No intermediate concept; FK everything straight to `patient_id`** (rejected) — loses the notion of "this specific visit's events," which is exactly what a hospital chart needs (you don't want today's vitals attributed loosely to "the patient" with no visit boundary).
3. **A separate encounter model per care setting (OPDEncounter, IPDEncounter as distinct tables)** (rejected) — this is precisely the "disconnected modules" anti-pattern; a single `encounters` table with a type discriminator, mirroring the same pattern already proven for `orders.order_type`, was chosen instead.

## Consequences

- Every downstream workflow doc in `docs/workflows/` can state, truthfully, "scoped by `encounter_id`" as a load-bearing fact, not an aspiration.
- Patient 360 (`docs/workflows/PATIENT_360.md`) is a straightforward join across tables that all share this one FK shape — it didn't need bespoke cross-referencing logic per data type.
- Widening `encounter_type` later is a one-line additive migration, not a redesign — the cost of "designing for it before it's needed" was paid once, cheaply, at Phase 3.

## Future implications

When IPD/Emergency are built (`docs/architecture/OPD_TO_IPD.md`), they create their own `encounters` rows with a new `encounter_type` value and their own admission/visit-specific tables (beds, wards, admissions) — but continue writing orders/prescriptions/charges through the *same* `orders`/`prescriptions`/`charges` tables this ADR's decision already established, not new parallel ones (see `docs/decisions/ADR-004-OPD-IPD-CONTINUITY.md` and `docs/architecture/ORDER_SPINE.md`).
