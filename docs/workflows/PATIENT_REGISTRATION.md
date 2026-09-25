# Workflow: Patient Registration

## 1. Purpose

Get a patient into the system with a permanent, de-duplicated identity (UHID) before any visit is created, per `docs/decisions/ADR-001-PATIENT-IDENTITY.md`.

## 2. Actors

Receptionist, Admin, Staff (front-desk registration). Patient (self-registration via WhatsApp OTP flow — a separate path, see Current State).

## 3. Entry points

- "+ Register new patient" from Patient Search (`PatientsPanel.tsx`).
- Walk-in step of "+ New OPD Visit" (`BookAppointmentPanel.tsx`) when search finds no existing patient.
- WhatsApp conversational flow (`app/api/booking.py`), which creates a patient identified by `whatsapp_number` on first contact — a separate, older code path from the admin-side `PatientFormModal.tsx`.

## 4. Preconditions

None required to search. Creating a patient requires at minimum name + mobile (see Validation).

## 5. Workflow

```
New OPD Visit / Patient Search
      ↓
Find Existing Patient (search by name/UHID/mobile)
      ↓
Existing?
 ┌────┴────┐
YES        NO
 │          │
Select      Register (PatientFormModal)
Patient     │
 │          ▼
 │      Duplicate check (possible_duplicates in response)
 │          │
 └────┬─────┘
      ↓
Patient Confirmed
      ↓
(if from New OPD Visit) Visit Details → Review → Payment if required → OPD Visit Created → Queue/Token
```

## 6. UI pages

`PatientsPanel.tsx` (search/list), `PatientFormModal.tsx` (register/edit), `BookAppointmentPanel.tsx`'s walk-in patient-search step.

## 7. Actions

Search, Register, Edit, Merge (`POST /patients/{id}/merge`), Unmerge (`POST /patients/merges/{merge_id}/unmerge`), review a flagged duplicate (`PATCH /patients/duplicate-reviews/{review_id}`).

## 8. State transitions

A patient record has no explicit lifecycle status (no soft-delete state — see `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §70). The closest thing to a state machine is the duplicate-review flow: a flagged possible-duplicate pair is `PENDING` until reviewed, then resolved as merged or dismissed.

## 9. Domain objects

`patients` (uhid, name, mobile, DOB, gender — minimal field set by design; no email/alternate-mobile/address/emergency-contact columns today), `patient_allergies`, `patient_duplicate_reviews`, `patient_merges`.

## 10. API requirements

`GET /api/patients/search`, `GET /api/patients/admin` (paginated, `limit`/`offset`), `POST /api/patients` (create), `PATCH /api/patients/{id}`, `GET /api/patients/by-uhid/{uhid}`, `GET /api/patients/{id}/timeline`, `POST /api/patients/{id}/merge`, `POST /api/patients/merges/{merge_id}/unmerge`, `PATCH /api/patients/duplicate-reviews/{review_id}`, `GET/POST /api/patients/{id}/allergies`, `POST /api/patients/{id}/allergies/{allergy_id}/resolve`.

## 11. Validation

Name + mobile required; DOB/gender optional (deliberate minimal-registration design, not an oversight — see `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §16-18). Mobile format normalization exists (`test_phone_normalization_api.py`). `patient_duplicate_detection.py` runs on creation and returns `possible_duplicates` rather than blocking creation outright — the operator decides whether to proceed or select the existing record.

## 12. Error handling

Duplicate mobile/name doesn't hard-block; it surfaces `possible_duplicates` for the operator to review. Standard `{success, errorCode, message, details}` envelope on any failure (`app/error_handling.py`).

## 13. Permissions

`RECEPTIONIST`, `ADMIN`, `STAFF` can register/edit. Merge/unmerge — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` for the exact permission name gating these (likely ADMIN-only given the risk of merging distinct real patients incorrectly; confirm in `app/api/patients.py` before assuming).

## 14. Audit requirements

Merges/unmerges are exactly the kind of action that should be in `audit_logs` — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` whether `patient_merges` itself (which records who/when) is being treated as sufficient audit trail or whether a generic `audit_logs` row is also written; `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §55 found audit coverage real but not exhaustive against every named example action.

## 15. Concurrency considerations

`test_patient_creation_race.py` exists — confirms a concurrent-registration race has been specifically tested. Duplicate detection reduces but does not eliminate the chance of two near-simultaneous registrations creating two patient records for the same person; merge exists as the recovery path, by design, rather than a hard uniqueness constraint on name+mobile (which would incorrectly block family members sharing a household number).

## 16. Idempotency requirements

No formal `Idempotency-Key` header mechanism (consistent with the rest of the API — see `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §56-58). A double-submitted registration form is not structurally prevented at the API level beyond the frontend disabling the submit button while a request is in flight — `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` if stronger guarantees are needed for a future phase.

## 17. Tests

`tests/test_patients.py`, `test_patients_pagination.py`, `test_patient_duplicate_detection.py`, `test_patient_merge.py`, `test_patient_creation_race.py`, `test_patient_identifiers_parity.py`, `test_patient_uhid_and_search.py`, `test_patient_allergies.py`, `test_phone_normalization_api.py`.

## 18. Exit conditions

A patient has a `uhid`, is findable by search, and (if this registration happened as part of visit creation) flows directly into `docs/workflows/OPD_CHECKIN_QUEUE.md` without re-entry.

## 19. Next workflow

`docs/workflows/OPD_CHECKIN_QUEUE.md`.
