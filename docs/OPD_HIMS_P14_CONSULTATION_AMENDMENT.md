# OPD/HIMS Master Spec — Phase 14: Controlled Consultation Amendment

Follows `docs/OPD_HIMS_P13_RECEIPT.md`.

## Scope

Phase 13's own "Next phase" note identified where the remaining, still
concretely-scoped work was: master spec section 70 ("Clinical/
financial records should have controlled amendment/void processes"),
referenced as deferred in this codebase's own comments in at least two
places — `app/services/clinical_services.py`'s module docstring ("no
amendment workflow yet: once COMPLETED, save_consultation_draft_
service refuses further edits") and Phase 8's known-gaps section, both
citing this exact spec section by number.

**Financial records already have their half of section 70.** Charges,
payments, and invoices (Phase 9) are never hard-deleted — a mistaken
one is VOIDED with a required reason, the row kept as an audit record.
Clinical records had the opposite: a COMPLETED consultation simply
refused any further edit, full stop. There was no controlled way to
correct one at all, only an absolute lock. This phase gives clinical
records the same "never silently overwrite, always require a reason,
always keep the history" treatment the financial side already has —
not a free reopen, but a real, auditable correction path.

**One design call drives everything else: an amendment is deliberately
NOT gated on CHECKED_IN.** Every other clinical write in this codebase
(vitals, consultation drafts, completion) requires the patient to
currently be CHECKED_IN, because that documentation happens *during*
the live visit. An amendment is the opposite by definition — it exists
specifically to correct a record *after* completion, which in the real
world usually means after the visit, and often the whole encounter, has
already closed (a lab result comes back two days later and changes the
diagnosis). Gating amendment on CHECKED_IN would make it unusable for
the one thing it exists to do. It's gated on RBAC instead — a new
`consultation.amend` permission (ADMIN-only today) — since correcting a
signed-off record is a materially more sensitive action than writing
one the first time, and deserves its own permission rather than
piggybacking on the CHECKED_IN check every other write here relies on.

## What changed

**`consultation_amendments`** (`migrations/0041_consultation_
amendments.sql`) — one row per correction, holding the *pre-amendment*
snapshot (never the current values, which stay on `consultations`
itself, updated in place) plus a required `reason`, `amended_by`, and
`amended_at`. Never updated or deleted once written.

**`amend_consultation_service`** (`app/services/clinical_services.py`)
— same "full-form save" semantics `save_consultation_draft_service`
already uses (every field set to exactly what's passed), but only for
a COMPLETED consultation (raises `ConsultationNotAmendable` for a
DRAFT one — those are just edited directly, no amendment needed), and
only after archiving the pre-amendment snapshot in one transaction with
the update. Keeps the same clinical-safety floor `complete_
consultation_service` itself enforces: an amendment can't blank out
chief complaint or diagnosis either.

**`POST .../consultation/amend`** and **`GET .../consultation/
amendments`** (`app/api/clinical.py`) — the first `consultation.amend`-
gated, the second bare-staff (viewing history is a read, same tier as
every other clinical read in this router).

**`ConsultationWorkspace.tsx`** — an "Amend consultation" button
appears (ADMIN-only) once a consultation is COMPLETED. Clicking it
locally re-enables just the consultation tab's fields (a new `amending`
flag, independent of the existing `readOnly` computation that still
correctly keeps vitals/orders locked) and adds a required "Reason for
amendment" field. A collapsible "Amendment history" list shows each
past correction's date, who made it, why, and the diagnosis it
replaced.

## Files changed

- `migrations/0041_consultation_amendments.sql` — new.
- `app/services/clinical_services.py` — `amend_consultation_service`, `list_consultation_amendments_service`.
- `app/services/exceptions.py` — `ConsultationNotAmendable`.
- `app/api/clinical.py` — the two new endpoints.
- `frontend/src/admin/ConsultationWorkspace.tsx` — the Amend UI + history.
- `frontend/src/types.ts`, `frontend/src/api.ts` — `ConsultationAmendment`/`ConsultationAmendInput`, `amendConsultation`/`getConsultationAmendments`.
- `frontend/src/styles.css` — `.amendment-history`/`.amendment-history-list`.
- `tests/test_consultation_amendments.py` — new, 8 tests.
- `tests/conftest.py`, `tests/test_hospital_tenant_coverage.py` — `consultation_amendments` added to the per-test truncation list and the tenant-coverage exemption list (child of `consultations`, tenant derivable through it).

## Database changes

`consultation_amendments` (new table, exempted from the per-table
`hospital_id` requirement as a direct child of `consultations`, which
is itself already in that exempt chain back to `encounters`). One new
permission, `consultation.amend`, granted to ADMIN.

## API changes

New: `POST /api/appointments/{appointment_id}/consultation/amend`
(`consultation.amend`), `GET /api/appointments/{appointment_id}/
consultation/amendments` (bare authenticated staff).

## Tests

8 new (`tests/test_consultation_amendments.py`): amending a DRAFT
consultation is rejected (409 — use the ordinary edit path instead); a
missing reason is rejected (422); blanking chief complaint or diagnosis
via an amendment is rejected (422, same floor completion itself
enforces); a plain STAFF account without `consultation.amend` is
rejected (403); a successful amendment updates the consultation's live
fields and archives exactly the pre-amendment values into history;
amendment succeeds even after the whole visit has been closed out via
the front desk's own `/complete` endpoint (proving the "not gated on
CHECKED_IN" design point, not just asserting it); a second amendment
archives the *first* amendment's values, not the original consultation
(the snapshot chain stays accurate through multiple corrections); 404
for a nonexistent consultation.

Full suite: 586 passed (578 + 8 new), 1 pre-existing failure —
`test_queue_visited_at_is_doctor_local_time_not_utc`, the same real-
wall-clock-time class of flake flagged in every phase report since
Phase 7 (the other intermittent one, `test_date_first_lists_multiple_
doctors_with_their_own_slot_counts`, didn't reproduce this run); no
skipped this run. Neither failure touches code this phase's diff
changes.

**Browser-verified end-to-end**: ran the app locally, booked a walk-in
visit through to the queue, completed a consultation ("Cough and cold"
/ "Common cold"), clicked the new "Amend consultation" button, confirmed
every field became editable again while the reason field was required
before "Save amendment" would enable, changed the diagnosis to
"Bronchitis (confirmed on follow-up)" with a reason, saved, and
confirmed the consultation's live diagnosis updated immediately.
Expanded "Amendment history" and confirmed it correctly showed the
date, the staff username, the reason, and "Previous diagnosis: Common
cold." `npx tsc -b --force`, `npm run lint`, and `npm run build` all
pass clean (only pre-existing warnings elsewhere in the app, none newly
introduced).

## Known gaps / deliberately out of scope

- **Consultation-only.** Vitals, orders, prescriptions, and invoices
  each have their own existing correction story (a fresh vitals
  recording simply supersedes the prior one as the "latest"; orders/
  prescriptions/charges/payments already have their own VOID -- never
  edit-in-place -- pattern from Phases 6-9). Consultation was the one
  clinical record type with no controlled correction path at all,
  which is what this phase closes; it doesn't revisit the others'
  already-adequate stories.
- **No amendment of an amendment's own reason.** Once written, an
  amendment row is immutable, same as a VOID reason on the financial
  side — if the reason itself was mistyped, a further amendment can
  note the correction in its own reason text, not edit history in
  place.
- **ADMIN-only, not doctor-specific.** There's still no dedicated
  DOCTOR login role in this codebase (flagged as a known gap since
  Phase 5) to gate amendment by "the treating doctor, or an admin"
  more precisely — `consultation.amend` is ADMIN-tier today, the same
  stance every other sensitive post-hoc correction (billing void/
  refund) already takes.

## Next phase

No further phase was specified beyond this point in the master spec
sequencing this session has followed (Phases 3, 5-14). Every
concretely-scoped, previously-flagged gap from Phases 9 through 14 is
now closed. Remaining unimplemented master spec sections are either
cross-cutting non-functional requirements expected to already be
honored throughout (loading/empty/error states, accessibility,
security, concurrency, API design conventions, etc.) or later-program-
phase features with their own significant scope (IPD/beds/OT
compatibility, a real insurance claims workflow, multilingual patient
documents, module licensing) that don't follow as a natural next
increment on top of what exists today.
