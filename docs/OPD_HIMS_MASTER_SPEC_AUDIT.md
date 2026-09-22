# OPD/HIMS Master Spec — Coverage Audit

Verification pass against the full ~100-section HospitalOS master
specification, checked against the actual code on `main` as of PR #112
(commit `0dd8007`) — every phase report through
`docs/OPD_HIMS_P14_CONSULTATION_AMENDMENT.md`, plus main's own
independently-shipped work (RBAC decomposition, patient merge, audit
log, hospital tenant context).

**Method**: for each section, the codebase was actually checked (grep/
read against the merged tree), not recalled from memory alone. ✅ means
verified present and working. 🟡 means partially present — the concrete
evidence for both what exists and what's missing is given. ❌ means
verified absent. This is a status report, not a to-do list — per
instruction, nothing was implemented or changed during this audit.

---

## 1. Primary product vision / 2-6. Core principles

| # | Item | Status |
|---|---|---|
| 1 | One connected patient journey (not disconnected pages) | ✅ Patient → UHID → Encounter → Appointment/Walk-in → Check-in → Queue → Triage → Consultation → Orders → Prescription → Pharmacy → Billing → Payment → Timeline all exist and are actually linked by foreign keys (`encounter_id` threads through vitals/consultations/orders/prescriptions/invoices), not separate silos. |
| Principle 1 | Permanent UHID | ✅ `patients.uhid`, generated format `HOS-NNNNNNN` (`migrations` patient identity work). |
| Principle 2 | Encounter is the clinical context | ✅ `encounters` table (Phase 3), every clinical/billing row FKs to it, not to the appointment directly. |
| Principle 3 | Never create disconnected modules (one generic Order, not OPD-Lab/IPD-Lab/Emergency-Lab) | ✅ Single `orders` table with `order_type` discriminator (LAB/RADIOLOGY/PROCEDURE/SERVICE/EXTERNAL_REFERRAL), reused as-is through every phase rather than per-type tables. |
| Principle 4 | Patient context header always visible | 🟡 Every clinical/billing screen shows patient name/UHID/doctor context (`.patient-context-meta` CSS class reused everywhere), but there's no single shared `PatientHeader` React component — each screen (ConsultationWorkspace, AppointmentBillingPanel, PatientTimelineModal, etc.) reimplements the markup locally. Functionally present, not componentized. |
| Principle 5 | Role-based work (Reception/Nurse/Doctor/Lab/Radiology/Pharmacist/Cashier/Admin see different workflows) | ❌ **Not implemented.** `migrations/0031_rbac_decomposition.sql` seeded `DOCTOR`/`NURSE`/`RECEPTIONIST`/`LAB_TECH`/`PHARMACIST`/`BILLING` roles but its own comment says they're "unused — no staff account holds one of these yet, and no permission is granted to any of them, until their own modules exist." Every real login is `ADMIN` or plain `STAFF`; a STAFF session can do triage, consultation, orders, pharmacy, and billing all through the same account. This is a genuine, unaddressed gap — role-differentiated workflows never got built. |

## 7-9. Information architecture / navigation / screen types

- ✅ Sidebar matches the target IA reasonably well (Dashboard/Appointments/Doctors/Patients/Departments/Appointment Types/Pharmacy/Packages/Staff Accounts/Billing), with `disabled`/"Coming soon" entries for Analytics/Settings — exactly the pattern section 13 asks for.
- 🟡 IPD/Emergency/Insurance-TPA future sections aren't shown even as disabled placeholders (spec explicitly says don't build them, but also shows them in the target nav as visible-but-future; current sidebar omits them entirely rather than graying them out).
- ✅ Full workspaces (Dashboard, Queue, ConsultationWorkspace, Billing, Pharmacy), guided workflows (BookAppointmentPanel's step wizard, Patient Registration), drawers/modals (AppointmentDetailsModal, PatientTimelineModal), and tabs (ConsultationWorkspace's Triage/Consultation/Orders/Prescription/Billing) all follow the spec's own screen-type taxonomy correctly — confirmed via `docs/OPD_HIMS_P0_AUDIT.md`'s own note that this was already true before this session's work began.

## 10-13. Design system / colors / typography / app shell

- ✅ `frontend/src/styles.css` + `docs/design-system.md` define one consistent token system (colors, spacing, radius); every phase in this session reused existing classes (`data-table`, `pill status-*`, `modal-panel`, `btn`/`btn-secondary`/`btn-danger`, `state-block`) rather than inventing new visual patterns.
- ✅ Status pills use consistent semantic tones (`tone-success`/`tone-warning`/`tone-danger`/`tone-info`) applied the same way across Dashboard stat cards, exception rows, billing statuses.
- ✅ Global shell (sidebar + top bar + main content) matches section 13's layout exactly.

