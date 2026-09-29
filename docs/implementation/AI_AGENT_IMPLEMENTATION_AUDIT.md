# AI Agent Automation Layer — Implementation Audit

Status: **audit only. No production code changed.** Phase 1 awaits approval.
Every claim below was read from the repo at `main` @ `bf36014`; items not verified are marked `TODO — VERIFY`.

## Headline findings (these change the prompt's assumptions)

1. **`encounter.create` should not be a tool.** An OPD encounter is created inside `create_appointment_service` (`app/services/appointment_services.py:379-400`), one per appointment, in the same transaction; `encounters.encounter_type` is `CHECK IN ('OPD')`. There is no standalone "create encounter" domain operation, and inventing one would create a second encounter path (violates CLAUDE.md). Replace with a read-only `encounter.get`.
2. **`appointment.check_in` does not generate a queue token.** `mark_visited_service` (`:1128`) only sets `CHECKED_IN` + `visited_at`, and requires status `CONFIRMED` (a `PENDING` walk-in needs `confirm_and_check_in_service`, `:1276`). Tokens are issued only by `generate_queue_token_service` (`:1033`), called solely from payment-PAID and fee-waiver (`record_payment_service`, `waive_consultation_fee_service`). So the prompt's `check_in → queue token exists` verification is wrong for this system. Correct post-check: `status == CHECKED_IN AND visited_at IS NOT NULL`; `token_number IS NULL` is the *expected* state until payment/waiver.
3. **`queue.generate_token` is not a safe standalone write.** It is coupled to payment/waiver as a business rule ("patient must not enter the queue until paid or waived"). Exposing it directly would let the agent bypass that gate. Do not build it; token issuance is reachable only through financial tools (later phase, high risk).
4. **Permission enforcement is weak on exactly the endpoints the slice needs.** `POST /appointments/{id}/visit`, `GET /patients/search`, `GET /patients/{id}`, `GET /appointments/{id}/invoice`, `GET /doctors/{id}/queue`, `GET /appointments/{id}/encounter` are gated only by `get_current_staff` (any logged-in staff), not `require_permission`. `POST /visit` also writes **no `audit_log` row** and several service functions take `appointment_id` with **no `hospital_id` filter** (e.g. `get_invoice_service`, `mark_visited_service`). The agent layer must therefore enforce role/permission and tenant checks itself in the tool wrapper — it cannot rely on the services to do it. This is a pre-existing gap, not something to fix inside the AI layer.
5. **No LLM code or dependency exists** (no `anthropic`/`openai` in `requirements.txt` or `app/`). "WhatsApp" appears only as `patients.whatsapp_number`, notifications and booking sessions. Greenfield for model calls; brownfield for everything they touch.
6. **No general idempotency-key mechanism exists.** Idempotency is per-operation and state-based (`generate_queue_token_service` returns the existing token; `mark_arrived_service` returns existing `arrived_at`; `patient_identifiers` uniqueness). `mark_visited_service` is *not* replay-safe: a second call raises `InvalidStatusTransition` (409) because status is already `CHECKED_IN`.

## A. Current architecture

- FastAPI + psycopg3 raw SQL (no ORM), `psycopg_pool`; PostgreSQL 16. React/Vite admin in `frontend/`. `ipd-service/schema` is schema only.
- Layering: `app/api/*.py` (routers, Pydantic bodies, HTTP mapping, notifications) → `app/services/*.py` (domain logic, take an open `cur`, raise `app/services/exceptions.py` types) → SQL. Transaction = `with get_connection()` (commit on clean exit, rollback on exception; `app/db/connection.py`).
- Migrations: plain SQL `migrations/00NN_*.sql` (57 files; two share `0054`), run by `scripts/migrate.py`, tracked in `schema_migrations`. The app never migrates on startup.
- Errors wrapped in `{success, errorCode, message, details}` envelope (`app/error_handling.py`).
- Tests: pytest, real Postgres `*_test` DB, TRUNCATE-based fixtures (`tests/conftest.py` `APP_TABLES` list — **new tables must be added there**). ~78 test files. `psql` is present in this container; a Postgres server was not found (`TODO — VERIFY` whether tests can run here).

