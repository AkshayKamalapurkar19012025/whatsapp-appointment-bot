# AI Agent Automation Layer

Phases 1-2 of the layer described in `docs/implementation/AI_AGENT_IMPLEMENTATION_AUDIT.md`. Decision record: `docs/decisions/ADR-006-AI-AGENT-LAYER.md`.

## Current State (verified against the code)

A controlled task-automation layer sits **above** the existing HospitalOS services, inside the same FastAPI app (`app/agent/`, `app/api/agent.py`). It is never a system of record: the only path from a model to hospital data is

```
model → registered Tool (app/agent/tools) → existing app/services/* → DB
```

Nothing under `app/agent/` runs hospital SQL. `store.py` and `metrics.py` execute SQL only against the seven `agent_*` tables (`migrations/0060_agent_layer.sql`); `tests/agent/test_security.py` enforces this structurally (AST scan) and that tools import only `app.services.*`.

### Pipeline

```
raw input → INTAKE → TaskSpec → AUTHORIZATION → PLANNER → Plan
  → ORCHESTRATOR → (per step) resolve refs → precheck → [approval] → EXECUTOR decision
  → Tool (raw trace captured by the orchestrator) → deterministic checks → VERIFIER
  → next step / COMPLETED / failure state / human escalation → audit trail
```

| Piece | File | Notes |
|---|---|---|
| Intake | `agents/intake.py` | model proposes a TaskSpec; `harden_spec` decides: unknown type → out of scope; missing/invalid required field → `needs_input` (no defaults); risk never below the type's floor; mandatory acceptance criteria always appended |
| Planner | `agents/planner.py` | `validate_plan`: only registered *and permitted* tools; args must match the tool schema; ≤6 steps; a write must depend on read steps and take its identifying args as `$from` references; a write may not depend on a write; `irreversible` normalized up from the registry |
| Executor | `agents/executor.py` | the model only says "proceed" or "input problem"; the orchestrator calls the tool with the planned, resolved args and records the raw response — there is nothing for the model to fabricate |
| Verifier | `agents/verifier.py` | `enforce`: failed deterministic check ⇒ fail; every criterion needs a verdict; a `pass` needs evidence that actually appears in the output/trace/checks; empty trace ⇒ fail |
| Orchestrator | `orchestrator.py` | lifecycle, authz, refs, approvals, retries (1/step), replans (1/task), escalation, idempotency, audit |
| Tool registry | `tools/registry.py`, `tools/hospital.py` | pydantic in/out models with `extra="forbid"`; output is a whitelist; registration refuses unsafe definitions |
| Authorization | `authz.py`, `app/services/staff_permissions.py` | context built server-side from the human's session; permissions resolved by the same query as `require_permission`; rebuilt fresh on resume |
| Persistence | `store.py` | tasks, plans, steps, tool calls, approvals, verifications, ordered event log; `get_task_trace` reconstructs a run |
| Metrics | `metrics.py`, `GET /api/agent/metrics` | see the module docstring for exact definitions |
| Model seam | `llm.py` | `LLMClient`; `FakeLLM` (tests), `AnthropicLLM` (lazy import), off unless `AGENT_LLM_PROVIDER=anthropic` |

The Improver (offline analysis of failed traces) is **not built** in Phase 1; the data it needs (`agent_audit_events`, `agent_verifications`, metrics, prompt versions in `agent_plans`/events) is being recorded.

### Lifecycle

The states are the master prompt's, plus `UNAUTHORIZED` (an authorization refusal fits none of the others). `states.TRANSITIONS` is the whole truth and the orchestrator refuses any other move. Approval is **per step** (entered from `EXECUTING` with the concrete resolved arguments in front of the human), not per plan.

### Tools (Phase 1)