## 14-15. Global search / notification center

- ❌ **Not implemented.** No cross-entity search bar (UHID/name/mobile/appointment/encounter/order/bill from one box) exists anywhere in the frontend — only per-page search fields scoped to that page's own list (patients search, appointments search). Confirmed via grep: no `GlobalSearch` component.
- ❌ **Not implemented.** No notification/alert bell or center exists. The closest thing is Phase 11's "Needs Attention" exceptions widget on the Dashboard, which is a *list*, not a persistent notification center, and only covers the six exception types it defines — not the broader "new patient arrived / lab result available / prescription ready" event stream section 15 describes.

## 16-18. Patient registration workflow

- ✅ Search-before-register flow exists (`PatientsPanel`'s search, `BookAppointmentPanel`'s walk-in patient search with "Existing patient found" / "+ Register new patient").
- ✅ Duplicate detection exists and is real: `app/services/patient_duplicate_detection.py`, `patient_duplicate_reviews` table, surfaced in the patient-creation response (`possible_duplicates`) — seen live in this session's own testing (Phase 14's test patient triggered a real duplicate match against "Receipt Test Patient").
- 🟡 Registration form fields: name, mobile, DOB, gender exist (`PatientFormModal.tsx`). Email, alternate mobile, address (city/state/PIN), emergency contact, and blood group/additional-details fields from the spec's mock layout are **not present** — registration is deliberately minimal (name + mobile required, DOB/gender optional), not the full layout section 17 sketches. This was an explicit, documented design choice from earlier work (fast walk-in registration), not an oversight, but it does mean several spec-listed fields have no column at all.

## 19-22. Appointment / walk-in / follow-up / review

- ✅ Existing scheduling/availability engine reused throughout, not duplicated (repeatedly confirmed in every phase's own audit-first discipline).
- ✅ Walk-in flow matches the spec's target exactly: search/select patient → doctor → date/time → book → check-in → token (verified live in this session's own browser testing, Phases 12-14).
- ✅ Follow-up quick-pick options exist: `FOLLOW_UP_OPTIONS` in `ConsultationWorkspace.tsx` (3/7/15/30 days + custom), settable directly from the consultation.
- ✅ Review & Confirm step exists in the booking wizard before creating the appointment.
- ✅ Idempotent visit creation: booking uses exclusion constraints + advisory locks (pre-existing, preserved through every phase per this session's own "never touch the booking path" discipline).

## 23-25. Check-in / live queue / queue status model

- ✅ Check-in records real arrival/visited timestamps, issues a token, live queue view exists (`QueuePanel`), verified live multiple times this session.
- 🟡 Queue status model: spec wants `SCHEDULED → ARRIVED → CHECKED_IN → WAITING → CALLED → IN_CONSULTATION → CONSULTATION_COMPLETED → COMPLETED` plus `CANCELLED/NO_SHOW/TRANSFERRED/REFERRED/ADMITTED`. Actual `appointments.status` values are `PENDING, CONFIRMED, REJECTED, CANCELLED, CHECKED_IN, COMPLETED, NO_SHOW` — a materially simpler model. There's no distinct WAITING/CALLED/IN_CONSULTATION states (queue "waiting" vs. "in consultation" is inferred from whether a consultation row exists, not a stored status), and no TRANSFERRED/REFERRED/ADMITTED at all (consistent with IPD/referral being out of scope, but the states genuinely don't exist).

## 26-29. Triage / doctor workspace / consultation features / patient history

- ✅ Triage/vitals: BP, pulse, temp, SpO2, respiratory rate, weight, height, BMI (computed), pain score, chief complaint, priority, nursing notes — all present (`vitals` table, Phase 5), matches spec's field list exactly.
- ✅ Doctor workspace tabs match spec section 27's layout: Triage/Vitals, Consultation, Orders, Prescription, Billing (Overview/Documents/History/Timeline are folded into Patient 360 instead of being extra tabs here — a reasonable interpretation, not a gap).
- 🟡 Consultation fields: chief complaint, history, examination, diagnosis, clinical notes, follow-up all present. **Allergies and current medications are not** — there is no `allergies` field or table anywhere in the codebase (confirmed via grep across migrations/services/types — zero matches). Section 27's own patient-header mock explicitly shows "Allergies: Penicillin ⚠" and section 91 lists allergy warnings as required clinical-safety UX; neither exists.
- ✅ Save-draft/complete lifecycle with autosave-equivalent (explicit Save Draft action, not lost on navigation since the draft row exists in the DB from first load).
- ✅ Patient history: Phase 10's Patient 360 timeline gives exactly this ("previous visits/diagnoses/prescriptions/lab results/vitals" without leaving the workflow), though it's reached as a separate modal from Patients, not inline inside the Doctor Consultation screen itself the way section 29's "without leaving the workflow" implies — a doctor mid-consultation has to leave the consultation tab to see it, not view it side-by-side.

