# Testing Strategy

## Ground rule

**Preserve all existing tests. Never delete a test simply to make the suite pass.** If a test fails after a change, the change is wrong, the test is stale, or the test needs updating to match an intentional, documented behavior change — in that order of likelihood. 683 test functions exist as of this writing (`tests/*.py`); that number should only grow.

## Current State by category

### Unit tests (domain logic)

✅ Present throughout `app/services/*.py`'s corresponding test files — duplicate detection, availability engine, timezone handling, module licensing logic, etc.

### API tests (contracts, validation, authorization, errors)

✅ Present — `test_admin_rbac.py`, `test_p10_security.py`, `test_module_licensing.py`, `test_app_config.py`, and per-resource test files each cover their router's contract.

### Integration tests (Patient → Encounter → Orders → Results → Billing)

✅ Present in pieces — `test_encounters.py`, `test_orders.py`, `test_order_results.py`, `test_billing_invoices.py` each cover a link in the chain. 🟡 Gap: no single test file exercises the *entire* chain in one test the way `docs/implementation/ACCEPTANCE_CRITERIA.md`'s target scripted run would — each test file validates its own link assuming the previous ones work, which is good unit/integration hygiene but isn't the same guarantee as one continuous journey test.

### Workflow tests (complete hospital journeys)

🟡 Partial — `test_patient_arrival_scenarios.py` and similar files cover multi-step scenarios within one workflow area; no test spans the full registration-to-Patient-360 journey in one function. See `docs/implementation/ACCEPTANCE_CRITERIA.md`.

### Concurrency tests

✅ Strong — `test_concurrency.py`, `test_concurrency_hardening.py`, `test_exclusion_constraint.py`, `test_patient_creation_race.py` cover token generation, queue state, appointment booking, slot availability, payment, duplicate actions. This is the single most rigorously tested category in the codebase, for good reason (a previously-reproduced, now-mitigated double-booking race is part of this project's own history — see `docs/DATABASE_P1_NOTES.md`).

### Permission tests (role/module combinations)

🟡 Real but not exhaustive. `test_admin_rbac.py`, `test_hospital_tenant_coverage.py`, `test_module_licensing.py` exist. Per `docs/OPD_HIMS_MASTER_SPEC_AUDIT.md` §78-79 (written before the current RBAC differentiation shipped): tests were not organized around a full role × configuration matrix because the roles didn't functionally exist yet at that audit's time — they do now (per `docs/product/HIMS_WORKFLOW.md`'s role table), so this gap should be **re-verified, not assumed still open**: `TODO — VERIFY AGAINST EXISTING IMPLEMENTATION` whether `test_admin_rbac.py`/`test_module_licensing.py` were extended alongside the role-differentiation work to cover the new roles, or whether that work shipped with only the capability gating itself tested (not a full role × permission matrix).

### Configuration tests

❌ Gap. No test suite currently runs the core patient journey under each of: OPD only / OPD + Laboratory / OPD + Radiology / OPD + IPD + Radiology / Full configuration. `test_module_licensing.py` tests the licensing/enablement mechanism itself (toggling, degradation values returned) but not "does the full patient journey still work correctly when a module is off" as an end-to-end concern. See Recommended Implementation.

## Target State

All of the above at ✅, plus the scripted full-journey acceptance run from `docs/implementation/ACCEPTANCE_CRITERIA.md`, plus the configuration matrix below.

## Gap

Two concrete, named gaps: (1) no single continuous integration test (or Playwright script) spans the entire patient journey; (2) no configuration-matrix test suite exists.

## Recommended Implementation

**For the configuration matrix** (minimum, per the acceptance criteria doc):

```
OPD only
OPD + Laboratory
OPD + Laboratory + Pharmacy
Full configuration
```

Implement as a pytest fixture that seeds `hospital_modules` to the target configuration before a shared journey-test function runs, parameterized (`@pytest.mark.parametrize`) over the four configurations — reusing the same journey logic four times rather than writing four separate near-duplicate tests. The core patient journey must remain valid under every applicable configuration; "valid" for a disabled module means the degradation behavior is correct (e.g. Orders routes to `EXTERNAL_REFERRAL` when `LAB_RADIOLOGY` is off), not that the journey is skipped.

**For the full-journey test**: either a pytest integration test that chains the existing per-workflow service calls in one function (fast, no browser needed, good for CI), or the Playwright acceptance script from `docs/implementation/ACCEPTANCE_CRITERIA.md` (slower, but the only way to catch a real frontend-wiring gap the backend-only test can't see) — build both if resourced; the pytest version first, since it's cheaper and can run on every CI push, with the Playwright version as the periodic/pre-release deeper check.

## Test infrastructure (unchanged, reusable as-is)

`tests/conftest.py` forces a `*_test` DB suffix, runs `scripts/migrate.py` once per session, truncates `APP_TABLES` between tests, uses a session-scoped `TestClient`. **Any new reference-style table (like `hospital_modules`) that should persist across tests like seed data must avoid FKs into an `APP_TABLES` member (especially `staff`)** — this exact mistake caused a silent cascade-truncation bug during the Module Licensing phase, caught and fixed before shipping. Read `tests/conftest.py`'s `APP_TABLES` list before adding any new table that isn't meant to be per-test-disposable.