Phase 1 -- Read: `patient.search`, `patient.get`, `appointment.search`, `appointment.get`, `doctor.get`, `department.get`, `doctor_schedule.get`, `queue.get`, `encounter.get`, `invoice.get`. Write: `appointment.check_in` (medium risk, no approval — it does exactly what the reception "Check In" button does, including the patient's arrival notification).

Phase 2 -- three **financial, high-risk, approval-gated** tools (`tools/billing.py`) that settle the consultation fee and, as the service's own effect, issue the queue token: `appointment.record_payment` (records that the patient paid CASH/UPI/CARD/OTHER), `appointment.waive_consultation_fee` (3-day revisit policy, with a reason), `appointment.settle_free_visit` (zero-fee visits only). They call the same services as the reception screens through `app/services/front_desk_billing_service.py` (the routes now call it too), so the audit rows (`bill.record_payment`, `appointment.waive_payment`) and the queue-token notification are identical; the agent adds its own `agent.<tool>` row.

Money is never typed by a model: `record_payment`'s `expected_amount` must be a `$from` reference to `invoice.get`'s `total_due` (`Tool.reference_only_args`, enforced at plan validation); the precheck and the handler both re-read the invoice and refuse if the amount due changed since the human was shown it; the amount actually charged is still server-computed. The approver sees a preview (patient, doctor, time, amount/reason) stored with the request (`agent_approvals.preview`). Approval-gated tools must declare that preview at registration.

**Deliberately not tools:** `encounter.create` (an OPD encounter is created inside appointment booking; a standalone creator would be a second encounter path) and `queue.generate_token` (a token is issued only when the consultation fee is paid or waived — exposing it alone would bypass that rule). `appointment.check_in` therefore leaves `token_number` NULL, and its post-conditions verify `status == CHECKED_IN`, not a token.

### Safety properties (each has a test in `tests/agent/`)

- No SQL/DB access from the agent layer; tools call only existing services.
- Arguments the model doesn't type: identifiers flow via `$from` references resolved from *recorded* output, and only when the list has exactly one item (0 or ≥2 ⇒ `NEEDS_INPUT` with the candidates listed).
- Tenant check on every tool (several services take a bare id and never look at `hospital_id`); other tenants' records read as "not found". Optional patient-scope allow-list narrows a task to given patients.
- Minimization: patient tools return id/name/UHID/gender/last-4 of phone only; the output schema is a whitelist enforced at the tool boundary, so no DOB, government id, address or full phone reaches a model, a trace or a log.
- Approvals: minted server-side only when a *different* human with `agent.task.approve` decides; stored as a SHA-256; bound to one task, one step and the exact argument hash; expiring; single-use; consumed in the same transaction as the write.
- Idempotency: `agent_tasks(hospital_id, idempotency_key)` unique; a successful write is keyed `task:plan:step` under a unique partial index, never retried, and replayed from the record if the step is re-entered. A write whose verification fails is escalated, not retried.
- Model output is parsed strictly (one JSON object, no extra keys, no code fence); anything else escalates. Model outage escalates. A crash can't leave a task non-terminal.

### Data model (`migrations/0060_agent_layer.sql`)

`agent_tasks`, `agent_plans`, `agent_steps`, `agent_tool_calls`, `agent_approvals`, `agent_verifications`, `agent_audit_events`. (The master prompt's `agent_task_inputs` is `agent_tasks.raw_input`; `agent_failures` are events.) Permissions seeded: `agent.task.create|read|approve`, `patient.read`, `appointment.read`, `queue.read`, `encounter.read`, `invoice.read`, `directory.read`, `appointment.check_in` — granted per role in the migration's own comments (LAB_TECH/PHARMACIST get none).

### API

`POST /api/agent/tasks` (503 unless enabled), `GET /api/agent/tasks/{id}` (initiator or `staff.manage`), `POST .../approve|reject` (`agent.task.approve`, approver ≠ initiator), `POST .../cancel`, `GET /api/agent/metrics` (`staff.manage`). The orchestrator runs synchronously in the request in Phase 1.

## Gap

- No frontend surface yet (API only).
- No background execution; a task holds its HTTP request open for the model calls (≈ intake + planner + 3×(executor+verifier) + final verifier for the slice).
- `staff_roles.department_id` is carried in the context but, like the existing `require_permission`, not enforced.
- The existing `POST /appointments/{id}/visit`, patient/queue/invoice/encounter GETs still authorize with `get_current_staff` only, and several services still don't filter by hospital. The agent layer compensates in its tools; the endpoints are unchanged (a separate, deliberate follow-up).
- Verifier evidence matching is strict (normalized substring), so a paraphrasing model fails closed; watch `first_pass_verification_rate`.
- `human_override_rate` only counts rejected approvals; resolving an escalation isn't recorded yet.
- `cost_per_task` is reported as `tokens_per_task` (price varies by model).
- The Improver, `confirm_and_check_in`, refunds and line-item edits (financial tools beyond fee settlement).
- Approval is one human click per financial step; there is no policy engine (e.g. auto-approve small cash payments) and none should be added without a decision on who bears that risk.

## Recommended next phase

Phase 3: background execution (tasks currently hold the HTTP request open), then the Improver; refunds and other financial reversals only with their own scoping.