## 30-31. Orders workspace / external referral

- ✅ Generic orders table with `order_type` (LAB/RADIOLOGY/PROCEDURE/SERVICE/EXTERNAL_REFERRAL), priority, clinical indication, status, ordering doctor — matches spec's field list.
- ✅ External referral works exactly as specified: `order_type = 'EXTERNAL_REFERRAL'` requires a destination, doesn't depend on an internal Lab/Radiology module existing.
- 🟡 No dedicated "search test/service" catalog UI — order descriptions are staff-typed free text, not selected from a searchable test/service list (an explicit, documented decision every phase since Phase 6 has repeated: "no price catalog exists yet, same staff-entered stance").

## 32-34. Lab / radiology workflow / results

- 🟡 Implemented as ONE simplified generic pipeline for all order types (`ORDERED → IN_PROGRESS → COMPLETED`, or `CANCELLED`), not the spec's two separate, more granular pipelines (Lab: Pending Collection → Collected → Processing → Result Pending → Verified → Released; Radiology: Ordered → Scheduled → In Progress → Reporting → Reported → Verified). There's no sample-collection tracking, no separate verify-then-release step, no radiology-specific Findings/Impression/Technique structure — `order_results` is one generic parameter/value/unit/reference-range/abnormal/critical table for both. Functionally: a doctor creates an order and later sees a result from the same encounter (the spec's own Phase 7 exit criterion) — that works. The richer, type-specific workflow granularity does not exist.
- ✅ Doctor sees results inline via Patient 360 timeline and the order's own result view — doesn't have to navigate to a separate Laboratory module.

## 35-37. Prescription / pharmacy / inventory-aware dispensing

- ✅ Prescription fields match spec: medicine name, generic name, dosage, route, frequency, duration, quantity, food/special instructions.
- 🟡 Lifecycle is `DRAFT → PRESCRIBED → CANCELLED`, not the spec's `Draft → Prescribed → Sent to Pharmacy → Partially Dispensed → Dispensed → Cancelled`. "Partially dispensed" and "dispensed" are tracked at the *item* level (`quantity_dispensed` vs `quantity`), not as a prescription-level status — functionally equivalent (the pharmacy queue correctly shows partial vs. fully dispensed items) but the status vocabulary doesn't literally match.
- ✅ Inventory-aware dispensing is real: stock, batch, expiry, available quantity, partial dispensing, and an actual `pharmacy_dispense_records` transaction log — not a bare "mark dispensed" flag, exactly per section 37's requirement.

## 38-42. Billing / packages / insurance-TPA / payment / receipt

- ✅ Charges aggregate from Consultation/Lab/Radiology/Procedure/Pharmacy/Package/Other sources (`charges.source_type`). Consumables specifically has no distinct source type (falls under OTHER) — minor gap against the spec's exact list.
- ✅ Gross/Discount/Tax/Net/Paid/Balance all present and computed server-side, tax rate configurable per-invoice (never hard-coded).
- ✅ Packages (Phase 12): real catalog, billed as a single line item, no double-billing possible by construction.
- ✅ Insurance/TPA extension point (Phase 12): `invoices.bill_type` six-way classification. Payer/policy/pre-auth/co-pay/claim fields deliberately not built — the spec's own text labels those "Future fields" and says not to fake them now; this was a scoped decision, not an omission.
- ✅ Payment methods (Cash/UPI/Card/Bank Transfer/Insurance/Other), receipt number, amount, method, transaction ID, cashier, timestamp, status all present. Duplicate-payment prevention: a partial unique index on `transaction_id` per invoice (`DuplicateTransactionId`), plus `PaymentExceedsBalance` guarding against overpayment.
- 🟡 Payment statuses: spec wants Pending/Paid/Partially Paid/Refunded/Voided as *payment*-level statuses. Actual: `payments.status` is only `COMPLETED`/`VOIDED`; "Partially Paid"/"Paid"/"Refunded" are *invoice*-level derived values (`payment_status` computed from the sum of payments), and a payment itself has a `refunded_amount` field rather than a `REFUNDED` status. There's no "Pending" payment state at all — a payment row is only ever created already-completed (no pending/failed intermediate state is modeled, so "Journey G — Payment Failure" from section 77 has nothing to represent a failed attempt with).
- ✅ Receipt (Phase 13): every field section 42 lists (Hospital/Receipt number/Patient/UHID/Encounter/Invoice/Services/Gross/Discount/Tax/Net/Paid/Payment method/Transaction ID/Cashier/Date-time), Print/Download (via native print dialog)/Send to patient all present and browser-verified.

## 43. Visit completion

- ❌ **Not implemented as specified.** There is no consolidated "✓ Consultation completed / ✓ Orders created / ✓ Prescription created / ✓ Billing completed / ✓ Payment completed / ✓ Follow-up scheduled" checklist screen anywhere. "Complete OPD Visit" exists only as a single status-flip action (`POST /appointments/{id}/complete`, the front-desk "Mark completed" button) with no precondition checklist shown to staff before or after clicking it — a visit can be marked completed with no orders, no billing, and no payment, and nothing surfaces that. (Phase 11's exception engine does *separately* flag "billing not started"/"payment pending" as ongoing alerts, which is a different, complementary mechanism — not this checklist.)
- ✅ "Do not allow accidental reopening without an authorized amendment workflow" — this half is covered: Phase 14 built exactly this for consultations (COMPLETED locks the form; amendment requires a reason and RBAC). Note this only covers consultations — appointments/encounters themselves have no "reopen" concept at all (COMPLETED is a terminal status with no reopen action, which trivially satisfies "no accidental reopening" but not via a real amendment workflow at that level).

## 44-47. Patient 360 / OPD command center / exception engine

- ✅ Patient 360 (Phase 10): full cross-domain timeline, matches spec's example event list conceptually (vitals, consultation, orders, results, prescription, dispensing, billing all shown per visit).
- 🟡 OPD Command Center (section 45): the Dashboard exists with real KPIs (today's/upcoming/pending/confirmed/cancelled/rejected/completed/visited appointment counts, a billing-collections chart), but **not** the specific KPIs section 45 names: no "Average Waiting Time" or "Average Consultation Time" stat card on the Dashboard itself (average wait time *is* computed and shown, but only in the separate Appointments page's footer line, not the command-center KPI row). No department-load or doctor-load breakdown. The "⚠ Cardiology waiting time above threshold" / "⚠ Laboratory collection backlog" style operational warnings are covered by Phase 11's Exception Engine instead, which is click-through-actionable exactly as section 45 asks — just surfaced as a list, not inline warning banners on the KPI cards themselves.
- ✅ Exception Engine (Phase 11): six live-computed, non-noisy, actionable exception types, each with what/why/who/action/status — directly matches sections 46-47's requirements and explicit anti-pattern warning ("do NOT create hundreds of noisy alerts"). "Doctor running late" was explicitly and deliberately not built (documented rationale: no reliable schedule-adherence signal without risking exactly the noisy-alert problem the spec warns against).

## 48-51. Loading / empty / error / success states

- ✅ Loading states: `state-block` + spinner pattern used consistently across every panel built or touched this session (Dashboard, Exceptions, Packages, Billing, Timeline, Receipt).
- ✅ Empty states: consistently explain what's missing and what to do next (e.g., Packages panel: "No packages yet. Add one to bill it as a single line item on a visit."; Exceptions widget: "Nothing overdue right now."), matching section 49's own example format.
- 🟡 Error states: shown as inline `<p className="error">` messages reading the API's own error detail — reasonably human-readable in practice ("A package with that name already exists", "Only a completed consultation can be amended") but not the specific `errorCode`-keyed structured format section 61 sketches, and no explicit "preserve entered data / allow retry without duplicate submission" pattern beyond disabling the submit button while a request is in flight.
- ✅ Success states show real detail (booking confirmation shows patient/doctor/date/time/token, not a bare "Success" toast).

## 52-53. Accessibility / responsiveness

- 🟡 Meaningful but partial: `aria-label`/`aria-describedby`/`role` attributes appear in 89 places across 32 files — modals, buttons, and form fields are reasonably covered. No systematic audit exists confirming full keyboard-navigability or contrast compliance across every screen; this was never a dedicated phase (section 77's own Phase 12 — "Production Hardening" — which would have covered a real accessibility pass, was not undertaken in this session).
- 🟡 Responsiveness: the design system's CSS uses a desktop-first layout; no dedicated tablet/mobile breakpoint audit was performed in any phase this session touched.

## 54. Printing

- 🟡 Only the Phase 13 receipt has a real, dedicated print layout (`.receipt-print-area` + `@media print` that hides everything else on the page). "Print Token" (`BookAppointmentPanel.tsx`) exists as a button calling `window.print()` but has **no dedicated print CSS scoping it** — clicking it would print the entire visible page (sidebar included), not a clean token slip. Appointment slip, patient registration summary, prescription, lab requisition, radiology requisition, and invoice have no print views at all.

## 55. Audit log

- ✅ `audit_logs` table (main's independent work) + `record_audit_log()` called from within the same transaction as the mutating action (not a separate, spoofable write path) — used by Phase 12's package CRUD, and pre-existing across appointment/doctor/department mutations.
- 🟡 Coverage is real but not exhaustive against the spec's own example list: "Consultation completed", "Order cancelled", "Prescription changed", "Bill reopened", "Payment voided", "Refund created" are **not** all wired to `record_audit_log` specifically — Phase 14's consultation amendment, for instance, has its own dedicated `consultation_amendments` history table (arguably a *better* audit trail for that one action, since it captures the full before/after snapshot) but doesn't also write a generic `audit_logs` row.
- ❌ No frontend page to view the audit log. The backend `GET /api/audit-log` endpoint exists (filterable, capped at 200 rows) but nothing in the sidebar or admin app links to it.

## 56-58. Security / concurrency / idempotency

- ✅ Backend enforces every permission via `require_permission()`, never trusts the frontend — confirmed throughout every phase's RBAC-gated endpoint.
- 🟡 The specific negative examples section 56 gives (reception can't void payments, lab tech can't touch billing, cashier can't edit diagnosis) are *structurally* true only insofar as STAFF-vs-ADMIN separates financial-write actions from clinical-write actions — but since there's no real LAB_TECH/CASHIER role in practice (see Principle 5 above), these are enforced by "is this person an ADMIN," not by the role-specific boundaries the spec describes.
- ✅ Concurrency: the pre-existing advisory-lock/exclusion-constraint booking path was explicitly preserved and never touched across all ten phases in this session; `FOR UPDATE` row locks used throughout billing (invoice/payment mutations) and consultation amendment.
- ✅ Idempotency: `ON CONFLICT DO NOTHING` patterns for encounter/consultation creation, unique constraints preventing duplicate tokens/transaction IDs — genuine database-level safeguards (6+ exclusion/conflict constraints found), not merely frontend double-click prevention. No formal `Idempotency-Key` HTTP header mechanism exists, but the underlying guarantee (repeated requests don't create duplicates) is real where it matters (booking, payments, encounters).

## 59-65. Database design / API design / error format / pagination / search / validation / date-time

- ✅ Every phase this session ran reused existing entities and extended additively — no destructive migrations, confirmed via `scripts/migrate.py`'s own append-only convention and every phase report's explicit "no destructive change" discipline.
- ✅ API conventions consistent: REST resource-noun paths, Bearer auth, consistent nesting (`/appointments/{id}/...`).
- ❌ **API error format does not match the spec's example.** No `{success, errorCode, message, details}` JSON shape anywhere — every endpoint uses FastAPI's default `{"detail": "..."}` HTTPException shape. This is a pre-existing, consistent convention (not something any phase introduced), so it satisfies "standardize errors... if the existing application does not already have a suitable convention" in spirit (there is one consistent convention), but it is not the spec's literal example.
- ❌ **No pagination on large list endpoints.** Verified directly: `GET /api/patients` has no `LIMIT`/`WHERE` filtering at all — every patient row loads on every call. `GET /api/appointments` explicitly documents in its own docstring that "the unfiltered call still returns everything." `GET /api/audit-log` is the one exception (filtered + capped at 200). This is a real, currently-unaddressed gap against both section 62 and section 80's "avoid: load entire table" warning.
- ✅ Search exists server-side for patients (name/UHID/mobile) and appointments (multi-field), not client-side filtering of a fully-loaded list.
- ✅ Validation is layered: Pydantic models (frontend-facing), service-level checks, and DB constraints (CHECK/UNIQUE/EXCLUDE) all present — confirmed throughout every phase (e.g., Phase 12's package price `CHECK (price > 0)` at the DB layer, not just the API model).
- ✅ Date/time handling is a session-wide, repeatedly-reinforced discipline — every phase report this session explicitly checked and fixed timezone-display bugs where found (see Phase 9's appointments-listing docstring above, an example from this exact audit).

## 66-69. Indian hospital requirements / module licensing / degradation / patient merge

- 🟡 INR (₹ symbol used throughout billing/receipt UI) ✅, UPI (a real payment method) ✅, GST-equivalent (`tax_rate`, configurable per-invoice, never hard-coded) ✅, TPA/corporate/government-scheme (`bill_type`) ✅. Multilingual patient documents ❌ (no i18n infrastructure exists at all — every string is hard-coded English).
- ❌ **Module licensing/enablement not implemented.** No `Licensed`/`Enabled`/`Available` concept exists anywhere — every module that exists is simply always on for every hospital (consistent with this app still being single-tenant in practice per `migrations/0027_hospital_tenant_context.sql`'s own "purely structural" framing).
- 🟡 Module degradation: the one concrete instance the spec asks for — "Laboratory disabled → Doctor still orders CBC via External Referral" — **does work**, because External Referral was built as a first-class order type from Phase 6 onward, not contingent on an internal Lab module existing. But there's no general `HIDDEN`/`EXTERNAL`/`BLOCKED` framework; this works by construction (orders were never actually gated on a "Lab module enabled" flag, since no such flag exists), not because a degradation system was built.
- ✅ Patient merge readiness (main's independent work): `patient_merges`/`patient_duplicate_reviews`, `merged_into_id`, a real authorized-merge workflow with unmerge safety checks — goes beyond "readiness" into a working feature. Mobile number is explicitly not used as permanent identity (UHID is).

## 70. Patient data safety

- ✅ No casual deletion anywhere in the clinical/financial paths verified this session: charges/payments/invoices use VOID-with-reason (never DELETE); consultations now have Phase 14's real amendment workflow (archive-then-update, never overwrite). This section is well covered for the record types this session touched.
- 🟡 The `Active/Inactive/Cancelled/Voided/Archived` vocabulary isn't applied uniformly — different entities use different subsets (patients have no soft-delete state at all; departments/doctors/appointment-types use `active` boolean; orders/prescriptions/invoices/charges/payments use their own domain-specific status enums). Functionally soft-delete-safe throughout, but not one consistent status vocabulary.

## 71. Production-ready UX (the 13 questions)

Spot-checked against Phase 12-14's own work (the parts of the app this session built or touched): "what if another user changed it" (row-level `FOR UPDATE` locks in billing/amendment), "what if payment already happened" (`PaymentExceedsBalance`, void-requires-no-payments checks), "what if the doctor slot disappeared" (pre-existing exclusion-constraint booking failure handling) are all genuinely handled with specific error paths, not just happy-path code. A full audit of all 13 questions across every screen in the app (including screens from before this session) was not performed here — that would be its own dedicated pass.

## 72. Complete OPD screen inventory (34 screens)

| Screen | Status |
|---|---|
| 1. OPD Command Center | ✅ Dashboard (partial KPIs, see §45 above) |
| 2. Patient Search | ✅ |
| 3. Patient Registration | ✅ (minimal field set, see §16-18) |
| 4. Patient 360 | ✅ Phase 10 |
| 5. New OPD Visit | ✅ |
| 6. Review & Confirm | ✅ |
| 7. Check-in | ✅ |
| 8. Token | ✅ (issuance yes; dedicated print layout no, see §54) |
| 9. Live Queue | ✅ |
| 10. Triage | ✅ |
| 11. Doctor Consultation | ✅ |
| 12. Orders | ✅ |
| 13. Results | ✅ (inline, generic model) |
| 14. Prescription | ✅ |
| 15. Pharmacy | ✅ |
| 16. Billing | ✅ |
| 17. Payment | ✅ |
| 18. Receipt | ✅ Phase 13 |
| 19. Follow-up | ✅ (as a consultation field, not a separate screen) |
| 20. Visit Completion | ❌ See §43 |
| 21. Appointment Calendar | ✅ (`Calendar.tsx`/`MonthCalendar.tsx`) |
| 22. Doctor Queue | ✅ (`QueuePanel`, per-doctor) |
| 23. Department Queue | ❌ Not found as its own view |
| 24. Laboratory Worklist | ❌ No dedicated lab worklist screen (orders queue is generic, not lab-specific) |
| 25. Laboratory Result Entry | 🟡 Generic order-result entry exists, not lab-specific |
| 26. Radiology Worklist | ❌ |
| 27. Radiology Report | 🟡 Generic order-result entry only, no Findings/Impression structure |
| 28. External Referral | ✅ |
| 29. Billing History | 🟡 Per-visit billing exists; no dedicated cross-visit billing-history screen |
| 30. Payment History | 🟡 Per-visit payments list exists; no dedicated cross-visit payment-history screen |
| 31. Audit Log | 🟡 Backend only, no frontend page (see §55) |
| 32. OPD Reports | 🟡 Dashboard billing-trends chart exists; no dedicated reports screen |
| 33. Waiting-Time Analytics | ❌ Not built as its own screen (raw average shown inline on Appointments page) |
| 34. Exceptions / Alerts | ✅ Phase 11 |

**20 of 34 fully built, 8 partial, 6 not built** — consistent with the spec's own "do not necessarily implement all 34 at once."

## 73-76. Screen interconnection / no dead ends / IPD future extension / IPD compatibility

- ✅ The screen flow (Command Center → New Visit → Patient Search → ... → Patient 360) matches the spec's interconnection diagram; verified live via this session's own browser walkthroughs of the full walk-in-to-payment path multiple times (Phases 12-14).
- ✅ No dead ends found in the screens this session touched — every completion state offers real next actions (booking confirmation offers Check In/View Patient/Print Token; billing offers void/edit-terms/add-charge/record-payment; the receipt offers Print/Send).
- ❌ **"Admit to IPD" disposition scaffold does not exist.** No `disposition` field, no Follow-up/Refer/Admit-to-IPD/Emergency choice in the consultation workspace — this was never built even as a stub, unlike External Referral which was.
- 🟡 IPD compatibility: `encounters.encounter_type` exists as a column specifically designed for this (per Phase 3's own migration comment), but its CHECK constraint currently only allows `'OPD'` — the column is architecturally present but not yet actually open to a second value, so "add IPD later without redesigning the patient model" is *aimed at* but not proven, since nothing has tried to insert a non-OPD encounter yet.

## 77. Phased implementation plan

This session's own 12 phases (3, 5-14, skipping 1-2/4 as already covered by pre-existing WEB_P* work per the Phase 0 audit) map reasonably well onto the spec's Phase 0-13 structure, with two differences worth naming honestly:
- The spec's **Phase 11 ("Operational Command Center")** — dashboard KPIs, waiting time, consultation time, queue/department/doctor load, no-shows, backlog — is only partially done; this session's own "Phase 11" was actually the Exception Engine (spec section 46-47), a different, narrower slice than the spec's Phase 11 describes.
- The spec's **Phase 12 ("Production Hardening")** — performance optimization, indexing, caching, a dedicated security review, concurrency/idempotency *testing* (as opposed to the underlying mechanisms, which do exist), accessibility, responsive, print, and browser testing as a *dedicated pass* — was **not undertaken** as its own phase in this session.
- The spec's **Phase 13 ("End-to-End Validation")** — running the 8 named journeys (A-H) as an explicit validation pass — was not run as a dedicated exercise, though most of Journey A/B/C's individual steps were exercised piecemeal during each feature phase's own browser verification.

## 78-79. Testing matrix / test data

- ✅ 581 test functions across 62 files — substantial coverage, verified by direct count.
- ❌ Tests are not organized around the spec's named role/configuration matrix (Reception/Nurse/Doctor/Lab-Tech/Radiology-Tech/Radiologist/Pharmacist/Cashier/Admin × OPD-only/OPD+Lab/OPD+Lab+Radiology/etc.) — since those distinct roles don't exist in the RBAC layer (see Principle 5), there's nothing to write that matrix of tests against yet.
- ✅ Realistic seed/test data patterns exist in `tests/helpers.py` (department/doctor/appointment-type seeding, multiple schedules) and this session's own manual demo-data setup (multiple departments, doctors, patients, duplicate-candidate patients) used for every phase's browser verification.

## 80-82. Performance / data integrity / logging

- ❌ Performance: see §62 above — patients and appointments load unpaginated. This directly contradicts section 80's explicit "avoid: load entire table" warning for datasets that can grow.
- ✅ Data integrity: verified DB-level constraints prevent duplicate tokens, duplicate transaction IDs, and (via exclusion constraints) double-booked slots; `orders`/`payments`/`bills` all require their parent encounter/invoice to exist by FK, not by convention alone.
- ✅ Structured logging exists: `app/logging_config.py`, request-ID correlation (`app.access` logger used throughout, confirmed via grep), consistent with section 82's field list (though not verified field-by-field against every single log line).

## 83-84. Migration strategy / code quality

- ✅ Every migration this session wrote was additive (`ADD COLUMN`, `CREATE TABLE`, `ADD CONSTRAINT` with a drop-and-readd for CHECK constraints only) — zero destructive migrations across 10 phases, confirmed by the migrations directory itself (`0028` through `0041`, monotonically additive).
- 🟡 Code quality: no hard-coded prices/tax/doctor-IDs found in the phases this session built (tax rate is per-invoice, package prices are staff-entered via the catalog, not constants) — but no dedicated code-quality audit pass was run against the *entire* codebase (including pre-existing code from before this session).

## 85. Frontend component strategy

- 🟡 Reuse is strong at the *pattern* level (every phase reused `data-table`, `pill`, `modal-panel`, `state-block`, `AlertDialog`) but weak at the *component* level for a few of the spec's named components: no shared `PatientHeader`, `StatusBadge` (each screen builds its own `<span className="pill status-...">` inline rather than a shared component), `Timeline` (Phase 10's timeline rendering is bespoke to `PatientTimelineModal`, not a reusable `Timeline` component), or `ConfirmationDialog` beyond the existing `AlertDialog` primitive (which is itself reused correctly).

## 86-91. UX speed / keyboard / tables / drawers / confirmation / clinical safety

- ✅ Walk-in flow genuinely is fast (search → select → doctor → slot → check-in, verified live, no unnecessary intermediate screens).
- 🟡 Keyboard support exists in a minority of components (7 files use explicit Enter/Escape key handling) — not a systematic keyboard-first pattern across reception/billing screens.
- ✅ Tables follow the compact-columns-plus-drawer pattern (e.g., PatientsPanel's table shows Name/UHID/Mobile/Last Visit/Visits/Actions, with fuller detail behind "View/Edit" or the timeline drill-in) — matches section 88.
- ✅ Drawer/modal quick-views exist (AppointmentDetailsModal) matching section 89's pattern.
- ✅ Confirmation UX: dangerous actions (void bill, void charge, void payment, refund, delete draft) all require either a typed reason (AlertDialog-style prompts) or an explicit confirm dialog — verified across every billing endpoint touched this session.
- 🟡 Clinical safety UX: critical-result flagging exists (`order_results.is_critical`, visually flagged per Phase 7); duplicate-patient warning exists and is real (Phase 2-era work, confirmed live). **Allergy warnings and high-risk-medication warnings do not exist** (no allergy data model at all, see §26-29 above) — this is the most concrete, repeatedly-surfaced gap in this whole audit.

## 92-94. No fake intelligence / HospitalOS USP / product feel

- ✅ No AI/ML features were added anywhere in this session — every feature (exceptions, timeline, amendment) is deterministic, rule-based logic over real data, consistent with section 92's explicit instruction.
- 🟡 Section 93's "Patient Flow Intelligence" vision (where is the patient / what's pending / who needs to act, shown as a per-patient checklist like the mock example) is **conceptually** delivered by Phase 11's Exception Engine (which does answer "who needs to act" and "what's pending" hospital-wide) and Phase 10's Patient 360 (which answers "what happened" per patient) — but not as the specific per-patient checklist UI section 93's example shows ("Ramesh: Registration ✓ Check-in ✓ Queue ✓ ... Billing pending ⚠"). The building blocks exist; that exact view was never assembled.

## 95-100. Screenshot references / meta-instructions

- N/A — no screenshots were supplied to this session; sections 95-97/100 are process instructions this session followed (audit-first, one phase at a time, full-stack not UI-only) rather than checkable features. Every phase report in `docs/OPD_HIMS_P*.md` documents what changed/files/DB/API/tests/known-issues/next-phase, matching section 96's own requested report format.
- Section 98 (Definition of Production Ready) — see the summary verdict below.
- Section 99 (38-step final E2E acceptance test) — not run as a single scripted end-to-end pass in this session; most individual steps were exercised piecemeal across different phases' own browser verification, but never chained together as one continuous 38-step run in one sitting.

---

## Summary verdict

**Solidly covered**: the core patient journey (registration → visit → check-in → queue → triage → consultation → orders → results → prescription → pharmacy → billing → payment → receipt → timeline) genuinely works end-to-end, is genuinely connected by real foreign keys rather than looking connected, and was verified live in a browser at every phase. Packages, insurance-TPA extension point, receipts, exceptions, and consultation amendment (the "later phase" items the spec explicitly deferred) are all now real, working features with tests and browser verification.

**The gaps that matter most**, in rough order of how much they'd affect a real hospital using this:

1. **No pagination on `GET /patients` or `GET /appointments`** — every row loads every time, will not scale, directly contradicts spec section 80's explicit warning.
2. **No allergy data model at all** — a named clinical-safety requirement (section 91) with zero implementation.
3. **Role-based work is aspirational, not real** — DOCTOR/NURSE/LAB_TECH/PHARMACIST/BILLING roles exist in the schema but no login can actually hold one; every action is gated by ADMIN-vs-STAFF only.
4. **No Visit Completion checklist** — "Complete OPD Visit" is a bare status flip with no precondition summary.
5. **No global search or notification center.**
6. **Printing is receipt-only** — token/prescription/lab-requisition/invoice print views don't exist as dedicated layouts.
7. **Section 77's Phase 12 (Production Hardening) and Phase 13 (E2E Validation)** were never run as dedicated passes.

None of these were silently skipped — most are either explicitly pre-existing (predate this session's ten phases) or explicitly deferred with a documented reason in the relevant phase report. This document exists to state plainly which is which, without editorializing further or starting to fix any of them.
