"""
The Orchestrator: the ONLY thing that drives a task. The four runtime
agents never call each other and never control the workflow; this module
owns the lifecycle, authorization, dependency resolution, approvals, the
raw tool trace, deterministic checks, retries, replans, escalation and
the audit trail.

Guarantees it enforces (each has a test in tests/agent):
  * a model can't run a tool that isn't registered, isn't the step's
    tool, or that the human couldn't run themselves;
  * arguments come from the plan or from $from references resolved from
    RECORDED tool output -- never typed by a model at execution time;
  * a write happens only after a deterministic precheck, and (if the tool
    requires it) a human approval bound to those exact arguments;
  * the trace is what the tool actually returned, captured here, not
    anything a model reported;
  * a successful write is never retried, and never re-executed on resume
    (idempotency key = task/plan/step, enforced by a unique index).
"""

import logging
import time as _time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from app import config
from app.agent import refs, states, store
from app.agent.agents import executor as executor_agent
from app.agent.agents import intake as intake_agent
from app.agent.agents import planner as planner_agent
from app.agent.agents import verifier as verifier_agent
from app.agent.authz import reload_auth_context
from app.agent.llm import LLMClient, LLMUnavailable
from app.agent.models import AuthContext, ModelOutputError, PlanStep, TaskSpec
from app.agent.prompts import PROMPT_VERSIONS
from app.agent.task_types import TASK_TYPES
from app.agent.tools.registry import ToolError, ToolRegistry
from app.services.audit_log import record_audit_log

logger = logging.getLogger("app.agent")


@dataclass
class OrchestratorConfig:
    max_steps: int = planner_agent.MAX_STEPS
    max_step_retries: int = 1          # re-attempts of ONE step (transient tool error / failed read verification)
    max_replans: int = 1
    approval_ttl_minutes: int = 30
    timezone: str = config.DEFAULT_TIMEZONE


class _Stop(Exception):
    """Internal control flow: end the task in `state`."""

    def __init__(self, state: str, reason: str, result: dict | None = None):
        super().__init__(reason)
        self.state = state
        self.reason = reason
        self.result = result


class _Pause(Exception):
    """Internal control flow: leave the task waiting for a human."""


class _Replan(Exception):
    def __init__(self, failure: dict):
        self.failure = failure


@dataclass
class _Run:
    task_id: int
    ctx: AuthContext
    state: str
    spec: TaskSpec | None = None
    plan_id: int | None = None
    plan_version: int = 0
    steps: list[PlanStep] = field(default_factory=list)
    step_ids: dict[int, int] = field(default_factory=dict)      # step_no -> agent_steps.id
    outputs: dict[int, dict] = field(default_factory=dict)      # step_no -> raw tool output
    wrote: bool = False                                          # a write tool has succeeded in this task