## B. Existing capabilities that can become tools

| Proposed tool | Existing basis | Notes |
|---|---|---|
| `patient.search` | `GET /patients/search` (`app/api/patients.py:335`), `global_search_service` | Inline SQL in router for `/search`; service exists only for global search. Extract-or-reuse decision needed (see K). |
| `patient.get` | `GET /patients/{id}` (`:462`) | Router-level SQL. |
| `appointment.search` | `GET /appointments` (`app/api/appointments.py:162`, filters by date/doctor etc.) | Large inline handler; needs a service-level read. |
| `appointment.get` | none dedicated | Must be composed. |
| `doctor.get`, `department.get` | `GET /doctors/{id}`, `GET /departments` | Router-level. |
| `doctor_schedule.get` | `GET /doctors/{id}/schedule`, `availability_engine.py` | |
| `queue.get` | `GET /doctors/{id}/queue`, `queue_display` | |
| `encounter.get` | `get_encounter_summary_service` (`clinical_services.py:72`) | Real service; best-shaped existing read. |
| `invoice.get` | `get_invoice_service` (`appointment_services.py:1436`) | Real service; no tenant filter. |
| `appointment.check_in` | `mark_visited_service` / `confirm_and_check_in_service` | Reuse as-is. Not replay-safe. |

## C. Reusable existing components

Domain services above; `send_mock_notification` / `create_notification` (the `/visit` handler sends both — a tool must reproduce or call the same side effects, see K); `record_audit_log`; `get_staff_by_session_token`; `is_module_available`; `resolve_patient_by_identifier`; `patient_duplicate_detection`; `app/services/exceptions.py` for typed failures (map to tool errors).

## D. Existing authorization

- Staff auth: username/password (argon2id), opaque SHA-256-hashed bearer tokens in `staff_sessions`, 24h absolute + 30min idle expiry, lockout after 5 failures. `get_staff_by_session_token` returns `{id, username, role, hospital_id}`.
- RBAC: `require_permission(name)` (`app/api/staff_auth.py:122`) checks `staff_roles → role_permissions → permissions` or a live `break_glass_grants` row. Roles: ADMIN, STAFF, DOCTOR, NURSE, RECEPTIONIST, LAB_TECH, PHARMACIST, BILLING. `staff_roles.department_id` supports department scope (NULL = hospital-wide) but `require_permission` **ignores it**.
- **`require_permission` is a FastAPI dependency, not a callable.** The agent layer can't invoke it; the permission query must be lifted into a shared function (small, behavior-preserving refactor of `staff_auth.py`) or duplicated (rejected — second authz path).
- Tenant: `hospital_id` on staff/patients/appointments/encounters; enforcement is inconsistent (finding 4).
- Patient scope: no per-staff patient-assignment model exists. `patient_scope` from the prompt has nothing to bind to today; Phase 1 defines it as `{hospital_id}` plus an explicit allow-list of patient ids resolved in the task (see K).
- Patient sessions are a separate subsystem and must never authenticate agent tasks.

## E. Existing audit mechanisms

`audit_log` (`migrations/0033`): append-only `(hospital_id, staff_id, action, resource_type, resource_id, details JSONB)`, written via `record_audit_log(cur, …)` in the caller's transaction; read at `GET /audit-log` (`staff.manage`). It is a one-row-per-mutation log — **insufficient** for the prompt's reconstruction requirement (plan, steps, raw traces, verifications, approvals), and not all writes use it. It is kept as the system-of-record audit for hospital mutations; agent-initiated writes should additionally emit an `audit_log` row (action prefixed `agent.`) inside the same transaction as the tool's write. The new `agent_*` tables hold the agent-specific trace.

## F. Existing appointment / check-in / queue / encounter flow

