# OPD/HIMS Master Spec — Phase 11: Exception Engine

Follows `docs/OPD_HIMS_P10_PATIENT_360.md`.

## Scope

The master spec's Phase 11 goal (sections 46-47) is an exception/
alerting layer: "no alerting/threshold layer exists"
(`docs/OPD_HIMS_P0_AUDIT.md` section 5), already flagged as this
codebase's own "Phase 11 territory" in `docs/OPD_HIMS_P8_PRESCRIPTION_
PHARMACY.md`'s known-gaps section. Implemented: `GET /api/exceptions`
— a live-computed list of actionable operational alerts (a patient
waiting too long, an order or prescription stuck, a bill left
unbilled or unpaid), and a "Needs Attention" widget on the admin
Dashboard.

Section 46 is explicit about the failure mode to avoid: "Do NOT create
hundreds of noisy alerts. Only create actionable exceptions", and every
exception must carry What happened / Why it matters / Who should act /
Recommended action / Current status. Section 47's example workflow ends
with "Exception resolved" being reached by *taking the action* (open
the patient, understand why, act) — not by dismissing a notification.

**Computed live, not stored.** Every condition this detects is already
represented by an existing row (an appointment still CHECKED_IN with no
vitals, an order still ORDERED, an invoice still unpaid). An
exception's "resolution" is simply that underlying row changing — record
the vitals, complete the order, take the payment — at which point it
stops matching the query and disappears from the next call on its own.
A parallel `exceptions` table with its own dismiss/resolve lifecycle
would be a second place that can drift from the truth, and the master
spec doesn't ask for one — so `current_status` is always `"OPEN"`:
anything this endpoint returns is, by construction, still true right
now. No new migration this phase, same as Phase 10.

Six exception types, each a single bulk SQL query (never per-patient
loops):

- **WAITING_FOR_TRIAGE** / **WAITING_FOR_DOCTOR** — the master spec
  lists "Patient waiting > threshold" and "Patient checked in but not
  called" as separate examples; this splits them into the two real OPD
  queue stages (checked in but no vitals yet, vs. triaged but the
  doctor hasn't started) rather than one vague "waiting" signal that
  can't distinguish who should act on it.
- **ORDER_PENDING** — covers both "Order pending too long" and "Lab
  result delayed": the same underlying signal (an order awaiting a
  result past a threshold), differing only by `order_type`, which the
  payload already reports. Threshold scales with the order's own
  `priority` (STAT 30min / URGENT 60min / ROUTINE 180min) — the one
  place this phase uses data the codebase already models (`orders.
  priority`, migrations/0030) instead of inventing a new field.
- **PRESCRIPTION_NOT_DISPENSED**, **BILLING_NOT_STARTED**,
  **PAYMENT_PENDING** — map directly to their spec examples
  ("Prescription not dispensed", "Patient consultation completed but
  billing incomplete", "Payment pending").

**"Doctor running late" is deliberately not implemented.** Every other
type here is a direct threshold on one row's own timestamp; "late"
needs comparing a doctor's actual pace against their schedule, which
this phase doesn't build a model for. Guessing at a shallow heuristic
(e.g. "first token running behind its slot time") risks exactly the
"hundreds of noisy alerts" section 46 warns against on a busy or
overbooked day. Left as a known gap rather than a weak approximation.

## What changed

**`app/services/exception_engine.py`** — `get_active_exceptions_
service(cur, *, hospital_id)`. Six private query functions, one per
type, each a single SQL statement scoped by `hospital_id` (through
`appointments.hospital_id` or `encounters.hospital_id`) with a
per-type minute threshold as a module constant. Results are merged and
sorted oldest-first (`age_minutes` descending) so the most overdue item
leads. `PAYMENT_PENDING` replicates `billing_services.py`'s
`_compute_totals` balance formula (gross − discount, + tax, − paid) in
SQL across every open invoice at once rather than reusing the
per-invoice Python helper, which would mean one round trip per invoice.

**`GET /api/exceptions`** (`app/api/exceptions.py`) — bare
`get_current_staff`, same tier as `GET /dashboard/stats`: viewing
operational exceptions is no more sensitive than viewing the
appointments/queue data they're computed from.

**Dashboard widget** (`frontend/src/admin/DashboardPanel.tsx`) — a
full-width "Needs Attention" card between the top stat row and
"Appointment Status", fetched alongside the existing stats/trends
calls. Each row shows the exception's message, its recommended action,
an age badge, who should act, and a "View Patient" link. An empty list
renders an explicit "Nothing overdue right now" state rather than
silently showing nothing (empty-state convention, master spec section
49) — the widget itself always renders once loaded, whether or not
there's anything in it, so the card's presence isn't itself a signal
of trouble.

Thresholds are per-type module constants, not a per-hospital setting.
This repo is still single-tenant in practice (`hospital_id`'s own
"purely structural" migration, 0027) — building configuration UI for a
second hospital's different thresholds is speculative until a second
hospital exists.

## Files changed

- `app/services/exception_engine.py` — new.
- `app/api/exceptions.py` — new: `GET /exceptions`.
- `app/main.py` — registers the new router.
- `tests/test_exception_engine.py` — new, 10 tests.
- `frontend/src/types.ts` — `ExceptionType`, `OperationalException`,
  `ExceptionsResponse`.
- `frontend/src/api.ts` — `getActiveExceptions`.
- `frontend/src/admin/DashboardPanel.tsx` — the "Needs Attention"
  widget.
- `frontend/src/styles.css` — `.exceptions-card`/`.exceptions-list`/
  `.exception-row`/`.exceptions-empty`.

## Database changes

None. Every exception type is computed from existing tables (Phases
3/5-9) — see "Scope" above.

## API changes

New: `GET /api/exceptions` (bare authenticated staff, any role).
Returns `{"exceptions": [...], "count": N}`, sorted most-overdue-first.

## Tests

10 new (`tests/test_exception_engine.py`): unauthenticated request
rejected; empty when nothing is overdue; each of the six types fires
once its threshold is crossed (verified by backdating the one relevant
timestamp via direct SQL, the same pattern `tests/test_queue_tokens.py`
already uses for time-dependent assertions) and stops appearing once
the underlying row changes (vitals recorded, consultation started, a
result recorded, a charge raised, a payment made); a STAT order is
flagged at 31 minutes while a ROUTINE order at the same age is not
(confirms the priority-scaled threshold); results sort oldest-first
across types.

Full suite: 557 passed (547 + 10 new), 1 skipped, 2 pre-existing
failures — `test_date_first_lists_multiple_doctors_with_their_own_slot_
counts` and `test_queue_visited_at_is_doctor_local_time_not_utc`, both
real-wall-clock-time-dependent, the same class of flake flagged in
every phase report since Phase 7 (root-caused again: today's date
crossing a different boundary than in earlier runs); neither touches
code this phase's diff changes.

**Branch note**: PR #110 (Phases 5-9) merged since Phase 10's own
commit, and `main` advanced further with an unrelated PR (#111, a Vite
dev-server bind fix) before this phase started. Per this repo's
merged-branch working agreement, the Phase 10 commit was rebased onto
latest `main` first (the sandbox's git-destructive-action guard
blocked the resulting `--force-with-lease` push, so this was done as a
plain merge commit instead — no rewritten history, ordinary
fast-forward push) before any Phase 11 work began.

**Browser-verified end-to-end**: ran the app locally, created two
CHECKED_IN visits, backdated one appointment's `visited_at` and one
closed encounter's `closed_at` directly via SQL to cross their
thresholds, and confirmed via `curl` that `GET /api/exceptions`
returned both with correct `age_minutes`/`balance`/messages. Opened the
Dashboard in a browser and confirmed the "Needs Attention" card showed
both rows with the correct icon tone, age badge, and message; reset
both timestamps to "now" and confirmed the card switched to the
"Nothing overdue right now" empty state on reload. `npx tsc -b
--force`, `npm run lint`, and `npm run build` all pass clean (only
pre-existing warnings elsewhere in the app, none newly introduced).

## Known gaps / deliberately out of scope

- **No "Doctor running late" type.** See "Scope" above — needs a real
  schedule-adherence model this phase doesn't build, not just a missed
  case.
- **No per-hospital threshold configuration.** Constants in
  `exception_engine.py`, not a settings table — see "What changed".
- **No deep link from an exception to the specific patient's record.**
  "View Patient" navigates to the Patients list (this dashboard's
  existing shallow-navigation convention — "Add Patient" does the
  same), not directly into that patient's Phase 10 timeline. A future
  pass could wire the two together.
- **No push/SMS notification.** Screen-only, surfaced on the Dashboard
  a staff member is already expected to have open — matching this
  codebase's existing stance (Phase 8's known gaps: "no notification/
  exception-engine push").

## Next phase

No further phase was specified beyond this point in the master spec
sequencing this session has followed (Phases 3, 5-11). Remaining
flagged gaps from `docs/OPD_HIMS_P0_AUDIT.md` section 5: packages and
full insurance/TPA claim modeling (sections 39-40) — explicitly
deferred by the master spec itself ("Do not build a fake insurance
system now. Create the correct extension point"; "This can be
implemented in a later billing phase if not already available").