class Orchestrator:
    def __init__(self, llm: LLMClient, registry: ToolRegistry, *, connection_factory=None,
                 config: OrchestratorConfig | None = None, today_fn=None):
        if connection_factory is None:
            from app.db.connection import get_connection as connection_factory
        self.llm = llm
        self.registry = registry
        self._connect = connection_factory
        self.config = config or OrchestratorConfig()
        self._today = today_fn or (lambda: datetime.now(ZoneInfo(self.config.timezone)).date())

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------

    @contextmanager
    def _tx(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                yield conn, cur

    def _event(self, run: _Run, event: str, details: dict | None = None) -> None:
        with self._tx() as (_, cur):
            store.add_event(cur, run.task_id, event, actor_id=run.ctx.actor_id, details=details)

    def _set_state(self, run: _Run, to_state: str, reason: str | None = None, result: dict | None = None) -> None:
        with self._tx() as (_, cur):
            store.transition(cur, run.task_id, run.state, to_state, reason=reason, result=result,
                             actor_id=run.ctx.actor_id)
        run.state = to_state

    def _call_agent(self, run: _Run, agent: str, fn):
        """Run one model call, record it, and turn model failure into an
        escalation (a human decides; the system never guesses)."""
        try:
            parsed, response = fn()
        except LLMUnavailable as exc:
            self._event(run, "model_unavailable", {"agent": agent, "error": str(exc)})
            raise _Stop(states.ESCALATED, f"model unavailable for the {agent} agent") from exc
        except ModelOutputError as exc:
            self._event(run, "model_output_rejected", {"agent": agent, "error": str(exc), "raw_output": exc.raw})
            raise _Stop(states.ESCALATED, f"the {agent} agent returned malformed output") from exc
        self._event(run, "model_call", {
            "agent": agent, "prompt_version": PROMPT_VERSIONS[agent], "model": response.model,
            "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
            "raw_output": response.text[:4000],
        })
        return parsed

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def submit(self, ctx: AuthContext, raw_input: str, *, idempotency_key: str | None = None) -> dict:
        """Run a task from raw input until it finishes or needs a human
        (approval / missing input). Idempotent on (hospital, key)."""
        with self._tx() as (_, cur):
            task_id, created = store.create_task(cur, ctx, raw_input, idempotency_key)
        if not created:
            return self.view(task_id, ctx.facility_id)

        run = _Run(task_id=task_id, ctx=ctx, state=states.RECEIVED)
        self._guarded(run, self._pipeline, raw_input)
        return self.view(task_id, ctx.facility_id)

    def resume(self, task_id: int, hospital_id: int, approval_token: str) -> dict:
        """Continue a task waiting in APPROVAL_REQUIRED using the token
        minted by approve(). The token is checked against this task, the
        step and the exact arguments; anything else raises ApprovalError
        and changes nothing."""
        with self._tx() as (_, cur):
            task = store.get_task(cur, task_id, hospital_id=hospital_id, for_update=True)
            if task is None:
                raise store.ApprovalError("unknown task")
            if task["state"] != states.APPROVAL_REQUIRED:
                raise store.ApprovalError("this task is not waiting for approval")
            pending = store.latest_approval(cur, task_id)
            if pending is None:
                raise store.ApprovalError("no approval was requested")
            store.verify_approval(cur, task_id=task_id, step_id=pending["step_id"], args=pending["args"],
                                  token=approval_token, initiator_staff_id=task["initiated_by"])
            # Claim the task in the same transaction (the row is locked), so a
            # concurrent second resume finds it no longer waiting and is refused
            # instead of executing the step twice.
            store.transition(cur, task_id, states.APPROVAL_REQUIRED, states.APPROVED,
                             reason="approval verified", actor_id=task["initiated_by"])

        run = self._load_run(task_id, hospital_id)
        if run is None:
            return self.view(task_id, hospital_id)
        self._guarded(run, self._execute, approval_token)
        return self.view(task_id, hospital_id)

    def approve(self, task_id: int, approver: AuthContext, *, approve: bool = True) -> dict:
        """A different human decides the pending approval. Approved ->
        the task resumes immediately with the freshly minted token (which
        never leaves this process); rejected -> the task is CANCELLED."""
        if not approver.has("agent.task.approve"):
            raise PermissionError("approver lacks agent.task.approve")

        with self._tx() as (_, cur):
            task = store.get_task(cur, task_id, hospital_id=approver.facility_id, for_update=True)
            if task is None:
                raise store.ApprovalError("unknown task")
            if task["state"] != states.APPROVAL_REQUIRED:
                raise store.ApprovalError("this task is not waiting for approval")
            pending = store.pending_approval(cur, task_id)
            if pending is None:
                raise store.ApprovalError("no pending approval")
            token = store.decide_approval(
                cur, pending["approval_id"], approver_staff_id=approver.actor_id,
                initiator_staff_id=task["initiated_by"], approve=approve,
                ttl_minutes=self.config.approval_ttl_minutes,
            )
            store.add_event(cur, task_id, "approval_decided", actor_id=approver.actor_id,
                            details={"approved": approve, "step_id": pending["step_id"], "tool": pending["tool"],
                                     "args": pending["args"]})
            if not approve:
                store.transition(cur, task_id, states.APPROVAL_REQUIRED, states.CANCELLED,
                                 reason="approval rejected", actor_id=approver.actor_id)
                return self.view_in(cur, task_id, approver.facility_id)

        return self.resume(task_id, approver.facility_id, token)

    def cancel(self, task_id: int, actor: AuthContext) -> dict:
        with self._tx() as (_, cur):
            task = store.get_task(cur, task_id, hospital_id=actor.facility_id, for_update=True)
            if task is None:
                raise store.ApprovalError("unknown task")
            if task["initiated_by"] != actor.actor_id and not actor.has("agent.task.approve"):
                raise PermissionError("only the initiator or an approver can cancel a task")
            if task["state"] in states.TERMINAL_STATES:
                raise store.InvalidTransition(f"task is already {task['state']}")
            store.transition(cur, task_id, task["state"], states.CANCELLED, reason="cancelled by user",
                             actor_id=actor.actor_id)
            return self.view_in(cur, task_id, actor.facility_id)

    def view(self, task_id: int, hospital_id: int) -> dict:
        with self._tx() as (_, cur):
            return self.view_in(cur, task_id, hospital_id)

    @staticmethod
    def view_in(cur, task_id: int, hospital_id: int) -> dict:
        task = store.get_task(cur, task_id, hospital_id=hospital_id)
        pending = store.pending_approval(cur, task_id) if task and task["state"] == states.APPROVAL_REQUIRED else None
        return {
            "task_id": task["id"],
            "state": task["state"],
            "task_type": task["task_type"],
            "risk_tier": task["risk_tier"],
            "reason": task["final_reason"],
            "result": task["result"],
            "pending_approval": (
                {"step": pending["step_no"], "tool": pending["tool"], "args": pending["args"]} if pending else None
            ),
        }

    # ------------------------------------------------------------------
    # pipeline
    # ------------------------------------------------------------------

    def _guarded(self, run: _Run, fn, *args) -> None:
        """Run a pipeline stage; every outcome ends in a legal state. A
        crash mid-task can't leave it in limbo: it is escalated."""
        try:
            fn(run, *args)
        except _Pause:
            pass
        except _Stop as stop:
            self._finish(run, stop)
        except Exception as exc:  # last resort -- never leave a task non-terminal
            logger.exception("agent task %s crashed", run.task_id)
            self._finish(run, _Stop(states.ESCALATED, f"internal error: {type(exc).__name__}"))

    def _finish(self, run: _Run, stop: _Stop) -> None:
        with self._tx() as (_, cur):
            task = store.get_task(cur, run.task_id, hospital_id=run.ctx.facility_id)
            current = task["state"]
            if current in states.TERMINAL_STATES:
                return
            to_state = stop.state if states.can_transition(current, stop.state) else states.ESCALATED
            store.transition(cur, run.task_id, current, to_state, reason=stop.reason, result=stop.result,
                             actor_id=run.ctx.actor_id)
        run.state = to_state

    def _pipeline(self, run: _Run, raw_input: str) -> None:
        self._intake(run, raw_input)
        self._authorize(run)
        self._plan(run, previous_failure=None)
        self._execute(run, None)

    # -- intake -----------------------------------------------------------

    def _intake(self, run: _Run, raw_input: str) -> None:
        self._set_state(run, states.INTAKE)
        spec = self._call_agent(
            run, "intake",
            lambda: intake_agent.run_intake(self.llm, raw_input, today=self._today(), timezone=self.config.timezone),
        )
        spec = intake_agent.harden_spec(spec)
        with self._tx() as (_, cur):
            store.save_spec(cur, run.task_id, spec)
        run.spec = spec

        if spec.status == "out_of_scope":
            raise _Stop(states.OUT_OF_SCOPE, "the request matches no supported task type")
        if spec.status == "needs_input":
            raise _Stop(states.NEEDS_INPUT, "required information is missing",
                        {"missing_fields": spec.missing_fields})
        self._set_state(run, states.INTAKE_VALIDATED)

    # -- authorization ----------------------------------------------------

    def _authorize(self, run: _Run) -> None:
        self._set_state(run, states.AUTHORIZATION_CHECK)
        required = ("agent.task.create", *TASK_TYPES[run.spec.task_type].required_permissions)
        missing = [p for p in required if not run.ctx.has(p)]
        if missing:
            raise _Stop(states.UNAUTHORIZED, "the actor lacks permission: " + ", ".join(missing))

    # -- planning ---------------------------------------------------------

    def _plan(self, run: _Run, previous_failure: dict | None) -> None:
        while True:
            if run.state != states.PLANNING:
                self._set_state(run, states.PLANNING)
            plan = self._call_agent(
                run, "planner",
                lambda: planner_agent.run_planner(
                    self.llm, run.spec, self.registry.describe_for(run.ctx), previous_failure=previous_failure),
            )
            try:
                plan = planner_agent.validate_plan(plan, self.registry, run.ctx, max_steps=self.config.max_steps)
            except planner_agent.PlanRejected as exc:
                self._event(run, "plan_rejected", {"reason": str(exc)})
                if self._replans_used(run) >= self.config.max_replans:
                    raise _Stop(states.ESCALATED, f"the planner could not produce a valid plan: {exc}") from exc
                self._bump_replan(run)
                previous_failure = {"reason": f"the previous plan was rejected: {exc}"}
                continue
            break

        with self._tx() as (_, cur):
            run.plan_id, run.plan_version = store.save_plan(cur, run.task_id, plan, PROMPT_VERSIONS["planner"])
            run.step_ids = {s["step_no"]: s["id"] for s in store.list_steps(cur, run.plan_id)}
        run.steps = plan.steps
        run.outputs = {}

        if plan.status == "blocked":
            raise _Stop(states.BLOCKED, plan.reason)
        if plan.status == "too_large":
            raise _Stop(states.BLOCKED, f"task too large for one plan: {plan.reason}")
        if plan.status == "unverifiable":
            raise _Stop(states.UNVERIFIABLE, plan.reason)
        self._set_state(run, states.PLANNED)

    def _replans_used(self, run: _Run) -> int:
        with self._tx() as (_, cur):
            return store.get_task(cur, run.task_id, hospital_id=run.ctx.facility_id)["replan_count"]

    def _bump_replan(self, run: _Run) -> None:
        with self._tx() as (_, cur):
            store.bump_replan(cur, run.task_id)

    # -- execution --------------------------------------------------------

    def _execute(self, run: _Run, approval_token: str | None) -> None:
        while True:
            try:
                for step in run.steps:
                    if self._step_state(run, step) == "VERIFIED":
                        continue
                    self._run_step(run, step, approval_token)
                    approval_token = None  # an approval is spent on its own step only
                self._final_signoff(run)
                return
            except _Replan as replan:
                self._bump_replan(run)
                self._plan(run, previous_failure=replan.failure)
                approval_token = None

    def _step_state(self, run: _Run, step: PlanStep) -> str:
        with self._tx() as (_, cur):
            return store.step_state(cur, run.step_ids[step.id])

    def _ensure_executing(self, run: _Run) -> None:
        if run.state == states.STEP_VERIFIED:
            self._set_state(run, states.NEXT_STEP)
        if run.state != states.EXECUTING:
            self._set_state(run, states.EXECUTING)

    def _run_step(self, run: _Run, step: PlanStep, approval_token: str | None) -> None:
        """Drive ONE step to VERIFIED, or raise a control exception
        (_Stop / _Pause / _Replan). Retries of this step loop in here."""
        feedback: str | None = None
        tool = self.registry.get(step.tool)
        step_id = run.step_ids[step.id]
        max_attempts = 1 + self.config.max_step_retries

        while True:
            self._ensure_executing(run)

            # 1. authorization, re-checked at execution time (defense in depth)
            if tool is None or not tool.permitted(run.ctx):
                raise _Stop(states.UNAUTHORIZED, f"the actor may not run {step.tool}")

            # 2. resolve references from RECORDED outputs
            try:
                resolved = refs.resolve_args(step.args, run.outputs)
            except refs.RefAmbiguous as exc:
                raise _Stop(states.NEEDS_INPUT, str(exc),
                            {"ambiguous_step": exc.step, "list": exc.list_name, "candidates": exc.candidates}) from exc
            except refs.RefError as exc:
                raise _Stop(states.INPUT_PROBLEM, str(exc)) from exc

            # 3. validate arguments against the tool's schema
            try:
                parsed = tool.validate_args(resolved)
            except ToolError as exc:
                raise _Stop(states.INPUT_PROBLEM, exc.message) from exc
            args = parsed.model_dump(mode="json")

            # 4. deterministic precheck (writes)
            if tool.is_write:
                with self._tx() as (_, cur):
                    try:
                        pre = tool.precheck(cur, run.ctx, parsed)
                    except ToolError as exc:
                        raise _Stop(states.BLOCKED, exc.message) from exc
                if pre.status == "blocked":
                    raise _Stop(states.BLOCKED, pre.reason, {"detail": pre.detail})
                if pre.status == "needs_input":
                    raise _Stop(states.NEEDS_INPUT, pre.reason, {"detail": pre.detail})
                if pre.status == "already_done":
                    self._event(run, "step_skipped_already_done", {
                        "step": step.id, "tool": step.tool, "plan_version": run.plan_version,
                        "reason": pre.reason, "detail": pre.detail,
                    })
                    output = {"already_done": True, **pre.detail}
                    trace = [{"kind": "precheck_already_done", "step": step.id, "tool": step.tool,
                              "reason": pre.reason, "detail": pre.detail}]
                    with self._tx() as (_, cur):
                        checks = self._postchecks(tool, cur, run.ctx, parsed, output)
                    run.outputs[step.id] = output
                    self._set_state(run, states.VERIFYING)
                    verified, feedback = self._verify_step(run, step, tool, output, trace, checks, args)
                    if verified:
                        return
                    continue

            # 5. approval gate: a human decision on THESE arguments
            approval_id = None
            needs_approval = tool.requires_approval or (step.irreversible and tool.is_write)
            if needs_approval:
                if approval_token is None:
                    with self._tx() as (_, cur):
                        store.request_approval(cur, run.task_id, step_id, args)
                        store.set_step_state(cur, step_id, "AWAITING_APPROVAL")
                    self._set_state(run, states.APPROVAL_REQUIRED, reason=f"approval required for {step.tool}")
                    raise _Pause()
                with self._tx() as (_, cur):
                    try:
                        approval_id = store.verify_approval(
                            cur, task_id=run.task_id, step_id=step_id, args=args, token=approval_token,
                            initiator_staff_id=run.ctx.actor_id)
                    except store.ApprovalError as exc:
                        raise _Stop(states.ESCALATED, f"approval could not be honored: {exc}") from exc

            # 6. the Executor model decides only proceed / input problem
            try:
                decision = self._call_agent(
                    run, "executor",
                    lambda: executor_agent.run_executor(
                        self.llm, step, args, {d: run.outputs[d] for d in step.depends_on if d in run.outputs},
                        approval_granted=approval_id is not None, verifier_feedback=feedback),
                )
            except executor_agent.ExecutorViolation as exc:
                self._event(run, "executor_violation", {"error": str(exc)})
                raise _Stop(states.ESCALATED, "the executor deviated from its single step") from exc
            if decision.action == "input_problem":
                raise _Stop(states.INPUT_PROBLEM, decision.notes or "the executor judged the inputs unusable")

            # 7. call the tool; the orchestrator captures the raw trace
            with self._tx() as (_, cur):
                attempt = store.set_step_state(cur, step_id, "RUNNING", bump_attempt=True)
            outcome = self._call_tool(run, step, tool, parsed, args, step_id, attempt, approval_id)

            if outcome["status"] == "success":
                output = outcome["response"]
                run.outputs[step.id] = output
                if tool.is_write:
                    run.wrote = True
                self._set_state(run, states.VERIFYING)
                verified, feedback = self._verify_step(
                    run, step, tool, output, [outcome["trace"]], outcome["checks"], args)
                if verified:
                    return
                continue

            # error path
            code = outcome["code"]
            if code == "permission_denied":
                raise _Stop(states.UNAUTHORIZED, outcome["error"])
            if code == "invalid_arguments":
                raise _Stop(states.INPUT_PROBLEM, outcome["error"])
            if outcome["retryable"] and attempt < max_attempts:
                self._event(run, "step_retry", {"step": step.id, "attempt": attempt, "error": outcome["error"]})
                feedback = None
                continue
            raise _Stop(states.TOOL_ERROR, outcome["error"], {"code": code, "step": step.id})

    def _call_tool(self, run: _Run, step: PlanStep, tool, parsed, args: dict, step_id: int, attempt: int,
                   approval_id: int | None) -> dict:
        base_key = f"agent-task:{run.task_id}:plan:{run.plan_version}:step:{step.id}"
        # Writes are keyed per step (one success, ever); reads per attempt so a
        # verification-driven re-read doesn't collide with the earlier success.
        key = base_key if tool.is_write else f"{base_key}:attempt:{attempt}"
        started = _time.monotonic()

        with self._tx() as (conn, cur):
            if tool.is_write:
                existing = store.find_successful_call(cur, key)
                if existing is not None:
                    response = existing["raw_response"]
                    store.add_event(cur, run.task_id, "tool_call_replayed", actor_id=run.ctx.actor_id,
                                    details={"step": step.id, "tool": step.tool, "idempotency_key": key})
                    checks = self._postchecks(tool, cur, run.ctx, parsed, response)
                    store.add_verification(cur, task_id=run.task_id, step_id=step_id, kind="deterministic",
                                           verdict="pass" if all(c["passed"] for c in checks) else "fail",
                                           criteria=checks)
                    return {"status": "success", "response": response, "checks": checks,
                            "trace": {"kind": "tool_call", "step": step.id, "tool": step.tool, "attempt": attempt,
                                      "args": args, "status": "success", "response": response, "replayed": True}}

            response, error, code, retryable = None, None, None, False
            try:
                with conn.transaction():  # savepoint: a failing tool can't leave partial writes behind
                    if approval_id is not None:
                        store.consume_approval(cur, approval_id)
                    response = tool.execute(cur, run.ctx, args)
                    if tool.is_write:
                        resource_type, resource_id = (
                            tool.audit_resource(parsed, response) if tool.audit_resource else (step.tool, None))
                        record_audit_log(
                            cur, hospital_id=run.ctx.facility_id, staff_id=run.ctx.actor_id,
                            action=f"agent.{step.tool}", resource_type=resource_type, resource_id=resource_id,
                            details={"task_id": run.task_id, "plan_version": run.plan_version, "step": step.id,
                                     "idempotency_key": key},
                        )
            except ToolError as exc:
                error, code, retryable = exc.message, exc.code, exc.retryable
            except store.ApprovalError as exc:
                error, code = str(exc), "approval_invalid"
            except Exception as exc:  # any service/DB failure becomes a recorded tool error
                error, code = f"{type(exc).__name__}: {exc}", "internal_error"
                retryable = type(exc).__name__ == "OperationalError"

            status = "success" if error is None else ("denied" if code == "permission_denied" else "error")
            store.record_tool_call(
                cur, task_id=run.task_id, step_id=step_id, attempt=attempt, tool=step.tool, args=args,
                idempotency_key=key,
                status=status, raw_response=response, error=error,
                duration_ms=int((_time.monotonic() - started) * 1000),
            )
            trace = {"kind": "tool_call", "step": step.id, "tool": step.tool, "attempt": attempt, "args": args,
                     "status": status, "response": response, "error": error}
            if status != "success":
                return {"status": "error", "code": code, "error": error, "retryable": retryable, "trace": trace}

            checks = self._postchecks(tool, cur, run.ctx, parsed, response)
            store.add_verification(cur, task_id=run.task_id, step_id=step_id, kind="deterministic",
                                   verdict="pass" if all(c["passed"] for c in checks) else "fail", criteria=checks)
            return {"status": "success", "response": response, "checks": checks, "trace": trace}

    @staticmethod
    def _postchecks(tool, cur, ctx, parsed, output: dict) -> list[dict]:
        """Deterministic verification. Reads: the call succeeded and its
        output passed the tool's schema (enforced by Tool.execute). Writes:
        the tool's own post-conditions, re-read from the hospital service."""
        if not tool.is_write:
            return [{"name": "tool_call_succeeded_with_schema_valid_output", "passed": True,
                     "detail": f"{tool.name} returned output conforming to its output schema"}]
        try:
            results = tool.postchecks(cur, ctx, parsed, output)
        except Exception as exc:  # a crashing check is a failing check, never a silent pass
            return [{"name": "postchecks_executed", "passed": False, "detail": f"{type(exc).__name__}: {exc}"}]
        return [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in results]

    # -- verification -----------------------------------------------------

    def _verify_step(self, run: _Run, step: PlanStep, tool, output: dict, trace: list[dict], checks: list[dict],
                     args: dict) -> tuple[bool, str | None]:
        """(True, None) = step verified. (False, feedback) = retry the step
        with the verifier's feedback. Raises _Stop/_Replan otherwise."""
        criteria = [step.success_check]
        verdict = self._call_verifier(run, criteria, output, trace, checks)
        step_id = run.step_ids[step.id]

        with self._tx() as (_, cur):
            store.add_verification(cur, task_id=run.task_id, step_id=step_id, kind="llm", verdict=verdict.verdict,
                                   criteria=[c.model_dump() for c in verdict.criteria], detail=verdict.fix_hint)

        if verdict.verdict == "pass":
            with self._tx() as (_, cur):
                store.set_step_state(cur, step_id, "VERIFIED")
            self._set_state(run, states.STEP_VERIFIED)
            return True, None

        with self._tx() as (_, cur):
            store.set_step_state(cur, step_id, "FAILED")

        if verdict.verdict == "uncertain":
            raise _Stop(states.UNCERTAIN, "the verifier could not decide; a human must review",
                        {"step": step.id, "fix_hint": verdict.fix_hint})

        # verdict == fail
        if tool.is_write:
            # The write already happened and is not retried: a human reconciles.
            raise _Stop(states.ESCALATED, "a write step failed verification after it executed",
                        {"step": step.id, "fix_hint": verdict.fix_hint})

        with self._tx() as (_, cur):
            attempts = store.step_attempts(cur, step_id)
        if attempts < 1 + self.config.max_step_retries:
            self._event(run, "step_retry", {"step": step.id, "attempt": attempts, "verifier": verdict.fix_hint})
            return False, verdict.fix_hint

        if self._replans_used(run) < self.config.max_replans and not run.wrote:
            raise _Replan({"step": step.id, "tool": step.tool, "args": args, "verifier_fix_hint": verdict.fix_hint})
        raise _Stop(states.VERIFICATION_FAILED, "step verification failed",
                    {"step": step.id, "fix_hint": verdict.fix_hint})

    def _call_verifier(self, run: _Run, criteria: list[str], output: dict, trace: list[dict], checks: list[dict]):
        result = self._call_agent(
            run, "verifier",
            lambda: verifier_agent.run_verifier(self.llm, criteria, output, trace, checks),
        )
        return verifier_agent.enforce(result, criteria, output, trace, checks)

    def _final_signoff(self, run: _Run) -> None:
        """Task-level verification against the acceptance criteria, over the
        whole recorded trace and every deterministic check."""
        if run.state == states.STEP_VERIFIED:
            self._set_state(run, states.VERIFYING)
        with self._tx() as (_, cur):
            trace = store.plan_trace(cur, run.task_id, run.plan_id, run.plan_version)
            checks = store.latest_deterministic_checks(cur, run.task_id, run.plan_id)
        output = {"outputs_by_step": {str(k): v for k, v in sorted(run.outputs.items())}}

        verdict = self._call_verifier(run, run.spec.acceptance_criteria, output, trace, checks)
        with self._tx() as (_, cur):
            store.add_verification(cur, task_id=run.task_id, step_id=None, kind="llm", verdict=verdict.verdict,
                                   criteria=[c.model_dump() for c in verdict.criteria], detail=verdict.fix_hint)

        if verdict.verdict == "pass":
            already = any(t["kind"] == "precheck_already_done" for t in trace)
            self._set_state(run, states.COMPLETED, reason="all acceptance criteria verified",
                            result={"already_done": already, "outputs_by_step": output["outputs_by_step"]})
            return
        if verdict.verdict == "uncertain":
            raise _Stop(states.UNCERTAIN, "final verification was uncertain; a human must review",
                        {"fix_hint": verdict.fix_hint})
        if run.wrote:
            raise _Stop(states.ESCALATED, "the task's writes executed but final verification failed",
                        {"fix_hint": verdict.fix_hint})
        raise _Stop(states.VERIFICATION_FAILED, "final verification failed", {"fix_hint": verdict.fix_hint})

    # ------------------------------------------------------------------
    # resume support
    # ------------------------------------------------------------------

    def _load_run(self, task_id: int, hospital_id: int) -> _Run | None:
        with self._tx() as (_, cur):
            task = store.get_task(cur, task_id, hospital_id=hospital_id)
            ctx = reload_auth_context(cur, task["auth_context"])
            plan = store.current_plan(cur, task_id)
            steps = store.list_steps(cur, plan["id"])
            outputs = store.successful_outputs(cur, task_id, plan["id"])
            wrote = any(
                (self.registry.get(s["tool"]) is not None and self.registry.get(s["tool"]).is_write)
                and s["step_no"] in outputs for s in steps
            )
        run = _Run(task_id=task_id, ctx=ctx, state=task["state"]) if ctx else None
        if run is None:
            # The initiator was deactivated while the task waited: fail closed.
            with self._tx() as (_, cur):
                store.transition(cur, task_id, task["state"], states.CANCELLED,
                                 reason="the initiating account is no longer active")
            return None
        run.spec = TaskSpec.model_validate(task["task_spec"])
        run.plan_id, run.plan_version = plan["id"], plan["version"]
        run.steps = [PlanStep.model_validate(s) for s in plan["plan"]["steps"]]
        run.step_ids = {s["step_no"]: s["id"] for s in steps}
        run.outputs = outputs
        run.wrote = wrote
        return run
