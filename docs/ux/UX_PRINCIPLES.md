# UX Principles

## Purpose

The principles every screen in HospitalOS should be judged against, and this repo's current standing against each.

## Context follows the user

**Principle**: users should not repeatedly search for the patient.

**Current State**: ✅ Real. Once a patient/appointment/encounter is selected, every downstream screen in that flow (`ConsultationWorkspace`, billing, prescription) receives the id via props/route param, not a re-search. `GlobalSearchBar.tsx`'s appointment-result click lands directly in the in-progress visit (`ConsultationWorkspace`) rather than a generic patient page, per its own code comment.

## No dead-end pages

**Principle**: every important page should offer meaningful next actions.

**Current State**: ✅ Verified for every screen touched during recent phases — booking confirmation offers Check-in/View Patient/Print Token; billing offers void/edit-terms/add-charge/record-payment; the receipt offers Print/Send. `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` for screens predating recent phases that weren't part of that verification pass.

## Role-oriented UI

**Principle**: different roles see workflows relevant to their responsibilities.

**Current State**: ✅ Real (see `docs/product/HIMS_WORKFLOW.md`'s role table). `ADMIN`/`STAFF` remain full-access fallbacks by design — `STAFF` is the "unrestricted generalist" role for a small hospital that hasn't assigned granular roles yet, not a bug.

## Patient context is always visible

**Principle**: patient header/encounter context shown where appropriate.

**Current State**: 🟡 Functionally present (every clinical/billing screen shows patient name/UHID/doctor), not componentized — see `docs/ux/DESIGN_SYSTEM.md`'s `PatientHeader` gap. One specific known gap: a doctor mid-consultation has to leave the Consultation tab to see Patient 360 history (it opens as a separate modal from Patients), rather than viewing it side-by-side — the master spec's "without leaving the workflow" language isn't fully met here.

## Progressive disclosure

**Principle**: don't overload users with every possible field at once.

**Current State**: ✅ Patient registration is deliberately minimal (name + mobile required, DOB/gender optional) rather than the full field layout a complete registration form could have — an explicit, documented design choice for walk-in speed, not an oversight. Tables follow a compact-columns-plus-drawer pattern (e.g. `PatientsPanel`'s table shows Name/UHID/Mobile/Last Visit/Visits/Actions, full detail behind "View/Edit" or the timeline drill-in).

## Clear states

**Principle**: every workflow supports loading / empty / success / validation error / server error / permission denied / unavailable module / duplicate-conflict / in-progress / completed.

**Current State**:
- ✅ Loading, empty, success: consistent (`state-block` + spinner; empty states explain what's missing and what to do next, e.g. "No packages yet. Add one to bill it as a single line item on a visit.").
- 🟡 Validation/server error: shown as inline human-readable messages reading the API's own error detail, not the specific `errorCode`-keyed structured pattern a fully modeled error-state UI would use (the backend *does* now return `{success, errorCode, message, details}` per `app/error_handling.py` — the frontend doesn't yet branch UI behavior on `errorCode` specifically, it just displays `message`).
- ✅ Permission denied: `require_permission()` 403s surface as an inline error; role-gated UI elements are hidden rather than shown-then-rejected in the common case.
- ✅ Unavailable module: `DEGRADATION_LABELS` in `ModuleLicensingPanel.tsx` and the `EXTERNAL`/`BLOCKED`/`HIDDEN` behavior itself (see `docs/architecture/MODULE_ARCHITECTURE.md`) are the concrete implementation of this state.
- ✅ Duplicate/conflict: patient duplicate detection surfaces `possible_duplicates` in the registration response; booking conflicts return a friendly message backed by the DB exclusion constraint.
- 🟡 In-progress/completed: present per-workflow (e.g. consultation Save Draft vs. Complete, invoice states) but not a single named UI pattern applied uniformly — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` before assuming any specific screen has it.

## Accessibility

**Current State**: ✅ A dedicated accessibility pass verified 0 axe-core violations across 13 admin screens (color contrast, heading order, unlabelled selects, missing page `<h1>`), per the Phase 12 hardening work referenced in `docs/product/PRODUCT_VISION.md`. `aria-label`/`aria-describedby`/`role` usage is broad (89 occurrences across 32 files as of the last full audit) but keyboard-first interaction (explicit Enter/Escape handling) exists in only a minority of components — not yet systematic.

## Responsive design

**Current State**: 🟡 A real phone-width topbar overflow bug was found and fixed in the same hardening pass. No dedicated tablet-breakpoint audit has been performed; desktop is the primary target, mobile support is "doesn't visibly break," not "designed for."

## Gap

The two concrete, named UX gaps worth carrying into a future phase: (1) frontend error UI doesn't yet key off the backend's `errorCode` field now that it exists, so error messages are less differentiable than the backend now supports; (2) Patient 360 isn't viewable inline during an active consultation.

## Recommended Implementation

Neither gap requires new data — both are frontend-only. (1) is a small, mechanical change to whichever component renders API error messages, once a phase actually needs `errorCode`-specific behavior (e.g. a "retry" action only for idempotency-safe errors). (2) would mean adding a collapsible timeline panel inside `ConsultationWorkspace`, reusing `PatientTimelineModal`'s data-fetching logic rather than duplicating it — a good candidate to pair with the `Timeline` component extraction named in `docs/ux/DESIGN_SYSTEM.md`.
