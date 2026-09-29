"""Metrics over agent_* tables."""

from app.agent import states
from app.agent.metrics import compute_metrics
from tests.agent.support import dangerous_plan, dangerous_registry, slice_script, verifier_fails


def _metrics(world):
    with world.db.cursor() as cur:
        m = compute_metrics(cur, 1)
    world.db.commit()
    return m


def test_empty_metrics_have_no_rates(world):
    m = _metrics(world)
    assert m["tasks_total"] == 0 and m["task_success_rate"] is None and m["tokens_per_task"] is None


def test_mixed_outcomes(world):
    world.appointment(world.patient("Ravi Kumar"))
    orch, _ = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    assert orch.submit(world.ctx(), "check in Ravi")["state"] == states.COMPLETED
    bad = slice_script(day=world.day)
    bad["planner"] = ["nonsense"]
    orch2, _ = world.orchestrator(bad, repeat_last=True)
    assert orch2.submit(world.ctx(), "check in Ravi")["state"] == states.ESCALATED

    m = _metrics(world)
    assert m["tasks_total"] == 2 and m["task_success_rate"] == 0.5 and m["escalation_rate"] == 0.5


def test_replan_tool_error_and_override_rates(world):
    world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["verifier"] = [verifier_fails]
    orch, _ = world.orchestrator(script, repeat_last=True)
    orch.submit(world.ctx(), "check in Ravi")
    m = _metrics(world)
    assert m["replan_rate"] == 1.0 and m["verification_failure_rate"] == 1.0 and m["first_pass_verification_rate"] == 0.0

    executions = []
    script2 = slice_script(day=world.day)
    script2["planner"] = [dangerous_plan(day=world.day)]
    orch2, _ = world.orchestrator(script2, registry=dangerous_registry(executions), repeat_last=True)
    view = orch2.submit(world.ctx(), "do it")
    orch2.approve(view["task_id"], world.ctx("ADMIN"), approve=False)
    m = _metrics(world)
    assert m["human_override_rate"] == 1.0 and m["approval_rate"] == 0.0
