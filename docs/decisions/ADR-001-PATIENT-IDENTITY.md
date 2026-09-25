# ADR-001: Patient Identity

## Status

Accepted. Implemented.

## Context

A hospital patient needs one durable identity that survives across visits, departments, and (eventually) care settings (OPD/IPD/Emergency). The obvious shortcut — using mobile number as the identity key — is common in lightweight systems (including this project's own original WhatsApp bot, which identifies patients by `whatsapp_number`) but breaks down in a real hospital: numbers are shared within families, changed, reassigned by telecom providers, or simply not provided by every patient.

## Problem

How does HospitalOS identify "the same patient" across a walk-in visit booked at the front desk, a WhatsApp-booked appointment, and a future IPD admission — without either (a) creating duplicate patient records for the same real person, or (b) incorrectly merging two different real people who happen to share a contact number?

## Decision

**Permanent UHID-based patient identity. Mobile is not the sole identity key.**

- `patients.uhid` (format `HOS-NNNNNNN`, migration `0024`) is the permanent, backend-generated identifier.
- Mobile number remains a searchable/contact attribute — used for lookup and as the WhatsApp bot's session key — but is never treated as *the* identity a clinical or financial record is keyed to.
- Duplicate detection (`app/services/patient_duplicate_detection.py`) runs at registration time and surfaces `possible_duplicates` rather than silently creating a second record or silently blocking registration — the decision to merge or proceed separately stays a human one.
- Patient merge (`app/services/patient_merge.py`, `patient_merges`/`patient_duplicate_reviews` tables) is a real, working, authorized workflow, not just "readiness" — it includes unmerge safety checks.

## Alternatives considered

1. **Mobile number as identity** (rejected) — breaks under shared/reassigned numbers, and is explicitly the anti-pattern named in the master spec ("make mobile number the permanent patient identity" is in `CLAUDE.md`'s "what not to do" list).
2. **Strict uniqueness constraint on name+mobile at creation time** (rejected) — would incorrectly block legitimate cases (family members sharing a household number) and doesn't handle the case where the *same* patient registers twice with slightly different name spelling.
3. **No duplicate detection, merge-only-after-the-fact via manual admin review of the whole patient table** (rejected) — doesn't scale, and misses the registration-time moment when the operator has the most context to catch it.

## Consequences

- Every clinical/financial table in the system references `patient_id`, and every patient-facing screen displays `uhid` as the primary human-readable reference — this is why `docs/architecture/DOMAIN_MODEL.md` puts `patients`/`uhid` at the top of the domain model.
- A duplicate that slips past detection at registration time is recoverable via merge, not a permanent data-integrity problem.
- Future IPD/Emergency admission must reuse this same `patient_id`/`uhid` — never create a parallel patient record per care setting (see `docs/decisions/ADR-004-OPD-IPD-CONTINUITY.md`).

## Future implications

When IPD is built, admission must resolve to an existing `patients.id` (via UHID lookup or the same duplicate-detection path used at OPD registration) — not a new identity scoped to the admission. Any future patient-facing self-service portal (beyond the existing WhatsApp OTP flow) must authenticate against the same `patients` table, not a separate user-identity system.
