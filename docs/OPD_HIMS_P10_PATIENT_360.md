# OPD/HIMS Master Spec — Phase 10: Patient 360 / Unified Timeline

Follows `docs/OPD_HIMS_P9_BILLING_PAYMENT.md`.

## Scope

The master spec's Phase 10 goal (section 44) is a cross-domain event
timeline for a patient, replacing "patient history exists only as a
list of past appointments" (`docs/OPD_HIMS_P0_AUDIT.md` section 5).
Implemented: `GET /api/patients/{id}/timeline` — every visit (encounter)
for a patient, most recent first, each carrying everything that
happened during it: vitals, consultation, orders + results, prescription
+ dispensing, billing (charges + payments). A new frontend modal
(`PatientTimelineModal.tsx`) renders it as an expandable list, one card
per visit.

**Deliberately a read-only aggregation, no new business data.** Every
table this pulls from already exists (Phases 3/5-9: `encounters`,
`vitals`, `consultations`, `orders`/`order_results`,
`prescriptions`/`prescription_items`/`pharmacy_dispense_records`,
`invoices`/`charges`/`payments`) — this phase adds no migration at all,
just a query layer and a view on top of what's already there.

**Shaped by visit, not as one flat interleaved event list.** Every one
of the tables aggregated here is itself scoped to exactly one encounter
(most with a `UNIQUE` FK: `consultations`, `prescriptions`, `invoices`
each have at most one row per encounter), so grouping by encounter is
the structure the data already has, not an invented one. A real
clinical/billing history also reads visit by visit in practice — "what
happened at the July 12th visit" is a more useful question to answer
than "list everything since 2019, interleaved by timestamp." The master
spec's own phrase "cross-domain event timeline" is satisfied by each
visit card itself being that cross-domain view (vitals + consultation +
orders + prescription + billing, one card, one visit), not by flattening
those domains into a single un-scoped feed.

## What changed

