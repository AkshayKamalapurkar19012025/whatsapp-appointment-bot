# OPD/HIMS Master Spec — Phase 12: Packages + Insurance/TPA Extension Point

Follows `docs/OPD_HIMS_P11_EXCEPTION_ENGINE.md`.

## Scope

The last two gaps `docs/OPD_HIMS_P0_AUDIT.md` section 5 flagged and
every phase since has deferred: packages and insurance/TPA extension
points (master spec sections 39-40). `migrations/0033_billing_
invoices.sql`'s own header named this exact moment: "Packages... and
full insurance/TPA claim modeling... explicitly NOT built here -- the
master spec itself defers packages to 'a later billing phase if not
already available'". This is that phase, for both gaps, read strictly
from what the spec text itself asks for now versus later:

**Section 39 (Packages) — build the real thing.** A package is a
hospital's own priced catalog entry ("Health Checkup Basic", "Antenatal
Package") billed as one line item. New `packages` table (name,
description, price, active — a hospital-owned catalog, same shape as
`appointment_types`/`departments`), a `PACKAGE` charge source type, and
`charges.source_package_id`. "Package pricing should not duplicate
individual services incorrectly" is satisfied structurally, not by
reconciliation logic: a package charge has no link to any order/
dispense, so there is nothing to double-charge against.

**Section 40 (Insurance/TPA) — build only the classification, not the
claims fields.** Read closely, section 40 has two parts: "The initial
OPD version should at least be architecturally ready for: Cash /
Self-pay / Corporate / Insurance / TPA / Government scheme" — a plain
ask, buildable now — followed immediately by a second list (Payer,
Policy, Membership, Authorization, Pre-auth, Co-pay, Patient
responsibility, Claim) under its own heading, **"Future fields"**, then
"Do not build a fake insurance system now. Create the correct extension
point." `migrations/0033`'s own header had already ruled on this same
question for the claim-tracking fields specifically: "not simulated
with empty columns here." This phase doesn't revisit that call — it
adds exactly the one field that clears that bar: `invoices.bill_type`,
a real six-way classification a front desk sets once per bill, editable
through the same `PATCH .../bill` endpoint discount/tax already use.
No `payer_name`/`policy_number`/`claim_*` columns are added — they
would be exactly the "empty columns" both that migration and this
phase's own reading of section 40 reject.

## What changed

**`packages` catalog + billing integration**
(`migrations/0038_packages.sql`). `app/api/packages.py`: `GET`
(bare-staff, active-only) / `GET /admin` (all, `package.manage`) /
`POST` / `PUT /{id}` / `PATCH /{id}/active` — same CRUD shape as
`appointment_types.py`. `add_charge_service` (`app/services/
billing_services.py`) gained `source_package_id`, validated against the
encounter's own `hospital_id` and the package's `active` flag (an
inactive package can't be billed going forward, but past charges
referencing it are untouched). `charges`'s "at most one source" CHECK
(previously `source_order_id`/`source_dispense_id`) is extended to all
three — a package charge, an order charge, and a dispense charge stay
mutually exclusive — but, unlike those two, `source_package_id` gets no
uniqueness index: a package is a reusable catalog entry, not a single
clinical event, so the same package can legitimately be billed more
than once.

**`invoices.bill_type`** (`migrations/0039_invoice_bill_type.sql`),
default `CASH`. `update_invoice_terms_service` accepts it alongside
`discount_amount`/`tax_rate` with the same `COALESCE`-if-omitted
semantics. `payments.method` already had `INSURANCE`
(migrations/0033) — that's how a specific payment gets settled;
`bill_type` is a different, invoice-level fact (who this bill is being
raised against) set independently, since a TPA-billed patient can still
make a cash co-payment.

