"""Agent 1 -- Intake: one raw input -> one TaskSpec. Never plans, never
executes, never calls a hospital tool."""

from datetime import date

from app.agent import prompts
from app.agent.llm import LLMClient, LLMResponse
from app.agent.models import TaskSpec, parse_model_json
from app.agent.task_types import TASK_TYPES, describe_for_intake, risk_at_least

MAX_RAW_INPUT_CHARS = 2000
MAX_CRITERIA = 10


def run_intake(llm: LLMClient, raw_input: str, *, today: date, timezone: str) -> tuple[TaskSpec, LLMResponse]:
    payload = {
        "raw_input": raw_input[:MAX_RAW_INPUT_CHARS],
        "context": {"today": today.isoformat(), "timezone": timezone},
        "task_types": describe_for_intake(),
    }
    response = llm.complete(agent="intake", system=prompts.INTAKE, payload=payload)
    return parse_model_json(response.text, TaskSpec), response


def harden_spec(spec: TaskSpec) -> TaskSpec:
    """The model proposes; this decides. Whatever the model claimed:
      * an unknown task type is out of scope;
      * a required field that is absent or malformed makes it needs_input
        (the model can't fill in a default);
      * only the required fields are kept in `inputs`;
      * risk can't fall below the type's floor;
      * the type's mandatory acceptance criteria are always present.
    """
    if spec.status == "out_of_scope":
        return spec

    task_type = TASK_TYPES.get(spec.task_type)
    if task_type is None:
        return TaskSpec(status="out_of_scope", goal=spec.goal)

    inputs = {k: v for k, v in spec.inputs.items() if k in task_type.required_fields}
    missing = [
        name for name in task_type.required_fields
        if name not in inputs or not task_type.validators[name](inputs[name])
    ]

    if spec.status == "needs_input" or missing:
        return TaskSpec(
            status="needs_input",
            task_type=task_type.name,
            goal=spec.goal or task_type.description,
            inputs={k: v for k, v in inputs.items() if k not in missing},
            missing_fields=missing or list(dict.fromkeys(spec.missing_fields)),
            risk_tier=risk_at_least(spec.risk_tier, task_type.risk_floor),
        )

    criteria = []
    for c in [*spec.acceptance_criteria, *task_type.mandatory_criteria]:
        c = c.strip()
        if c and c not in criteria:
            criteria.append(c)

    return TaskSpec(
        status="ok",
        task_type=task_type.name,
        goal=spec.goal.strip() or task_type.description,
        inputs=inputs,
        missing_fields=[],
        acceptance_criteria=criteria[: MAX_CRITERIA + len(task_type.mandatory_criteria)],
        risk_tier=risk_at_least(spec.risk_tier, task_type.risk_floor),
        deadline=spec.deadline,
    )
