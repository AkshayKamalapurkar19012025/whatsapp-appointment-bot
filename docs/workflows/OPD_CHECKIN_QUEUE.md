# Workflow: OPD Check-in & Queue

## 1. Purpose

Move a confirmed appointment/walk-in from "booked" to "in the doctor's live queue," with a token, so triage/consultation can begin in the right order.

## 2. Actors

Receptionist, Billing/Cashier (payment collection at check-in), Doctor/Nurse (queue-facing view), Admin, Staff.

## 3. Entry points

Appointments list row action ("Collect Payment"/"Waive Charge" on a `CHECKED_IN` appointment), or immediately after walk-in booking.

## 4. Preconditions

Appointment exists with status allowing check-in (`CONFIRMED` or freshly-created walk-in). Payment must be collected or waived before a token is issued — see State transitions.

## 5. Workflow

```
Appointment (CONFIRMED) or Walk-in created
      ↓
Check-in (arrival recorded)
      ↓
Payment due?
 ┌────┴────┐
YES        NO/ALREADY PAID
 │          │
Collect     │
Payment or  │
Waive       │
 └────┬─────┘
      ↓
Token issued → Queue (WAITING)
      ↓
Recall / Priority / Hold (operator actions) or naturally becomes NOW SERVING
      ↓
"View consultation" / "Mark completed"
```

## 6. UI pages

`AppointmentsPanel.tsx` (check-in/payment actions), `AppointmentDetailsModal.tsx`, `QueuePanel.tsx` / `QueueSection.tsx` / `DepartmentQueuePanel.tsx` (live queue), `VisitCompletionDialog.tsx` (Mark Completed).

## 7. Actions

Check-in, Collect Payment, Waive Charge (ADMIN, requires a completed visit with the same doctor in the last 3 days per a live-verified business rule), Recall, Set Priority (with reason), Hold, Open Consultation, Mark Completed.

## 8. State transitions

`appointments.status`: `PENDING → CONFIRMED → CHECKED_IN → COMPLETED` (or `CANCELLED`/`REJECTED`/`NO_SHOW` at various points). **Note**: this is a materially simpler model than a hypothetical `SCHEDULED→ARRIVED→CHECKED_IN→WAITING→CALLED→IN_CONSULTATION→CONSULTATION_COMPLETED→COMPLETED` — "waiting" vs. "in consultation" within `CHECKED_IN` is *derived* (lowest un-served token = now-serving), not a stored status. This is a deliberate simplification already documented in `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §23-25 — do not "fix" it by adding new status values without a concrete reason, since the queue view already computes the right answer from token order.

A token is only issued once `payment_status` leaves `UNPAID`/`FAILED` (paid or waived) — confirmed live: the "Mark completed"/queue actions are unavailable on an appointment still `AWAITING PAYMENT`.

## 9. Domain objects

`appointments` (status, payment_status, token fields), `encounters` (opened alongside/via the appointment).

## 10. API requirements

Check-in/payment endpoints under `/api/appointments/{id}/...` (collect payment, waive charge), queue read under a doctor-scoped queue endpoint, `POST /appointments/{id}/complete`.

## 11. Validation

Waiver requires a reason and (per live-verified behavior) a completed visit with the same doctor within the last 3 days — a genuine business rule, not a bug, encountered directly during this session's own live testing.

## 12. Error handling

A waiver attempt that fails the 3-day rule returns a clear inline message ("Waiver requires a completed visit with this doctor in the last 3 days") rather than a generic failure.

## 13. Permissions

Check-in/payment: `RECEPTIONIST`, `BILLING`, `ADMIN`, `STAFF`. Waive Charge: `ADMIN` only (confirmed live — the row action only appears for an ADMIN session). Recall/Priority/Hold: front-desk roles. Mark Completed: `RECEPTIONIST`, `DOCTOR`, `ADMIN`, `STAFF`.

## 14. Audit requirements

Payment collection/waiver, priority overrides, and completion are exactly the class of action `audit_logs` should capture — see `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §55 for current (partial) coverage.

## 15. Concurrency considerations

Token issuance and queue-position computation must not race two simultaneous check-ins into the same token number — this sits on the same booking/appointment infrastructure whose concurrency guarantees (`pg_advisory_xact_lock` + `EXCLUDE USING gist`) are the single most protected invariant in this codebase (see `docs/DATABASE_P1_NOTES.md` for the full history of this guarantee, including a previously-reproduced and now-mitigated cross-path race). **Never modify booking/check-in concurrency logic without reading that history first.**

## 16. Idempotency requirements

A double-click on "Mark completed" or "Collect Payment" must not double-charge or double-complete — `PaymentExceedsBalance` and similar guards exist for the payment side; the frontend also disables the button while a request is in flight.

## 17. Tests

`tests/test_appointment_lifecycle.py`, `test_patient_arrival_scenarios.py`, `test_queue_tokens.py` (referenced in this session's own work), `test_concurrency.py`, `test_concurrency_hardening.py`, `test_exclusion_constraint.py`.

## 18. Exit conditions

Patient is in the queue with a token, or (if the visit is being completed) the appointment reaches `COMPLETED` and `docs/workflows/PATIENT_360.md` reflects the closed visit.

## 19. Next workflow

`docs/workflows/CONSULTATION.md`.
