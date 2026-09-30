# AI Agent Automation Layer

Phases 1-3 of the layer described in `docs/implementation/AI_AGENT_IMPLEMENTATION_AUDIT.md`. Decision record: `docs/decisions/ADR-006-AI-AGENT-LAYER.md`.

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
| Worker | `worker.py`, `migrations/0061_agent_jobs.sql` | Phase 3: DB-backed job queue + in-process worker threads (see below) |
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

### Background execution (Phase 3)

`POST /api/agent/tasks` now records the task and enqueues a `start` job, answering **202** with the task in `RECEIVED`; clients poll `GET /api/agent/tasks/{id}`. Approving records the decision, moves the task to `APPROVED` and enqueues a `resume` job. Worker threads (`AGENT_WORKER_THREADS`, default 2, started with the app only when a provider is configured) claim jobs from `agent_jobs` with `FOR UPDATE SKIP LOCKED` and call `Orchestrator.process_job`. `AGENT_EXECUTION_MODE=inline` restores the old run-in-the-request behavior (tests, scripts).

- **No broker.** The queue is a table in the existing Postgres; multiple threads or app processes claim distinct jobs safely. One live job per task (unique partial index).
- **Nothing secret in a job.** A resume needs no approval token: the worker re-verifies the decided approval from `agent_approvals` (approved, unexpired, unspent, same arguments, approver ≠ initiator). Public `resume()` still requires the bearer token.
- **Crash recovery.** A claimed job holds a 10-minute lease; if the process dies the lease expires and the job is re-claimed. `process_job` is state-driven: `RECEIVED` starts the pipeline; `PLANNED…NEXT_STEP`/`APPROVED` continue execution (finished steps are skipped; a write that already committed is detected by its precheck or replayed from its recorded call -- never repeated; a decided-but-unspent approval is honored rather than re-requested); a crash before a plan exists (`INTAKE…PLANNING`) escalates because nothing ran and a human should resubmit.
- **Failures.** An exception escaping the orchestrator requeues the job with backoff (5s/30s/120s); after 3 attempts the job is `failed` and the task `ESCALATED`.
- **Cancellation** while queued/running is best-effort: the task becomes `CANCELLED` and the worker treats it as terminal at its next state change; a step already in flight completes.
- Metrics add `jobs_queued`, `average_queue_wait_s`, `job_failure_rate`.

### API

`POST /api/agent/tasks` (503 unless enabled), `GET /api/agent/tasks/{id}` (initiator or `staff.manage`), `POST .../approve|reject` (`agent.task.approve`, approver ≠ initiator), `POST .../cancel`, `GET /api/agent/metrics` (`staff.manage`). See Background execution above.

## Gap

- No frontend surface yet (API only).
- The worker is in-process (a restart interrupts running jobs; the lease + re-entrancy cover it) and has no heartbeat: a single task must finish within the 10-minute lease or it may be re-claimed by a second worker (writes stay safe via idempotency, but model calls would be repeated).
- Clients must poll; there is no push/streaming of task progress.
- `staff_roles.department_id` is carried in the context but, like the existing `require_permission`, not enforced.
- The existing `POST /appointments/{id}/visit`, patient/queue/invoice/encounter GETs still authorize with `get_current_staff` only, and several services still don't filter by hospital. The agent layer compensates in its tools; the endpoints are unchanged (a separate, deliberate follow-up).
- Verifier evidence matching is strict (normalized substring), so a paraphrasing model fails closed; watch `first_pass_verification_rate`.
- `human_override_rate` only counts rejected approvals; resolving an escalation isn't recorded yet.
- `cost_per_task` is reported as `tokens_per_task` (price varies by model).
- The Improver, `confirm_and_check_in`, refunds and line-item edits (financial tools beyond fee settlement).
- Approval is one human click per financial step; there is no policy engine (e.g. auto-approve small cash payments) and none should be added without a decision on who bears that risk.

## Recommended next phase

Phase 4: the offline Improver (analysis of failed/escalated/overridden traces; proposals only, never auto-deployed), then a frontend surface for tasks and approvals; refunds and other financial reversals only with their own scoping.
