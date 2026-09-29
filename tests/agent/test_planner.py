"""Planner agent: what validate_plan accepts and rejects."""

from datetime import date

import pytest

from app.agent.agents.planner import MAX_STEPS, PlanRejected, run_planner, validate_plan
from app.agent.llm import FakeLLM
from app.agent.models import ModelOutputError, Plan
from app.agent.tools.hospital import build_default_registry
from tests.agent.support import ctx, intake_ok, ref, slice_plan

DAY = date(2030, 1, 7)
REGISTRY = build_default_registry()
CTX = ctx()


def _plan(**over):
    return Plan.model_validate(slice_plan(day=DAY) | over)


def test_valid_plan():
    plan = validate_plan(_plan(), REGISTRY, CTX)
    assert plan.status == "ok" and [s.tool for s in plan.steps] == [
        "patient.search", "appointment.search", "appointment.check_in"]


def test_planner_is_shown_only_the_tools_the_actor_may_use():
    from app.agent.models import TaskSpec

    billing = ctx(actor_role="BILLING", permissions=("agent.task.create", "patient.read", "appointment.read", "invoice.read"))
    llm = FakeLLM(script={"planner": [slice_plan(day=DAY)]})
    run_planner(llm, TaskSpec(**intake_ok(day=DAY)), REGISTRY.describe_for(billing))
    shown = {t["name"] for t in llm.calls[0]["payload"]["available_tools"]}
    assert "appointment.check_in" not in shown and "patient.search" in shown
    assert {"name", "description", "operation", "risk", "requires_approval", "input_schema", "output_schema"} <= set(
        llm.calls[0]["payload"]["available_tools"][0])


def test_missing_capability_is_blocked_with_a_reason():
    plan = validate_plan(Plan(status="blocked", reason="needs a tool to send SMS"), REGISTRY, CTX)
    assert plan.status == "blocked" and plan.steps == [] and "SMS" in plan.reason


def test_unverifiable_and_too_large_pass_through_with_a_reason():
    for status in ("unverifiable", "too_large"):
        plan = validate_plan(Plan(status=status, steps=[]), REGISTRY, CTX)
        assert plan.status == status and plan.reason


def test_unregistered_tool_is_rejected():
    plan = _plan()
    plan.steps[0].tool = "database.run_sql"
    with pytest.raises(PlanRejected, match="unregistered tool"):
        validate_plan(plan, REGISTRY, CTX)


def test_tool_the_actor_may_not_use_is_rejected():
    no_checkin = ctx(permissions=("agent.task.create", "patient.read", "appointment.read"))
    with pytest.raises(PlanRejected, match="not permitted"):
        validate_plan(_plan(), REGISTRY, no_checkin)


def test_too_many_steps_is_rejected():
    steps = [
        {"id": i, "action": "look", "tool": "patient.search", "args": {"query": "Ravi"},
         "success_check": "found", "irreversible": False, "depends_on": []}
        for i in range(1, MAX_STEPS + 2)
    ]
    with pytest.raises(PlanRejected, match="maximum"):
        validate_plan(Plan(status="ok", steps=steps), REGISTRY, CTX)


def test_empty_ok_plan_is_rejected():
    with pytest.raises(PlanRejected):
        validate_plan(Plan(status="ok", steps=[]), REGISTRY, CTX)


def test_step_ids_must_be_sequential():
    plan = _plan()
    plan.steps[1].id = 7
    with pytest.raises(PlanRejected, match="ids"):
        validate_plan(plan, REGISTRY, CTX)


def test_dependencies_must_point_backwards_at_existing_steps():
    plan = _plan()
    plan.steps[0].depends_on = [2]
    with pytest.raises(PlanRejected, match="depends_on"):
        validate_plan(plan, REGISTRY, CTX)


def test_a_reference_must_name_a_declared_dependency():
    plan = _plan()
    plan.steps[1].depends_on = []
    with pytest.raises(PlanRejected, match="not a dependency"):
        validate_plan(plan, REGISTRY, CTX)


def test_a_reference_must_point_at_a_real_output_field():
    plan = _plan()
    plan.steps[1].args["patient_id"] = ref(1, "patients", "government_id")
    with pytest.raises(PlanRejected, match="government_id"):
        validate_plan(plan, REGISTRY, CTX)


def test_unknown_and_missing_arguments_are_rejected():
    plan = _plan()
    plan.steps[0].args = {"query": "Ravi", "sql": "select 1"}
    with pytest.raises(PlanRejected, match="unknown argument"):
        validate_plan(plan, REGISTRY, CTX)
    plan = _plan()
    plan.steps[2].args = {}
    with pytest.raises(PlanRejected, match="missing required"):
        validate_plan(plan, REGISTRY, CTX)


def test_write_step_with_no_verified_inputs_is_rejected():
    plan = _plan()
    plan.steps[2].depends_on = []
    plan.steps[2].args = {"appointment_id": 42}
    with pytest.raises(PlanRejected, match="must depend"):
        validate_plan(plan, REGISTRY, CTX)
    plan = _plan()
    plan.steps[2].depends_on = []  # reference to a step that is not declared as a dependency
    with pytest.raises(PlanRejected, match="not a dependency"):
        validate_plan(plan, REGISTRY, CTX)


def test_write_step_with_a_model_typed_id_is_rejected():
    plan = _plan()
    plan.steps[2].args = {"appointment_id": 42}  # a literal, not a $from reference
    with pytest.raises(PlanRejected, match=r"\$from"):
        validate_plan(plan, REGISTRY, CTX)


def test_write_step_may_not_depend_on_another_write():
    plan = _plan()
    plan.steps.append(plan.steps[2].model_copy(update={"id": 4, "depends_on": [3],
                                                       "args": {"appointment_id": ref(3, "appointments")}}))
    with pytest.raises(PlanRejected):
        validate_plan(plan, REGISTRY, CTX)


def test_irreversible_flag_is_normalized_up_never_down():
    from app.agent.tools.registry import Tool  # noqa: F401  (registry invariants tested elsewhere)

    plan = _plan()
    plan.steps[0].irreversible = True  # over-cautious: kept
    assert validate_plan(plan, REGISTRY, CTX).steps[0].irreversible is True


def test_malformed_planner_output_is_rejected():
    from app.agent.models import TaskSpec

    llm = FakeLLM(script={"planner": ['{"status": "ok", "steps": [{"id": 1}]}']})
    with pytest.raises(ModelOutputError):
        run_planner(llm, TaskSpec(**intake_ok(day=DAY)), [])


def test_replan_carries_the_previous_failure_and_lessons_are_capped():
    from app.agent.models import TaskSpec

    llm = FakeLLM(script={"planner": [slice_plan(day=DAY)]})
    run_planner(llm, TaskSpec(**intake_ok(day=DAY)), [], lessons=[f"l{i}" for i in range(9)],
                previous_failure={"step": 2, "verifier_fix_hint": "wrong date"})
    payload = llm.calls[0]["payload"]
    assert payload["previous_failure"]["step"] == 2 and len(payload["lessons"]) == 5
