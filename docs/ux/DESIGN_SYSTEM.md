# Design System

## Canonical reference

The token-level design system (colors, status-color mapping, button hierarchy, what's tokenized vs. deliberately left alone) already has a thorough, current document: **`docs/design-system.md`**. This file does not duplicate it — read that file for every color/token/button question. This file covers the parts the new documentation structure asks for that the existing doc doesn't: component-level reuse status and the app shell.

## Current State

### Token system (see `docs/design-system.md` for full detail)

- One `:root` block in `frontend/src/styles.css` defines every color as a CSS custom property; nothing hardcodes a hex value that already has a token meaning (verified by a full-codebase grep — zero remaining unaccounted hex-literal colors as of that doc's last audit).
- Four semantic status families (Success/Warning/Danger/Info), each with a solid + soft tone, applied consistently via `pill status-*` classes.
- Three-tier button hierarchy: `.btn` (primary, one per screen), `.btn-secondary`, `.btn-danger` (destructive only).

### App shell

- Sidebar + top bar + main content — matches the target layout described in `docs/architecture/MODULE_ARCHITECTURE.md`.
- Consistent loading (`state-block` + spinner), empty-state, and error-state patterns reused across every panel (confirmed across Dashboard, Exceptions, Packages, Billing, Timeline, Receipt, per `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §48-51).

### Component-level reuse — the real gap

Reuse is strong at the CSS-class/pattern level, weaker at the shared-React-component level:

| Target shared component | Current state |
|---|---|
| `PatientHeader` | 🟡 Every relevant screen shows patient name/UHID/doctor context via a shared `.patient-context-meta` CSS class, but each screen (`ConsultationWorkspace`, `AppointmentBillingPanel`, `PatientTimelineModal`, etc.) reimplements the markup locally — functionally present, not componentized |
| `StatusBadge` | 🟡 Every screen builds its own `<span className="pill status-...">` inline rather than importing a shared component |
| `Timeline` | 🟡 Patient 360's timeline rendering (`PatientTimelineModal.tsx`) is bespoke to that one modal, not a reusable `Timeline` component |
| `ConfirmationDialog` | ✅ Effectively covered — `AlertDialog`/`AlertDialogContent`/etc. (`components/ui/alert-dialog.tsx`, a Radix-based shadcn-style primitive) is already shared and reused correctly across the app for every confirm-before-destructive-action prompt |

## Target State

The visual result (colors, spacing, status meaning) should not change — the tokens are correct and complete. The target is componentizing `PatientHeader`/`StatusBadge`/`Timeline` so future screens import one implementation instead of re-deriving the same markup, reducing drift risk (the design-system doc's own worked example — "In Consultation" accidentally using a one-off purple — is exactly the class of bug a shared `StatusBadge` component would make structurally harder to reintroduce).

## Gap

Three named components don't exist yet as reusable React components, despite the visual pattern already being consistent everywhere they'd be used.

## Recommended Implementation

Extract `PatientHeader`/`StatusBadge`/`Timeline` as their own components under `frontend/src/components/` (or `frontend/src/admin/shared/`) **only when a phase actually touches two or more of their current call sites** — extracting them speculatively, with no real second caller driving the API design, risks guessing the wrong shared interface. `ConsultationWorkspace.tsx`, `AppointmentBillingPanel.tsx`, and `PatientTimelineModal.tsx` are the three existing call sites to design `PatientHeader`'s props against.
