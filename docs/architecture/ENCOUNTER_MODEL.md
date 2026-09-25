# Encounter Model

## Purpose

Define the encounter as the central clinical context connecting OPD (and, in target state, IPD/Emergency) to every downstream clinical and financial event, per `docs/decisions/ADR-002-ENCOUNTER-CENTRIC-DESIGN.md`.

## Current State

`encounters` (migration `0028_encounters.sql`):

| Column | Notes |
|---|---|
| `id` | PK |
| `patient_id` | FK to `patients` |
| `encounter_type` | `TEXT NOT NULL DEFAULT 'OPD' CHECK (encounter_type IN ('OPD'))` — **only `'OPD'` is currently allowed** |
| `status` | `OPEN` / `CLOSED`, mirrors the appointment's own lifecycle |
| `started_at` | required |
| `closed_at` | nullable; `NULL` while `OPEN` |
| `created_at` / `updated_at` | standard |

One row per clinical visit. Every appointment created going forward gets exactly one encounter (the migration's own comment: "Nullable on purpose: every appointment gets one going forward" — i.e. historical appointments that predate this migration may not have one, but current bookings always do).

Every clinical/financial table added since migration `0028` FKs to `encounter_id`, not to `appointment_id` — this is the concrete mechanism behind "one connected workflow" in `docs/product/HIMS_WORKFLOW.md`. Confirmed by grep across `migrations/`: `vitals`, `consultations`, `orders`, `prescriptions`, `charges`, `invoices` all carry `encounter_id`.

`encounters.status` (`OPEN`/`CLOSED`) is simpler than the appointment's own status enum (`PENDING`/`CONFIRMED`/`REJECTED`/`CANCELLED`/`CHECKED_IN`/`COMPLETED`/`NO_SHOW`) — an encounter is a binary "is this visit still active," while the appointment tracks the more granular scheduling lifecycle. `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION`: the exact rule for which appointment-status transitions close the encounter (the migration comment says "mirrors the appointment's own lifecycle at the moment of writing" — re-check `app/services/appointment_services.py` for the current transition logic before relying on this doc's summary alone).

## Target State

`encounter_type` should eventually support `OPD` / `IPD` / `EMERGENCY` (and, further out, `FOLLOW_UP` if that's modeled as its own encounter rather than a field on an OPD encounter — **undecided, do not assume**). The column and its CHECK constraint already anticipate this ("Widen the CHECK in a later migration alongside whatever introduces the next type" — the migration's own comment), but nothing has actually attempted to insert a non-`'OPD'` row yet, so this is an aimed-at, unproven target, not a near-complete feature.

## Gap

1. `encounter_type` CHECK constraint only allows `'OPD'` — widening it is a one-line, additive migration, but is meaningless without the IPD/Emergency admission workflow that would create those rows (see `docs/architecture/OPD_TO_IPD.md`). Do not widen the CHECK speculatively without a concrete IPD phase ready to use it.
2. No formal state-transition table/diagram exists for `encounters.status` independent of the appointment's — anyone changing appointment-completion logic should re-verify the encounter-closing behavior stays correct, since it currently piggybacks on appointment status rather than being independently modeled.

## Recommended Implementation

When an IPD phase starts: widen the CHECK constraint in the same migration that adds the admission/bed tables (extending the OPD database, per ADR-004 — not the superseded separate-service sketch), and add an explicit encounter-status transition function/service rather than continuing to let it implicitly track appointment status, since an IPD admission has no appointment to mirror.
