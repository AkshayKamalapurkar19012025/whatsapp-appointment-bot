"""Agent 3 -- Executor: exactly ONE planned step.

The model here only decides "proceed or the inputs look wrong". It cannot
choose a tool, alter arguments, retry, or report an outcome: the
orchestrator calls the planned tool with the planned (already-resolved)
arguments and captures the raw trace itself, so there is nothing for the
model to fabricate."""

from app.agent import prompts
from app.agent.llm import LLMClient, LLMResponse
from app.agent.models import ExecutorDecision, PlanStep, parse_model_json


class ExecutorViolation(Exception):
    """The Executor tried to do something other than its one step."""


def run_executor(
    llm: LLMClient,
    step: PlanStep,
    resolved_args: dict,
    dependency_outputs: dict[int, dict],
    *,
    approval_granted: bool,
    verifier_feedback: str | None = None,
) -> tuple[ExecutorDecision, LLMResponse]:
    payload = {
        "step": {**step.model_dump(), "args": resolved_args},
        "dependency_outputs": {str(k): v for k, v in dependency_outputs.items()},
        "approval_granted": approval_granted,
    }
    if verifier_feedback:
        payload["verifier_feedback"] = verifier_feedback
    response = llm.complete(agent="executor", system=prompts.EXECUTOR, payload=payload)
    decision = parse_model_json(response.text, ExecutorDecision)

    if decision.action == "call_tool" and decision.tool != step.tool:
        raise ExecutorViolation(
            f"executor named tool {decision.tool!r} but the step's tool is {step.tool!r}"
        )
    return decision, response
