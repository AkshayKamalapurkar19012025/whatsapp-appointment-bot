"""Orchestrator: success, needs_input, blocked, approval, tool failure,
verification failure, retry, replan, uncertain, human escalation."""

import pytest

from app.agent import states
from app.agent.llm import FakeLLM, LLMUnavailable
from app.agent.models import AuthContext
from tests.agent.support import (
    dangerous_plan, dangerous_registry, executor_proceeds, failing_handler, intake_ok, slice_plan, slice_script,
    swap_tool, verifier_fails, verifier_passes,
)


def _submit(world, script=None, *, ctx=None, registry=None, config=None, text="Check in today's 10:30 appointment for Ravi",
            repeat_last=True, key=None):
    orch, llm = world.orchestrator(script or slice_script(day=world.day), registry=registry, config=config,
                                   repeat_last=repeat_last)
    view = orch.submit(ctx or world.ctx(), text, idempotency_key=key)
    return orch, llm, view


def _calls(world, task_id):
    return world.rows("SELECT tool, status, attempt FROM agent_tool_calls WHERE task_id = %s ORDER BY id", (task_id,))


def _events(world, task_id, event):
    return world.rows("SELECT details FROM agent_audit_events WHERE task_id = %s AND event = %s ORDER BY seq",
                      (task_id, event))


# ---- needs_input: identity is never guessed ------------------------------

def test_two_matching_patients_need_input_and_nothing_is_written(world):
    world.appointment(world.patient("Ravi Kumar"))
    world.patient("Ravi Sharma")
    _, _, view = _submit(world)
    assert view["state"] == states.NEEDS_INPUT
    assert len(view["result"]["candidates"]) == 2
    assert {c["name"] for c in view["result"]["candidates"]} == {"Ravi Kumar", "Ravi Sharma"}
    assert [c[0] for c in _calls(world, view["task_id"])] == ["patient.search"]
    assert world.rows("SELECT count(*) FROM audit_log WHERE action LIKE 'agent.%'")[0][0] == 0


def test_unknown_patient_needs_input(world):
    _, _, view = _submit(world)
    assert view["state"] == states.NEEDS_INPUT and view["result"]["candidates"] == []


def test_no_appointment_at_that_time_needs_input(world):
    world.appointment(world.patient("Ravi Kumar"), hour=14, minute=0)
    _, _, view = _submit(world)
    assert view["state"] == states.NEEDS_INPUT and view["result"]["ambiguous_step"] == 2


def test_intake_missing_field_needs_input_and_never_plans(world):
    script = slice_script(day=world.day)
    script["intake"] = [{"status": "needs_input", "task_type": "appointment_check_in",
                         "missing_fields": ["appointment_time"],
                         "inputs": {"patient_reference": "Ravi", "appointment_date": world.day.isoformat()}}]
    _, llm, view = _submit(world, script)
    assert view["state"] == states.NEEDS_INPUT and view["result"] == {"missing_fields": ["appointment_time"]}
    assert not llm.calls_for("planner")


def test_out_of_scope(world):
    script = slice_script(day=world.day)
    script["intake"] = [{"status": "out_of_scope"}]
    _, llm, view = _submit(world, script, text="What's the weather?")
    assert view["state"] == states.OUT_OF_SCOPE and not llm.calls_for("planner")


# ---- blocked / unverifiable / unauthorized ------------------------------------

def test_pending_appointment_is_blocked_not_forced(world):
    appt = world.appointment(world.patient("Ravi Kumar"), confirm=False)
    _, _, view = _submit(world)
    assert view["state"] == states.BLOCKED and "PENDING" in view["reason"]
    assert world.status(appt) == "PENDING"


@pytest.mark.parametrize("status,expected", [("blocked", states.BLOCKED), ("unverifiable", states.UNVERIFIABLE),
                                             ("too_large", states.BLOCKED)])
def test_planner_refusals_end_the_task_with_the_reason(world, status, expected):
    script = slice_script(day=world.day)
    script["planner"] = [{"status": status, "steps": [], "reason": "cannot be done with the listed tools"}]
    _, llm, view = _submit(world, script)
    assert view["state"] == expected and "cannot be done" in view["reason"]
    assert not llm.calls_for("executor")


def test_actor_without_the_task_permission_is_unauthorized_before_any_planning(world):
    _, llm, view = _submit(world, ctx=world.ctx("BILLING"))
    assert view["state"] == states.UNAUTHORIZED and "appointment.check_in" in view["reason"]
    assert not llm.calls_for("planner")


# ---- already done -----------------------------------------------------------

