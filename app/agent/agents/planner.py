"""Agent 2 -- Planner: TaskSpec -> ordered, independently verifiable
steps. Never executes. validate_plan is the deterministic gate: a plan
that uses an unlisted or unpermitted tool, invents arguments, references
outputs that don't exist, or writes on unverified inputs is rejected
before any step runs."""

from typing import get_args

from app.agent import prompts, refs
from app.agent.llm import LLMClient, LLMResponse
from app.agent.models import AuthContext, Plan, PlanStep, TaskSpec, parse_model_json
from app.agent.tools.registry import ToolRegistry

MAX_STEPS = 6
MAX_LESSONS = 5


class PlanRejected(Exception):
    """The Planner's output is well-formed JSON but not an acceptable plan."""


def run_planner(
    llm: LLMClient,
    spec: TaskSpec,
    tools: list[dict],
    *,
    lessons: list[str] | None = None,
    previous_failure: dict | None = None,
) -> tuple[Plan, LLMResponse]:
    payload = {
        "task_spec": spec.model_dump(),
        "available_tools": tools,
        "lessons": (lessons or [])[:MAX_LESSONS],
        "max_steps": MAX_STEPS,
    }
    if previous_failure is not None:
        payload["previous_failure"] = previous_failure
    response = llm.complete(agent="planner", system=prompts.PLANNER, payload=payload)
    return parse_model_json(response.text, Plan), response


def _item_fields(output_model, list_name: str) -> set[str] | None:
    field = output_model.model_fields.get(list_name)
    if field is None:
        return None
    for arg in get_args(field.annotation):
        if hasattr(arg, "model_fields"):
            return set(arg.model_fields)
    return None


def validate_plan(plan: Plan, registry: ToolRegistry, ctx: AuthContext, *, max_steps: int = MAX_STEPS) -> Plan:
    """Returns the plan (with `irreversible` normalized to the registry's
    truth) or raises PlanRejected. Non-"ok" plans carry no steps."""
    if plan.status != "ok":
        return Plan(
            status=plan.status,
            steps=[],
            change_from_previous=plan.change_from_previous,
            reason=(plan.reason or "").strip() or f"planner returned {plan.status}",
        )

    if not plan.steps:
        raise PlanRejected("an 'ok' plan must contain at least one step")
    if len(plan.steps) > max_steps:
        raise PlanRejected(f"plan has {len(plan.steps)} steps; the maximum is {max_steps}")

    by_id: dict[int, PlanStep] = {}
    normalized: list[PlanStep] = []

    for expected_id, step in enumerate(plan.steps, start=1):
        if step.id != expected_id:
            raise PlanRejected(f"step ids must be 1..n in order; got {step.id} at position {expected_id}")

        tool = registry.get(step.tool)
        if tool is None:
            raise PlanRejected(f"step {step.id} uses unregistered tool {step.tool!r}")
        if not tool.permitted(ctx):
            raise PlanRejected(f"step {step.id}: the actor is not permitted to use {step.tool!r}")

        if len(set(step.depends_on)) != len(step.depends_on) or any(
            d not in by_id for d in step.depends_on
        ):
            raise PlanRejected(f"step {step.id} depends_on must list earlier steps only")

        schema = tool.input_model.model_json_schema()
        allowed = set(schema.get("properties", {}))
        required = set(schema.get("required", []))
        unknown = set(step.args) - allowed
        if unknown:
            raise PlanRejected(f"step {step.id}: unknown argument(s) for {step.tool}: {sorted(unknown)}")

        ref_steps: set[int] = set()
        for name, value in step.args.items():
            if isinstance(value, dict):
                if not refs.well_formed(value):
                    raise PlanRejected(f"step {step.id}: argument {name!r} must be a literal or a well-formed $from reference")
                body = value[refs.REF_KEY]
                dep = by_id.get(body["step"])
                if dep is None or body["step"] not in step.depends_on:
                    raise PlanRejected(f"step {step.id}: {name!r} references step {body['step']}, which is not a dependency")
                dep_tool = registry.get(dep.tool)
                if "list" in body:
                    item_fields = _item_fields(dep_tool.output_model, body["list"])
                    if item_fields is None or body["field"] not in item_fields:
                        raise PlanRejected(
                            f"step {step.id}: {dep.tool} output has no list {body['list']!r} with field {body['field']!r}"
                        )
                elif body["field"] not in dep_tool.output_model.model_fields:
                    raise PlanRejected(f"step {step.id}: {dep.tool} output has no field {body['field']!r}")
                ref_steps.add(body["step"])
            elif isinstance(value, list):
                raise PlanRejected(f"step {step.id}: list arguments are not supported in Phase 1")

        for name in tool.reference_only_args:
            if name in step.args and not refs.is_ref(step.args[name]):
                raise PlanRejected(
                    f"step {step.id}: {step.tool} argument {name!r} must be a $from reference to a read step, "
                    "never a typed value"
                )

        missing = required - set(step.args)
        if missing:
            raise PlanRejected(f"step {step.id}: missing required argument(s) for {step.tool}: {sorted(missing)}")

        if tool.is_write:
            # Read and verify before writing: a write must stand on read
            # steps it explicitly depends on, never on model-typed values
            # alone and never on another write.
            if not step.depends_on:
                raise PlanRejected(f"step {step.id}: a write step must depend on the read steps that establish its inputs")
            for dep_id in step.depends_on:
                if registry.get(by_id[dep_id].tool).is_write:
                    raise PlanRejected(f"step {step.id}: a write step may not depend on another write")
            if not ref_steps:
                raise PlanRejected(f"step {step.id}: a write step's identifying arguments must be $from references, not literals")

        normalized_step = step.model_copy(update={"irreversible": step.irreversible or tool.irreversible})
        by_id[step.id] = normalized_step
        normalized.append(normalized_step)

    return Plan(
        status="ok",
        steps=normalized,
        change_from_previous=plan.change_from_previous,
        reason=plan.reason,
    )
