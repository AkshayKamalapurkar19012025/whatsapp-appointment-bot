# ADR-003: Module Enablement

## Status

Accepted. Implemented (migrations `0052`/`0053`).

## Context

Different hospitals license different capabilities (a small clinic might not run its own pharmacy or lab; a larger one might). The system needs a way to turn a module off for a hospital that hasn't paid for it, and a way for a hospital that *has* paid for it to further control whether it's actually switched on day-to-day — without either capability letting a hospital admin grant themselves something they haven't licensed, and without module toggling ever destroying clinical history.

## Problem

How does the system distinguish "this hospital is entitled to use Pharmacy" (a commercial/platform decision) from "this hospital's admin has currently switched Pharmacy on" (an operational decision), and what happens to existing prescriptions/dispense records if Pharmacy gets switched off later?

## Decision

**Three separate concepts: Licensed, Enabled, Available.**

- **Licensed** — a platform-level entitlement (`hospital_modules.licensed`). This app has no separate platform-owner role yet, so the licensing toggle currently lives on the same ADMIN-only screen as Enabled (`ModuleLicensingPanel.tsx`) — but it's backed by its own distinct permission (`module.manage_license`, migration `0053`), separate from `module.manage_enablement`, specifically so a future platform-admin role only needs a `role_permissions` change, not a new screen or a schema change.
- **Enabled** — the hospital admin's own on/off switch, reachable only once Licensed. Enforced not just in application code but at the database layer: `CHECK (enabled = FALSE OR licensed = TRUE)` on `hospital_modules`. **A hospital admin cannot self-grant a paid module even by calling the API directly** — the constraint holds regardless of which code path attempts the write.
- **Available** — derived, never stored: `licensed AND enabled`. Every gated action calls `is_module_available()` (`app/services/module_services.py`), which treats a missing `hospital_modules` row (never licensed) the same as an explicit `licensed = FALSE` row.
- Un-licensing a module forces `enabled` off too (can't stay "enabled" on something no longer licensed); re-licensing never auto-enables — that stays a separate, deliberate hospital-admin action.
- Every module has exactly one **degradation** mode — `HIDDEN` / `EXTERNAL` / `BLOCKED` — declared once per module (`MODULES` dict), and disabling a module never deletes or hides historical clinical/financial rows, only blocks *new* use per that mode.

## Alternatives considered

1. **A single `enabled` boolean per module, no separate licensing concept** (rejected) — has no answer to "hospital admin must not be able to grant themselves paid modules," which is an explicit, named requirement.
2. **Enforce the licensing/enablement relationship only in application code, not the database** (rejected) — a future code path (a bug, a future admin-side bulk-update script) could bypass an application-level check; the DB constraint holds regardless of which code writes the row.
3. **Hard-delete or cascade-block access to historical records when a module is disabled** (rejected) — explicitly named as something never to do; a hospital that stops paying for Pharmacy should still be able to see last year's dispense history.

## Consequences

- Three real modules (`LAB_RADIOLOGY`, `PHARMACY`, `PACKAGES`) prove this pattern works, including through a real pre-ship bug (an early `hospital_modules.updated_by` FK to `staff` caused the test suite's per-test cleanup to cascade-truncate the whole table — caught and fixed before merging, documented in the relevant commit).
- Any future module (a real IPD module, an IPD-specific licensing unit, an IPD ward-level license) can reuse this exact mechanism — add an entry to `MODULES`, run a migration inserting/allowing the new `module_key`, done. No schema redesign needed.

## Future implications

See `docs/architecture/MODULE_ARCHITECTURE.md`'s Gap section for the one open question this ADR doesn't resolve: whether a whole care setting (a future `IPD` "module") fits this same single-flag-per-hospital shape, or needs a richer unit (e.g. per-ward licensing) — that's a decision for whenever IPD is actually scoped, not assumed here.