def test_already_checked_in_is_detected_and_not_repeated(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    _submit(world)  # first run checks in
    _, _, view = _submit(world, key=None)
    assert view["state"] == states.COMPLETED and view["result"]["already_done"] is True
    assert world.status(appt) == "CHECKED_IN"
    assert world.rows("SELECT count(*) FROM mock_sms_outbox WHERE kind = 'CHECK_IN'")[0][0] == 1
    assert world.rows("SELECT count(*) FROM audit_log WHERE action = 'agent.appointment.check_in'")[0][0] == 1
    assert [c[0] for c in _calls(world, view["task_id"])] == ["patient.search", "appointment.search"]
    assert _events(world, view["task_id"], "step_skipped_already_done")


# ---- approval ------------------------------------------------------------------

def _dangerous(world):
    executions = []
    script = slice_script(day=world.day)
    script["planner"] = [dangerous_plan(day=world.day)]
    return executions, script, dangerous_registry(executions)


def test_high_risk_step_waits_for_approval_and_does_not_execute(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    executions, script, registry = _dangerous(world)
    _, _, view = _submit(world, script, registry=registry)
    assert view["state"] == states.APPROVAL_REQUIRED
    assert view["pending_approval"] == {"step": 3, "tool": "test.dangerous_write", "args": {"appointment_id": appt},
                                        "preview": {"action": "test-only dangerous write", "appointment": appt}}
    assert executions == []
    assert world.rows("SELECT token_hash, decision FROM agent_approvals") == [(None, None)]


def test_a_different_human_approves_and_the_step_runs_once(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    executions, script, registry = _dangerous(world)
    orch, llm, view = _submit(world, script, registry=registry)
    admin = world.ctx("ADMIN")

    done = orch.approve(view["task_id"], admin)

    assert done["state"] == states.COMPLETED and executions == [appt]
    (row,) = world.rows("SELECT decision, approver_staff_id, consumed_at IS NOT NULL, token_hash FROM agent_approvals")
    assert row[0] == "approved" and row[1] == admin.actor_id and row[2] is True and len(row[3]) == 64
    # the executor was told approval was granted only for the approved step
    assert [c["payload"]["approval_granted"] for c in llm.calls_for("executor")] == [False, False, True]
    assert world.rows("SELECT count(*) FROM audit_log WHERE action = 'agent.test.dangerous_write'")[0][0] == 1


def test_rejection_cancels_the_task_and_the_step_never_runs(world):
    world.appointment(world.patient("Ravi Kumar"))
    executions, script, registry = _dangerous(world)
    orch, _, view = _submit(world, script, registry=registry)
    done = orch.approve(view["task_id"], world.ctx("ADMIN"), approve=False)
    assert done["state"] == states.CANCELLED and executions == []


def test_cancelling_a_waiting_task(world):
    world.appointment(world.patient("Ravi Kumar"))
    executions, script, registry = _dangerous(world)
    ctx = world.ctx()
    orch, _, view = _submit(world, script, registry=registry, ctx=ctx)
    assert orch.cancel(view["task_id"], ctx)["state"] == states.CANCELLED and executions == []


# ---- tool failure --------------------------------------------------------------

def _flaky_search(world, *, fail_times, retryable):
    from app.agent.tools.hospital import build_default_registry

    counter: list = []
    registry = build_default_registry()
    inner = registry.get("patient.search").handler
    swap_tool(registry, "patient.search", handler=failing_handler(counter, fail_times=fail_times, retryable=retryable,
                                                                  inner=inner))
    return counter, registry


def test_transient_tool_error_is_retried_by_the_orchestrator_then_succeeds(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    counter, registry = _flaky_search(world, fail_times=1, retryable=True)
    _, _, view = _submit(world, registry=registry)
    assert view["state"] == states.COMPLETED and world.status(appt) == "CHECKED_IN"
    assert _calls(world, view["task_id"])[:2] == [("patient.search", "error", 1), ("patient.search", "success", 2)]
    assert _events(world, view["task_id"], "step_retry")


def test_persistent_tool_error_ends_in_tool_error_with_the_raw_error_recorded(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    counter, registry = _flaky_search(world, fail_times=99, retryable=True)
    _, _, view = _submit(world, registry=registry)
    assert view["state"] == states.TOOL_ERROR and len(counter) == 2  # first attempt + exactly one retry
    assert world.status(appt) == "CONFIRMED"
    assert "unavailable" in world.rows("SELECT error FROM agent_tool_calls WHERE task_id = %s ORDER BY id DESC LIMIT 1",
                                       (view["task_id"],))[0][0]


def test_non_retryable_tool_error_is_not_retried(world):
    world.appointment(world.patient("Ravi Kumar"))
    counter, registry = _flaky_search(world, fail_times=99, retryable=False)
    _, _, view = _submit(world, registry=registry)
    assert view["state"] == states.TOOL_ERROR and len(counter) == 1


# ---- verification: retry, replan, uncertain, escalation ------------------------------

def test_failed_step_verification_is_retried_with_the_verifiers_feedback(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["verifier"] = [
        lambda p: verifier_fails(p) | {"fix_hint": "the patient list looks incomplete"},
        verifier_passes,
    ]
    _, llm, view = _submit(world, script)
    assert view["state"] == states.COMPLETED and world.status(appt) == "CHECKED_IN"
    executor_payloads = [c["payload"] for c in llm.calls_for("executor")]
    assert "verifier_feedback" not in executor_payloads[0]
    assert executor_payloads[1]["verifier_feedback"] == "the patient list looks incomplete"
    assert [c[2] for c in _calls(world, view["task_id"])[:2]] == [1, 2]


def test_persistent_verification_failure_replans_once_then_fails_without_writing(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["planner"] = [slice_plan(day=world.day), slice_plan(day=world.day) | {"change_from_previous": "same"}]
    script["verifier"] = [verifier_fails]
    _, llm, view = _submit(world, script)
    assert view["state"] == states.VERIFICATION_FAILED
    assert len(llm.calls_for("planner")) == 2 and "previous_failure" in llm.calls_for("planner")[1]["payload"]
    assert world.status(appt) == "CONFIRMED"
    assert world.rows("SELECT replan_count FROM agent_tasks WHERE id = %s", (view["task_id"],)) == [(1,)]


def test_replan_after_failure_can_succeed_and_is_recorded(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    second = slice_plan(day=world.day) | {"change_from_previous": "search by full name instead"}
    second["steps"][0]["args"] = {"query": "Ravi Kumar"}
    script["planner"] = [slice_plan(day=world.day), second]
    # step 1 fails verification twice (attempt + retry), then everything passes
    script["verifier"] = [verifier_fails, verifier_fails, verifier_passes]
    _, llm, view = _submit(world, script)
    assert view["state"] == states.COMPLETED and world.status(appt) == "CHECKED_IN"
    assert world.rows("SELECT version, change_from_previous FROM agent_plans WHERE task_id = %s ORDER BY version",
                      (view["task_id"],)) == [(1, None), (2, "search by full name instead")]
    assert llm.calls_for("planner")[1]["payload"]["previous_failure"]["step"] == 1


def test_uncertain_verification_goes_to_a_human(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["verifier"] = [lambda p: verifier_passes(p) | {"verdict": "uncertain", "fix_hint": "ambiguous criterion"}]
    _, _, view = _submit(world, script)
    assert view["state"] == states.UNCERTAIN and world.status(appt) == "CONFIRMED"


def test_a_failed_verification_of_a_write_escalates_and_is_never_retried(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["verifier"] = [verifier_passes, verifier_passes, verifier_fails]
    _, _, view = _submit(world, script)
    assert view["state"] == states.ESCALATED and "write step failed verification" in view["reason"]
    assert world.status(appt) == "CHECKED_IN"  # the write happened; a human reconciles
    assert [c[0] for c in _calls(world, view["task_id"])].count("appointment.check_in") == 1


def test_final_signoff_failure_after_a_write_escalates(world):
    world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["verifier"] = [verifier_passes, verifier_passes, verifier_passes, verifier_fails]
    _, _, view = _submit(world, script)
    assert view["state"] == states.ESCALATED and "final verification" in view["reason"]


def test_deterministic_failure_beats_a_model_pass(world, monkeypatch):
    """The model passes, but the post-condition re-read says the status is wrong."""
    from app.agent.tools import hospital

    world.appointment(world.patient("Ravi Kumar"))
    real = hospital._get_appointment

    def lying_read(cur, ctx, appointment_id):
        item = real(cur, ctx, appointment_id)
        return item | {"status": "CONFIRMED"} if item and item["status"] == "CHECKED_IN" else item

    monkeypatch.setattr(hospital, "_get_appointment", lying_read)
    _, _, view = _submit(world)
    assert view["state"] == states.ESCALATED
    (verif,) = world.rows("SELECT verdict, criteria FROM agent_verifications WHERE task_id = %s AND kind = 'deterministic' "
                          "AND verdict = 'fail'", (view["task_id"],))
    assert any(c["name"] == "appointment_status_is_CHECKED_IN" and not c["passed"] for c in verif[1])


# ---- executor / model misbehavior -------------------------------------------------------

def test_executor_input_problem_stops_the_task(world):
    world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["executor"] = [{"action": "input_problem", "notes": "query is empty"}]
    _, _, view = _submit(world, script)
    assert view["state"] == states.INPUT_PROBLEM and "query is empty" in view["reason"]


def test_executor_naming_another_tool_is_escalated_and_nothing_runs(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["executor"] = [{"action": "call_tool", "tool": "appointment.check_in", "notes": ""}]
    _, _, view = _submit(world, script)
    assert view["state"] == states.ESCALATED and "deviated" in view["reason"]
    assert _calls(world, view["task_id"]) == [] and world.status(appt) == "CONFIRMED"


@pytest.mark.parametrize("agent", ["intake", "planner", "executor", "verifier"])
def test_malformed_model_output_escalates_and_is_logged(world, agent):
    world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script[agent] = ["I think you should check the patient in!"]
    _, _, view = _submit(world, script)
    assert view["state"] == states.ESCALATED and agent in view["reason"]
    (evt,) = _events(world, view["task_id"], "model_output_rejected")
    assert evt[0]["agent"] == agent and "I think" in evt[0]["raw_output"]


def test_model_outage_escalates(world):
    world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["planner"] = [LLMUnavailable("down")]
    _, _, view = _submit(world, script)
    assert view["state"] == states.ESCALATED and "unavailable" in view["reason"]


def test_a_crash_never_leaves_a_task_in_limbo(world):
    script = slice_script(day=world.day)
    script["planner"] = [RuntimeError("boom")]
    _, _, view = _submit(world, script)
    assert view["state"] == states.ESCALATED and "internal error" in view["reason"]


def test_an_invalid_plan_is_replanned_once_then_escalated(world):
    bad = slice_plan(day=world.day)
    bad["steps"][0]["tool"] = "database.run_sql"
    script = slice_script(day=world.day)
    script["planner"] = [bad, bad]
    _, llm, view = _submit(world, script)
    assert view["state"] == states.ESCALATED and len(llm.calls_for("planner")) == 2
    assert "unregistered tool" in llm.calls_for("planner")[1]["payload"]["previous_failure"]["reason"]


def test_an_invalid_plan_can_be_corrected_on_replan(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    bad = slice_plan(day=world.day)
    bad["steps"][0]["tool"] = "database.run_sql"
    script = slice_script(day=world.day)
    script["planner"] = [bad, slice_plan(day=world.day)]
    _, _, view = _submit(world, script)
    assert view["state"] == states.COMPLETED and world.status(appt) == "CHECKED_IN"


# ---- idempotency ---------------------------------------------------------------------------

def test_resubmitting_with_the_same_key_returns_the_original_task_without_rerunning(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    orch, llm, first = _submit(world, key="submit-1")
    calls_before = len(llm.calls)
    again = orch.submit(world.ctx(), "Check in today's 10:30 appointment for Ravi", idempotency_key="submit-1") \
        if False else None
    # same actor, same key
    ctx = AuthContext.model_validate(world.rows("SELECT auth_context FROM agent_tasks WHERE id = %s", (first["task_id"],))[0][0])
    again = orch.submit(ctx, "Check in today's 10:30 appointment for Ravi", idempotency_key="submit-1")
    assert again["task_id"] == first["task_id"] and again["state"] == states.COMPLETED
    assert len(llm.calls) == calls_before and world.status(appt) == "CHECKED_IN"
    assert world.rows("SELECT count(*) FROM agent_tasks")[0][0] == 1


def test_a_replayed_write_returns_the_recorded_response_and_does_not_touch_the_hospital_again(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    orch, _, view = _submit(world)
    run = orch._load_run(view["task_id"], 1)
    step = run.steps[2]
    tool = orch.registry.get(step.tool)
    parsed = tool.validate_args({"appointment_id": appt})

    outcome = orch._call_tool(run, step, tool, parsed, {"appointment_id": appt}, run.step_ids[3], 2, None)

    assert outcome["status"] == "success" and outcome["trace"]["replayed"] is True
    assert outcome["response"]["status"] == "CHECKED_IN"
    assert world.rows("SELECT count(*) FROM mock_sms_outbox WHERE kind = 'CHECK_IN'")[0][0] == 1
    assert world.rows("SELECT count(*) FROM agent_tool_calls WHERE tool = 'appointment.check_in'")[0][0] == 1
    assert _events(world, view["task_id"], "tool_call_replayed")


def test_the_database_itself_refuses_a_second_successful_call_for_one_step(world, db_connection):
    import psycopg

    world.appointment(world.patient("Ravi Kumar"))
    _, _, view = _submit(world)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db_connection.cursor() as cur:
            cur.execute("""INSERT INTO agent_tool_calls (task_id, step_id, attempt, tool, args, idempotency_key, status)
                           SELECT task_id, step_id, 9, tool, args, idempotency_key, 'success'
                           FROM agent_tool_calls WHERE tool = 'appointment.check_in'""")
    db_connection.rollback()


def test_two_different_users_cannot_share_an_idempotency_key(world):
    world.appointment(world.patient("Ravi Kumar"))
    orch, _, _ = _submit(world, key="shared")
    with pytest.raises(PermissionError):
        orch.submit(world.ctx(), "Check in today's 10:30 appointment for Ravi", idempotency_key="shared")