**One service function, five bulk queries, not N+1 per encounter.**
`get_patient_timeline_service` fetches the patient's encounters once,
then fetches vitals/consultations/orders(+results)/prescriptions(+items
+dispenses)/invoices(+charges+payments) with `encounter_id = ANY(%s)` —
one query per table (seven total, since orders' results and
prescriptions' items+dispenses each need one more), grouped in Python
by encounter/order/prescription id. A patient with years of visits
means a handful of `IN`-list queries, not dozens of round trips.

**Follows a patient-merge redirect, same convention as UHID lookup.**
If the requested `patient_id` was retired by a merge
(`patients.merged_into_id` set — `app/services/patient_merge.py`), the
timeline resolves to the surviving patient instead (whose encounters
already absorbed the retired patient's, per `merge_patients`'s own
`UPDATE encounters SET patient_id = ...`), with `redirected_from` in the
response so the frontend can show a note — the exact same shape
`app/services/uhid.py`'s `resolve_patient_by_uhid` already established
for the same situation. Without this, opening a merged-away patient's
timeline would silently read as "no visits," even though the visits are
real and now live under a different id.

**Bare `get_current_staff`, not `require_permission`.** Same RBAC tier
as every other endpoint in `app/api/patients.py` — that router's own
existing convention (`migrations/0031_rbac_decomposition.sql`'s header
comment: every `patients.py` endpoint gated only by authentication, no
role check, is deliberately untouched). A timeline is a read, the same
tier as viewing a patient's record at all.

**Supersedes, not adds alongside, the old visit-history modal.**
`PatientVisitHistoryModal.tsx` (a flat date/doctor/status list, no
drill-down — the exact gap this phase exists to fill) is replaced
in-place by `PatientTimelineModal.tsx` behind the same "N visits" link
in `PatientsPanel.tsx`; the old file is deleted rather than left as dead
code alongside the new one.

## Files changed

- `app/services/patient_timeline_service.py` — new.
- `app/api/patients.py` — new `GET /{patient_id}/timeline` endpoint.
- `tests/test_patient_timeline.py` — new, 7 tests.
- `frontend/src/types.ts` — `TimelineVitals`/`TimelineConsultation`/
  `TimelineOrder`/`TimelineOrderResult`/`TimelinePrescription`/
  `TimelinePrescriptionItem`/`TimelineDispense`/`TimelineInvoice`/
  `TimelineCharge`/`TimelinePayment`/`TimelineVisit`/`PatientTimeline`.
- `frontend/src/api.ts` — `getPatientTimeline`.
- `frontend/src/admin/PatientTimelineModal.tsx` — new: the Patient 360
  view, one expandable card per visit.
- `frontend/src/admin/PatientsPanel.tsx` — swapped
  `PatientVisitHistoryModal` for `PatientTimelineModal` behind the same
  "N visits" link.
- `frontend/src/admin/PatientVisitHistoryModal.tsx` — deleted
  (superseded; see "What changed" above).
- `frontend/src/styles.css` — `.patient-timeline-modal`/
  `.patient-timeline-visit-header`/`.patient-timeline-visit-body`/
  `.patient-timeline-vitals-row`.

## Database changes

None. This phase adds no migration — see "Scope" above.

## API changes

New: `GET /api/patients/{patient_id}/timeline` (bare authenticated
staff, any role).

## Tests

7 new (`tests/test_patient_timeline.py`): a patient with no visits
returns an empty list, not an error; 404 for a nonexistent patient; 401
unauthenticated; a full visit (vitals, completed consultation, an order
with a recorded result, a prescribed-and-dispensed prescription item, a
charge and a payment) round-trips correctly through every nested level;
multiple visits sort most-recent-first; a merged-away patient's timeline
redirects to the survivor and still shows the retired patient's visit;
a plain STAFF account (not just ADMIN) can read it.

Full suite: 549 passed (542 + 7 new), 1 pre-existing failure —
`test_booking_uses_doctor_specific_timezone`, the same deterministic
real-wall-clock-time flake flagged in every phase report since Phase 7
(root-caused again this run: current time past 9am America/New_York
means a fresh doctor's "first available slot today" correctly rolls to
10am, not 9am — nothing in this phase's diff touches scheduling,
availability, or timezone handling at all).

**Browser-verified end-to-end**: ran the app locally, built a complete
visit for a fresh patient through every Phase 5-9 workflow (vitals,
consultation, a lab order with a result, a prescribed-and-dispensed
medicine, a billed and paid charge), then opened Patients → "1 visit" →
confirmed the timeline modal correctly showed every piece — vitals
summary line, consultation card with diagnosis, orders table with
inline results, prescription table with dispensed quantity, and a
billing summary line with the paid amount — and confirmed the
expand/collapse toggle on the visit header works. `npx tsc -b`,
`npm run lint`, and `npm run build` all pass clean (no new warnings).

## Known gaps / deliberately out of scope

- **No pagination.** A patient with an unusually long visit history
  gets every visit in one response. Real clinic volumes (a handful to a
  few dozen visits per patient) don't need it yet; add a `limit`/cursor
  if that assumption stops holding.
- **No printable/exportable view.** Screen-only, same stance as Phase
  9's bill summary.
- **No audit-log integration.** `migrations/0033_audit_log.sql`'s
  queryable audit trail (master spec section 55, main's independently-
  shipped work) is a different, staff-action-scoped log, not part of
  this patient-facing clinical timeline — deliberately not merged into
  one view here.
- **No cross-hospital view.** Scoped to the requesting staff's own
  `hospital_id` (matching `get_patient_by_uhid`'s existing convention),
  same single-tenant-in-practice stance as the rest of this codebase
  since `migrations/0027_hospital_tenant_context.sql`.

## Next phase

No further phase was specified beyond this point in the master spec
sequencing this session has followed (Phases 3, 5-10). Master spec
sections not yet implemented and not audited for a specific next phase
include packages/insurance-TPA extension points (sections 39-40) and an
exception/alerting engine (sections 46-47), both already flagged as
gaps in `docs/OPD_HIMS_P0_AUDIT.md` section 5.