`create_appointment_service` → encounter + appointment (`PENDING`/`CONFIRMED`) → `confirm` (PENDING→CONFIRMED; refused after `start_at`) → optional `arrive` (records `arrived_at`, stays CONFIRMED) → `visit` (CONFIRMED→CHECKED_IN, `visited_at`) or `confirm-and-checkin` (walk-ins) → payment PAID or fee waiver → `generate_queue_token_service` (advisory lock per doctor/day, max+1, idempotent) + token notification → consultation (`clinical_services`) → `complete` (→COMPLETED, closes encounter via `_close_encounter_for_appointment`); also `no-show`, `cancel`, `reschedule`. Status sets: `RELEASED = CANCELLED/REJECTED`, `ACTIONABLE = PENDING/CONFIRMED`, `TERMINAL = CANCELLED/REJECTED/COMPLETED/NO_SHOW`. Hold/recall/priority operate on queue entries.

## G. Proposed integration point

A new package `app/agent/` inside the existing FastAPI monolith (no new service, no microservice). Layout:

```
app/agent/
  models.py         # Pydantic: TaskSpec, AuthContext, Plan, Step, ExecutorResult, VerifierResult
  llm.py            # thin LLM client interface + FakeLLM for tests; real client behind config flag
  agents/{intake,planner,executor,verifier}.py   # prompt + strict output parsing (reject malformed JSON)
  improver.py       # offline; reads logs, writes proposal files only; not imported by app.main
  orchestrator.py   # state machine, retries, replan, escalation
  tools/registry.py # Tool dataclass + registry; the ONLY thing Planner/Executor can see
  tools/*.py        # wrappers -> existing app/services functions
  checks.py         # deterministic post-conditions per tool
  authz.py          # permission + hospital/patient-scope checks (calls shared permission fn)
  store.py          # agent_* persistence
app/api/agent.py    # POST /agent/tasks, GET /agent/tasks/{id}, POST /agent/tasks/{id}/approve|cancel
```

Rules: tool wrappers take `(cur, AuthContext, validated_args)` and call `app/services/*` only; nothing under `app/agent/` imports `psycopg` or issues SQL except `store.py` (own tables). A test will enforce this by AST-scanning `app/agent/` for SQL/`get_connection` outside `store.py` and the tool modules' injected `cur`.
Orchestrator runs **synchronously in the request** in Phase 1 (one short task); a background worker is deferred. Approval = a second HTTP call by a *different authenticated human* (see K).

## H. Proposed data model (migration `0060_agent_layer.sql`)

Names follow existing snake_case, `BIGSERIAL`, `hospital_id NOT NULL REFERENCES hospitals(id)`, append-only where possible. Consolidated from the prompt's nine tables to keep Phase 1 small:

- `agent_tasks` — id, hospital_id, initiated_by_staff_id, state, task_type, risk_tier, `auth_context JSONB`, `task_spec JSONB`, raw input, `idempotency_key UNIQUE(hospital_id, key)`, attempt/replan counters, final outcome, timestamps. (Merges `agent_task_inputs`; raw input is PHI-bearing — see K.)
- `agent_plans` — task_id, version, plan JSON, `change_from_previous`, status.
- `agent_steps` — plan_id, step_no, tool, args, irreversible, depends_on, state, attempt.
- `agent_tool_calls` — step_id, attempt, `tool_call_id`, args, **raw_response** (captured by the orchestrator, never by a model), error, `idempotency_key`, duration.
- `agent_approvals` — task_id, step_id, requested_at, approver_staff_id, decision, `token_hash` (SHA-256, same pattern as `staff_sessions`), expires_at, consumed_at. Constraint: approver ≠ initiator.
- `agent_verifications` — step_id/task_id, kind (`deterministic`|`llm`), verdict, criteria JSON, evidence.
- `agent_audit_events` — append-only ordered event log for reconstruction (state transitions, model calls with prompt version + token counts, escalations). Merges `agent_failures` (a failure is an event with a cause).
- Permissions (seeded like `0048`/`0053`): `agent.task.create`, `agent.task.read`, `agent.task.approve`, and per-tool permissions reuse existing names where one exists.
- `tests/conftest.py` `APP_TABLES` updated.

## I. Proposed first vertical slice

"Check in today's 10:30 appointment for Ravi." Phase 1 delivers it with a **scripted `FakeLLM`** (deterministic fixtures) plus the real-LLM client behind a flag, so every test is hermetic.

