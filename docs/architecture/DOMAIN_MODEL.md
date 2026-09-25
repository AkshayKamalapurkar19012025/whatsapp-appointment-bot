# Domain Model

## Purpose

The one-paragraph version of this whole documentation set: **Patient → UHID → Encounter → connected clinical/financial events.** Every other architecture doc drills into one piece of this; this doc is the map.

## Current State — core entities and their relationships

```
patients (permanent identity, UHID)
   │ 1:N
   ▼
encounters (clinical context; encounter_type currently 'OPD' only)
   │ 1:N each
   ├─▶ appointments (scheduling/visit record; status lifecycle)
   ├─▶ vitals (triage)
   ├─▶ consultations (+ consultation_amendments for controlled edits)
   ├─▶ orders (order_type: LAB/RADIOLOGY/PROCEDURE/SERVICE/EXTERNAL_REFERRAL)
   │      │ 1:N
   │      ▼
   │   order_results (generic parameter/value/unit/reference-range/abnormal/critical)
   ├─▶ prescriptions ─▶ prescription_items ─▶ pharmacy_dispense_records
   ├─▶ charges (source_type: Consultation/Lab/Radiology/Procedure/Pharmacy/Package/Other)
   │      │
   │      ▼
   └─▶ invoices ─▶ payments
```

Supporting/cross-cutting entities that reference the above but aren't part of the main spine:

- `doctors`, `departments`, `appointment_types`, `doctor_schedule`, `doctor_blocks` — scheduling/availability inputs, referenced by `appointments`, not by `encounters` directly.
- `staff`, `roles`, `permissions`, `staff_roles`, `role_permissions` — RBAC, referenced by every mutating action's `staff_id`/`updated_by`/`ordered_by`/etc.
- `hospital_modules` — module licensing state (`hospital_id`, `module_key`, `licensed`, `enabled`), referenced logically (by `hospital_id` + `module_key`) by every gated action, not by FK into the clinical spine.
- `patient_allergies`, `patient_duplicate_reviews`, `patient_merges` — patient-identity-adjacent, FK to `patients`, not to a specific encounter.
- `audit_logs`, `notifications` — cross-cutting, reference the mutated record by id + type, not a structural FK into the spine.
- `hospitals` — tenancy root; `hospital_id` appears on most tables per `migrations/0027_hospital_tenant_context.sql`'s "purely structural" single-tenant-in-practice framing.

## Target State

Unchanged in shape — the target state is this same spine, with two additions: `encounters.encounter_type` widened beyond `'OPD'` (see `docs/architecture/OPD_TO_IPD.md`), and a shared `PatientHeader`/`Timeline` presentation layer over the same data (see `docs/ux/DESIGN_SYSTEM.md`) — neither changes the entity relationships above.

## Gap

No entity-relationship gap. The domain model is sound and already matches the target shape; the gaps that exist elsewhere in this documentation set are about which *rows* can exist (`encounter_type` values, module coverage) and which *screens* exist over this data, not about the relationships themselves.

## Non-negotiable rule for future work

**Never add a new clinical or financial table that isn't reachable from `encounter_id`.** If a future feature seems to need a disconnected table (a per-department order table, a separate billing model for a new module), that is a signal the feature needs to extend `orders`/`charges`/`invoices` instead — see `docs/architecture/ORDER_SPINE.md` for the worked reasoning, and `CLAUDE.md`'s "what not to do" list.
