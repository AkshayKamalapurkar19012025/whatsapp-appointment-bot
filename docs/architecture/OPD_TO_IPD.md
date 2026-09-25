# OPD → IPD Continuity

## Purpose

State how a future IPD (in-patient) module must connect to the existing OPD/patient model, and record the architectural decision already made about *where* IPD data should live.

## Current State

IPD does not exist as a working feature. What exists is:

1. `encounters.encounter_type` (migration `0028`) — `CHECK (encounter_type IN ('OPD'))`, deliberately reserving the column for future widening, per that migration's own comment.
2. `ipd-service/schema/0001_baseline_ipd_schema.sql` — a **superseded** sketch of IPD as its own service with its own database (`wards`, `beds`, `admissions`, `bed_transfers`, `clinical_orders`, `discharge_summaries`), referencing the OPD app's `patients.uhid` only as a plain string (no live cross-database FK). Its own header now states plainly: this approach conflicts with the master spec's Principle 3/4 (shared orders/results/billing/timeline across OPD/IPD/Emergency) and section 76 ("IPD Compatibility"), and the decision taken was **Option A: IPD data lives in the OPD app's own database**, extending `encounters`, not a separate service. This file is kept as a record of the alternative considered and rejected — **do not build against it.**
3. No disposition field, no Follow-up/Refer/Admit-to-IPD/Emergency choice anywhere in `ConsultationWorkspace.tsx` — not even a stub, unlike External Referral which was built as a real first-class path from the start.

## Target State

OPD → IPD must preserve the same patient identity (`patients.uhid`) and the same `encounters` spine. A patient admitted to IPD should:

- Get a new `encounters` row with `encounter_type = 'IPD'` (or the OPD encounter transitions/links to an IPD one — **undecided, needs a design pass when this phase starts**, not assumed by this doc).
- Have that IPD encounter feed the same `orders`/`order_results`/`prescriptions`/`charges`/`invoices` tables the OPD spine already uses, so a patient's Patient 360 timeline shows OPD and IPD events together, not as two disconnected histories.
- Have IPD-specific data (ward/bed occupancy, admission/discharge, bed transfers) live in new tables in the **same OPD database** — extending the schema shape sketched in the superseded `ipd-service/schema/0001_baseline_ipd_schema.sql` (its `wards`/`beds`/`admissions`/`bed_transfers`/`discharge_summaries` table design is still a reasonable starting point for the *shape* of IPD-specific tables — only the "separate service/database" decision was rejected, not the table design itself) but with `admissions.patient_id` as a real FK to `patients.id` (or a real FK to `encounters.id`) instead of a loose `patient_uhid` string reference.
- Have `clinical_orders` (from the superseded sketch) collapse into the existing `orders` table with `order_type` extended (e.g. `NURSING`, `DIET`) rather than staying a separate table, per `docs/architecture/ORDER_SPINE.md`'s "one order spine" rule — the superseded sketch's own `clinical_orders` table predates that rule being applied to IPD and should not be carried over as-is.

## Gap

Everything. IPD is unbuilt. The concrete, actionable gap this doc records is the **architectural decision already made but not yet acted on**: when IPD work starts, it must NOT resurrect the separate-service approach, and it must NOT create a second `orders`-like table for clinical/nursing orders. Both are explicit anti-patterns already identified in this codebase's own history.

## Recommended Implementation (when this phase is scoped)

1. Widen `encounters.encounter_type`'s CHECK constraint to include `IPD` (and, separately, `EMERGENCY` if that phase comes first) in the same migration that adds the first IPD-specific table.
2. Design `admissions`/`beds`/`wards`/`bed_transfers`/`discharge_summaries` as new tables in this same Postgres database, `admissions.patient_id` FK'd to `patients.id` directly (not a string UHID reference — that pattern existed only because the superseded sketch assumed a separate database with no possible live FK).
3. Reuse `orders`/`order_results` for IPD clinical/nursing orders by extending `order_type`, not by reviving `clinical_orders`.
4. Add an `IPD` entry to `hospital_modules`/`MODULES` in `app/services/module_services.py` only after deciding (per the open question in `docs/architecture/MODULE_ARCHITECTURE.md`'s Gap section) whether the existing single-module-flag Licensed/Enabled shape fits an entire care setting or needs adaptation.
5. Add a real `disposition` field/choice (Follow-up / Refer / Admit to IPD / Emergency) to the consultation workflow only once there's a real IPD encounter for "Admit to IPD" to create — a disposition option that leads nowhere is worse than not having the option, per `CLAUDE.md`'s "do not create fake placeholder workflows and call them complete."

See `docs/decisions/ADR-004-OPD-IPD-CONTINUITY.md` for the formal decision record.
