"""The first end-to-end vertical slice: "Check in today's 10:30 appointment
for Ravi." Real database, real existing services, scripted model."""

from app.agent import states
from tests.agent.support import slice_script


def test_check_in_slice_completes_and_is_fully_reconstructable(world):
    ravi = world.patient("Ravi Kumar")
    appt = world.appointment(ravi, hour=10, minute=30)
    world.appointment(world.patient("Meera Shah"), hour=11, minute=0)  # a bystander
    ctx = world.ctx("RECEPTIONIST")
    orch, llm = world.orchestrator(slice_script(day=world.day), repeat_last=True)

    view = orch.submit(ctx, "Check in today's 10:30 appointment for Ravi")

    assert view["state"] == states.COMPLETED, view
    assert world.status(appt) == "CHECKED_IN"
    assert view["result"]["already_done"] is False

    # the existing OPD flow's side effects happened exactly once (patient + staff notification)
    assert world.rows("SELECT count(*) FROM mock_sms_outbox WHERE kind = 'CHECK_IN'")[0][0] == 1
    assert world.rows("SELECT count(*) FROM notifications WHERE kind = 'PATIENT_ARRIVED'")[0][0] == 1
    # the ordinary audit_log records the agent-initiated write, attributed to the human
    audit = world.rows("SELECT staff_id, action, resource_type, resource_id FROM audit_log WHERE action LIKE 'agent.%'")
    assert audit == [(ctx.actor_id, "agent.appointment.check_in", "appointment", appt)]

    # reconstruction: every stage is recorded
    task_id = view["task_id"]
    assert world.rows("SELECT initiated_by, raw_input FROM agent_tasks WHERE id = %s", (task_id,)) == [
        (ctx.actor_id, "Check in today's 10:30 appointment for Ravi")]
    assert world.rows("SELECT task_spec->>'task_type', auth_context->>'actor_role' FROM agent_tasks WHERE id = %s",
                      (task_id,)) == [("appointment_check_in", "RECEPTIONIST")]
    assert world.rows("SELECT count(*) FROM agent_plans WHERE task_id = %s", (task_id,))[0][0] == 1
    calls = world.rows("SELECT tool, status, args, raw_response FROM agent_tool_calls WHERE task_id = %s ORDER BY id",
                       (task_id,))
    assert [c[0] for c in calls] == ["patient.search", "appointment.search", "appointment.check_in"]
    assert all(c[1] == "success" for c in calls)
    # the ids were resolved by the orchestrator from recorded output, not typed by a model
    assert calls[1][2]["patient_id"] == ravi["id"]
    assert calls[2][2]["appointment_id"] == appt
    assert calls[2][3]["status"] == "CHECKED_IN"
    kinds = world.rows("SELECT kind, verdict FROM agent_verifications WHERE task_id = %s", (task_id,))
    assert ("deterministic", "pass") in kinds and ("llm", "pass") in kinds
    transitions = [e[0] for e in world.rows(
        "SELECT to_state FROM agent_audit_events WHERE task_id = %s AND event = 'state_changed' ORDER BY seq", (task_id,))]
    assert transitions[:6] == ["INTAKE", "INTAKE_VALIDATED", "AUTHORIZATION_CHECK", "PLANNING", "PLANNED", "EXECUTING"]
    assert transitions[-1] == "COMPLETED"
    # every model call is logged with its prompt version
    model_calls = world.rows("SELECT details->>'agent', details->>'prompt_version' FROM agent_audit_events "
                             "WHERE task_id = %s AND event = 'model_call'", (task_id,))
    assert {a for a, _ in model_calls} == {"intake", "planner", "executor", "verifier"}
    assert all(v and v.startswith(a) for a, v in model_calls)


def test_bystander_is_untouched(world):
    ravi = world.patient("Ravi Kumar")
    world.appointment(ravi, hour=10, minute=30)
    other = world.appointment(world.patient("Meera Shah"), hour=11, minute=0)
    orch, _ = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    orch.submit(world.ctx(), "Check in today's 10:30 appointment for Ravi")
    assert world.status(other) == "CONFIRMED"
