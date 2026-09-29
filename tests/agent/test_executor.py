"""Executor agent: exactly one step, no deviation. (Tool errors, missing
approval and retries-with-feedback run through the orchestrator; see
test_orchestrator.py.)"""

import pytest

from app.agent.agents.executor import ExecutorViolation, run_executor
from app.agent.llm import FakeLLM
from app.agent.models import ModelOutputError, PlanStep

STEP = PlanStep(id=3, action="check in", tool="appointment.check_in", args={"appointment_id": {"$from": {}}},
                success_check="CHECKED_IN", depends_on=[2])


def _run(response, **kw):
    llm = FakeLLM(script={"executor": [response]})
    out = run_executor(llm, STEP, {"appointment_id": 9}, {2: {"appointments": [{"id": 9}]}},
                       approval_granted=kw.pop("approval_granted", False), **kw)
    return out, llm


def test_correct_tool_invocation_is_accepted_and_sees_resolved_args():
    (decision, _), llm = _run({"action": "call_tool", "tool": "appointment.check_in"})
    assert decision.action == "call_tool"
    payload = llm.calls[0]["payload"]
    assert payload["step"]["args"] == {"appointment_id": 9} and payload["approval_granted"] is False
    assert payload["dependency_outputs"] == {"2": {"appointments": [{"id": 9}]}}


def test_a_different_tool_is_a_violation():
    with pytest.raises(ExecutorViolation):
        _run({"action": "call_tool", "tool": "invoice.get"})


def test_wrong_inputs_are_reported_as_input_problem_not_worked_around():
    (decision, _), _ = _run({"action": "input_problem", "notes": "appointment id is empty"})
    assert decision.action == "input_problem" and "empty" in decision.notes


def test_retry_passes_the_verifiers_reason_to_the_executor():
    _, llm = _run({"action": "call_tool", "tool": "appointment.check_in"}, verifier_feedback="date was wrong")
    assert llm.calls[0]["payload"]["verifier_feedback"] == "date was wrong"


def test_approval_flag_is_reported_only_when_the_system_says_so():
    _, llm = _run({"action": "call_tool", "tool": "appointment.check_in"}, approval_granted=True)
    assert llm.calls[0]["payload"]["approval_granted"] is True


@pytest.mark.parametrize("raw", [
    {"action": "add_step", "tool": "x"},                                   # cannot create steps
    {"action": "call_tool", "tool": "appointment.check_in", "args": {"appointment_id": 1}},  # cannot supply args
    {"status": "done", "output": {"status": "CHECKED_IN"}},                # cannot claim an outcome
    "call appointment.check_in",
])
def test_executor_cannot_add_steps_supply_arguments_or_claim_results(raw):
    with pytest.raises(ModelOutputError):
        _run(raw)