`Intake` → TaskSpec (`task_type=appointment_check_in`, required: patient reference, date, time; risk `medium`) → authz (`appointment.check_in` permission + hospital) → Planner: `patient.search` → `appointment.search(patient_id, date, time)` → *deterministic uniqueness check* (exactly 1 patient, exactly 1 appointment in a check-in-eligible status) → `appointment.check_in` → deterministic post-check (`status == CHECKED_IN`, `visited_at` set) → Verifier over acceptance criteria → `COMPLETED`.
- 0 or ≥2 patients/appointments → `NEEDS_INPUT` with the candidates listed; never guess.
- Appointment already `CHECKED_IN` → detected in pre-check; reported as "already done", not re-executed (avoids the 409 replay problem).
- Appointment `PENDING` → `BLOCKED` unless the plan uses `confirm_and_check_in` (which is a separate, explicitly approved tool; not in slice).
- `check_in` is medium risk (reversible only by staff correction; sends a patient notification → arguably **high** under the prompt's own "sends something to a person" rule; see K).

## J. Files

**Create:** `docs/implementation/AI_AGENT_IMPLEMENTATION_AUDIT.md` (this), `app/agent/**` (above), `app/api/agent.py`, `migrations/0060_agent_layer.sql`, `tests/agent/**` (per-agent, orchestrator, security suites), `docs/architecture/AI_AGENT_LAYER.md`, `docs/decisions/ADR-006-AI-AGENT-LAYER.md`.
**Modify (minimal):** `app/main.py` (register router), `app/api/staff_auth.py` (extract permission check into a reusable function; behavior-preserving, covered by existing `tests/test_rbac_permissions.py`), `tests/conftest.py` (`APP_TABLES`), `app/config.py` (LLM flag/model/key env), `requirements.txt` (LLM SDK, only when the real client lands), `docs/product/PRODUCT_VISION.md` and `docs/implementation/PHASES.md`.
**Do not modify:** `app/services/appointment_services.py`, `clinical_services.py`, `billing_services.py`, `patient_*`, `availability_engine.py`, existing migrations, `app/api/scheduling.py`, frontend, `ipd-service/`.
Optional, separately approved: add `require_permission` + `audit_log` to `POST /appointments/{id}/visit` (finding 4) — a behavior change to existing endpoints, out of the AI layer's scope.

## K. Risks and open questions (need your decision)

1. **PHI to a model provider.** Patient names/phones will reach the LLM via Intake/Planner/Verifier. Which provider/BAA/region applies? Proposal: pass patient ids and minimal fields; redact phone numbers; never send clinical notes to any agent in Phase 1. Raw task input stored in `agent_tasks` is PHI — retention/encryption policy?
2. **Approval semantics for `check_in`.** It triggers a patient notification (`send_mock_notification`, real SMS later). By your rule ("sends something to a person") that is HIGH → approval on every check-in, which defeats the automation. Options: (a) treat check-in as medium and have the *tool* suppress/keep the notification unchanged as today's manual flow does; (b) approval required. I recommend (a) for the first slice because a human staff member already performs this same action unapproved via `/visit` — but that is your call.
3. **Whose authority does the agent act under?** Proposal: always the initiating human's staff session/permissions (no service account), so the agent can never exceed the user. Confirm.
4. **Existing endpoints lack permission/tenant checks** (finding 4). The tool layer will add them; OK to leave the endpoints as they are?
5. **Tool wrappers vs. router-inline SQL.** `patient.search`, `patient.get`, `appointment.search` exist only as router code. Options: (a) extract the SQL into services (touches existing files, behavior-preserving), (b) write new read-only service functions under `app/agent/tools/` (duplicates query logic). I recommend (a) done narrowly, one function each, with the router calling the extracted function — but it modifies working files, so it needs sign-off.
6. **Idempotency.** New `agent_tool_calls.idempotency_key` (`task_id:step_id`, deliberately not per-attempt) guards our own replays (we check for a recorded successful call before re-invoking), but cannot make `mark_visited_service` itself replay-safe. Pre-checks + post-conditions cover it; no change to existing services.
7. **`patient_scope`** has no existing data model (see D).
8. **Synchronous execution** ties LLM latency (multiple calls) to an HTTP request. Acceptable for Phase 1; revisit before write-heavy flows.
9. **Prompt injection.** Free-text fields returned by tools (patient names, notes) flow back into model context. Mitigation: tool outputs are passed as data fields with a strict schema, the Executor can call only its one named tool, and all writes pass deterministic pre/post-checks. Residual risk remains and is why approval gates exist.
10. **Verifier independence.** Verifier uses the same model family as Executor in Phase 1; deterministic checks are the real gate.
11. `0054` migration number is duplicated in the repo (`0054_diagnostic_workflow`, `0054_medication_master`); confirm `scripts/migrate.py` handles it before I add the agent migration.

## L. Recommended Phase 1 (read-only foundation + one guarded write)

1. Migration `0060` + `store.py` + permissions seed.
2. Tool registry + read-only tools: `patient.search`, `patient.get`, `appointment.search`, `appointment.get`, `encounter.get`, `invoice.get`, `queue.get`, `doctor.get`, `doctor_schedule.get`, `department.get` (schema-validated, tenant-scoped, output minimized).
3. `AuthContext` + shared permission function; authz tests.
4. Intake/Planner/Executor/Verifier prompts + strict parsers + `FakeLLM`; per-agent tests.
5. Orchestrator state machine (using only the states you listed that are reachable), retries, replan, escalation, raw-trace capture, deterministic checks, audit events.
6. `appointment.check_in` as the single write tool, with pre-checks/post-conditions from finding 2; approval flow wired but exercised via a test-only high-risk tool.
7. Security + orchestrator test suites from your sections 18–19; metrics as SQL views over `agent_*` (no separate metrics infra).
8. Improver, `confirm_and_check_in`, financial tools and payment→token: **explicitly deferred.**

Next phase after this: Phase 2 — payment/waiver → queue-token tools (high risk, approval-gated) and background execution.


---

## Phase 1 implementation status (added after approval)

Approved with the audit's recommendations. Delivered: everything in section L items 1-7, with these outcomes for the open questions in section K:

| # | Decision taken |
|---|---|
| 1 PHI to provider | Tool outputs are whitelisted/minimized (id, name, UHID, gender, last-4 phone); raw staff-typed input is stored in `agent_tasks.raw_input`. The layer is **off** unless `AGENT_LLM_PROVIDER=anthropic`. Provider agreement/retention policy is still an operator decision. |
| 2 Approval for check-in | Medium risk, no approval; every agent write is attributed to the human in `audit_log` (`agent.appointment.check_in`). Approval machinery exists and is exercised via a test-only high-risk tool. |
| 3 Whose authority | Always the initiating human's own permissions, re-resolved on resume. |
| 4 Existing endpoints | Left unchanged; the tools enforce permission + tenant themselves. `/visit` was **not** given a permission/audit gate (separate follow-up). |
| 5 Router-only queries | Extracted, behavior-preserving, into `patient_lookup_service`, `appointment_services.list_appointments_service`, `queue_read_service`, `doctor_schedule_read_service`, `check_in_service`; permission SQL into `staff_permissions`. Routes call the services. |
| 6 Idempotency | `agent_tasks` unique key; unique partial index on successful tool calls; replay from record. |
| 7 patient_scope | Optional allow-list in `AuthContext`, enforced in every tool. |
| 8 Synchronous | Yes. |
| 9 Prompt injection | Tool output is data under strict schemas; the Executor can't choose tools or arguments; writes are gated by prechecks/postchecks. Residual risk remains. |
| 10 Verifier independence | Same model family; deterministic checks and the evidence-substring rule are the real gate. |
| 11 Duplicate `0054` | `scripts/migrate.py` handles it (files are keyed by full filename); `0060_agent_layer` applied cleanly (renumbered from 0058 after merging main, which took 0058-0059). |

Deviations from the audit's section J: `app/agent/agents/improver.py` was **not** created (the Improver is deferred); `tests/agent/` holds the suites; the new services above were added; `requirements.txt` now pins `anthropic` (imported lazily).
