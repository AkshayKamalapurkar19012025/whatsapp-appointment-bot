# ADR-006: AI Agent Automation Layer

## Status

Accepted. Phase 1 implemented (`migrations/0058_agent_layer.sql`, `app/agent/`).

## Context

Staff perform repetitive OPD tasks (e.g. "check in Ravi's 10:30"). We want language-model automation without giving a model any path to hospital data other than the paths humans already have, and without weakening identity, authorization, encounter, queue or billing rules that already exist.

## Decision

1. **The agent layer is a client of the existing services, inside the existing app.** No microservice, no second data path. Models reach data only through registered tools that call `app/services/*`. Nothing under `app/agent/` executes hospital SQL (test-enforced).
2. **A deterministic orchestrator drives five logical agents.** Intake, Planner, Executor and Verifier are model calls with strict JSON contracts; the Improver is offline (not built yet). The orchestrator — not a model — owns lifecycle, authorization, argument resolution, approvals, raw-trace capture, deterministic checks, retries, replans and escalation. Whatever a model says is re-checked by code (`harden_spec`, `validate_plan`, `enforce`).
3. **Models don't type identifiers or claim outcomes.** Ids flow through `$from` references resolved from recorded tool output only when exactly one candidate exists; the raw trace is what the tool returned; the Executor cannot report a result.
4. **The agent acts as the initiating human, never above them.** Context is built server-side from the staff session, permissions come from the existing RBAC query, and are re-resolved on resume. Approval of a high-risk step must come from a different human and is bound to that step's exact arguments.
5. **Tools mirror the business rules, they don't reimplement them.** Where a router held the only implementation (patient search/get, appointment listing, queue, doctor schedule, check-in + notifications), the code was moved into a service the route and the tool both call; routes' behavior is unchanged. Permission resolution moved to `app/services/staff_permissions.py` for the same reason.
6. **Some requested tools are not built because they would bypass rules:** `encounter.create` and `queue.generate_token` (see `docs/architecture/AI_AGENT_LAYER.md`).
7. **Check-in is medium risk with no approval,** because staff already perform the identical action unapproved; the agent's actions are attributed to the human in `audit_log` (`agent.*`) and fully traceable in the `agent_*` tables. High-risk tools (financial, external, irreversible) require approval by registry invariant.

## Alternatives considered

- **Give the model SQL/DB access** — forbidden: bypasses validation, authorization, domain rules, transactions and audit.
- **Let the Executor model call tools with its own arguments** — rejected: it re-opens argument invention and fabricated traces for no benefit; the model's useful judgment ("do these inputs look right?") is kept.
- **A service account for the agent** — rejected: it could exceed the user and blurs attribution.
- **Approval per plan** — rejected: the approver should see the concrete, verified arguments of the specific action.
- **A separate agent service/process** — rejected: no scale need; it would need its own copy of authorization and tenant scoping.

## Consequences

- Patient data reaches a model provider, in minimized form (id/name/UHID/gender/last-4 phone, plus staff-typed raw input). Enabling `AGENT_LLM_PROVIDER` in production requires confirming the provider agreement covers this.
- Existing endpoints that authorize with `get_current_staff` only, or lack tenant filters, remain as they are; the tool layer enforces permission and tenant itself.
- Each task costs several model calls and runs synchronously in Phase 1.