**Frontend**: `PackagesPanel.tsx` (new "Packages" nav entry, `Manage`
group) + `PackageFormModal.tsx` — table-plus-modal CRUD, `isAdmin`-gated
the same way `DepartmentsPanel`/`PharmacyPanel` already are (a plain
STAFF session gets the read-only active-only list, never the
`/admin` listing that would 403). `AppointmentBillingPanel.tsx`
(`ConsultationWorkspace`'s Billing tab) gained a "Bill a package"
picker next to the existing manual-charge form and "Bill this"
unbilled-order/dispense actions, and a "Bill type" selector inside the
existing discount/tax terms form; the bill summary header now shows a
pill for the current bill type next to the payment-status pill.

## Files changed

- `migrations/0038_packages.sql`, `migrations/0039_invoice_bill_type.sql` — new.
- `app/api/packages.py` — new.
- `app/api/billing.py` — `ChargeCreate.source_package_id`/`PACKAGE` source type, `InvoiceTermsUpdate.bill_type`, `PackageNotFound` → 404.
- `app/services/billing_services.py` — `source_package_id` validation/insert, `bill_type` in `_INVOICE_COLUMNS`/`update_invoice_terms_service`.
- `app/services/exceptions.py` — `PackageNotFound`, `DuplicatePackageName`.
- `app/services/patient_timeline_service.py` — `bill_type` added to the timeline's invoice projection.
- `app/main.py` — registers the packages router.
- `tests/test_packages.py` — new, 12 tests.
- `tests/conftest.py` — `packages` added to the per-test truncation list.
- `frontend/src/admin/PackagesPanel.tsx`, `PackageFormModal.tsx` — new.
- `frontend/src/admin/AdminApp.tsx` — "Packages" nav entry + route.
- `frontend/src/admin/AppointmentBillingPanel.tsx` — package picker + bill type selector/pill.
- `frontend/src/types.ts`, `frontend/src/api.ts` — `Package`/`PackageInput`/`BillType`, package CRUD calls, `source_package_id`/`bill_type` on the existing billing types.

## Database changes

`packages` (new table, real `hospital_id` — a hospital-owned catalog
with no parent to derive tenancy from, same as `appointment_types`).
`charges.source_package_id` (nullable FK, no uniqueness index — see
"What changed"). `invoices.bill_type` (`NOT NULL DEFAULT 'CASH'`).

## API changes

New: `GET/POST /api/packages`, `GET /api/packages/admin`, `PUT
/api/packages/{id}`, `PATCH /api/packages/{id}/active`. Extended:
`POST /appointments/{id}/bill/charges` (`source_package_id`,
`source_type: "PACKAGE"`), `PATCH /appointments/{id}/bill`
(`bill_type`).

## Tests

12 new (`tests/test_packages.py`): catalog CRUD (create/list, duplicate
name rejected, update, deactivate/reactivate — including that an
inactive package drops out of the plain listing but stays visible via
`/admin`); `package.manage`-gated for non-admin STAFF; billing a
package as a charge (gross/charge fields correct); the same package
billed twice on one invoice succeeds (no uniqueness conflict, unlike
order/dispense charges); billing a nonexistent or inactive package is
rejected with 404; `bill_type` defaults to `CASH`; updating it persists
and survives an unrelated terms update that omits it; an invalid
`bill_type` value is rejected with 422.

Full suite: 569 passed (557 + 12 new), 1 skipped, 2 pre-existing
failures — `test_date_first_lists_multiple_doctors_with_their_own_
slot_counts` and `test_queue_visited_at_is_doctor_local_time_not_utc`,
both real-wall-clock-time-dependent, the same class of flake flagged in
every phase report since Phase 7; neither touches code this phase's
diff changes.

**Branch note**: this phase continued directly from Phase 11's
already-restarted branch (no intervening merge to reconcile).

**Browser-verified end-to-end**: ran the app locally, created a package
via the new Packages page (screenshot-confirmed list/table rendering),
then walked a fresh walk-in visit through booking → check-in → queue →
Consultation Workspace → Billing tab, selected the package from "Bill a
package" and confirmed the resulting charge (description, `PACKAGE`
type, ₹1500.00) and updated gross/net/balance appeared correctly in the
live bill; opened "Edit discount / tax", changed Bill type from Cash to
TPA, saved, and confirmed the bill summary's pill updated from `CASH`
to `TPA` on the live page. `npx tsc -b --force`, `npm run lint`, and
`npm run build` all pass clean (only pre-existing warnings elsewhere in
the app, none newly introduced).

## Known gaps / deliberately out of scope

- **No payer/policy/claim fields.** See "Scope" above — the master
  spec's own text labels these "Future fields", not this phase's to
  build; the same call `migrations/0033` already made for claim
  tracking specifically.
- **No claims workflow.** Submission, adjudication, or any status
  beyond `bill_type` itself — "Create the correct extension point," not
  a real insurance system, is exactly what section 40 asks for here.
- **No package component tracking.** A package charge doesn't reference
  which orders/services it's meant to cover — deliberately structural
  only (see "What changed"), not a bundle-fulfillment feature.
- **No per-hospital package pricing tiers.** One price per package,
  same single-tenant-in-practice stance as every other catalog table
  since `migrations/0027_hospital_tenant_context.sql`.

## Next phase

No further phase was specified beyond this point in the master spec
sequencing this session has followed (Phases 3, 5-12). Every gap
`docs/OPD_HIMS_P0_AUDIT.md` section 5 originally flagged is now either
built or explicitly, deliberately deferred by the master spec's own
text (packages/insurance claims fields, "Doctor running late" per
`docs/OPD_HIMS_P11_EXCEPTION_ENGINE.md`). Remaining unimplemented
sections span the master spec's later program phases (IPD/beds/OT,
insurance claims processing itself, multilingual documents, module
licensing) — each its own scoped effort, not a natural next increment
on top of what exists today.
