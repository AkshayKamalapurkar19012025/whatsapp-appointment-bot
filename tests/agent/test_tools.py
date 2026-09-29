"""Tool registry invariants and each read tool against the real services."""

import pytest
from pydantic import BaseModel, ConfigDict

from app.agent.tools.hospital import build_default_registry
from app.agent.tools.registry import CheckResult, Precheck, Tool, ToolError, ToolRegistry
from tests.agent.support import ctx


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int = 0


def _tool(**over):
    base = dict(name="t.x", description="d", operation="read", risk="low", requires_approval=False, irreversible=False,
                idempotency_required=False, required_permissions=("p",), input_model=_M, output_model=_M,
                handler=lambda c, x, a: {"x": 1})
    base.update(over)
    return Tool(**base)


def _write(**over):
    base = dict(operation="write", idempotency_required=True, precheck=lambda c, x, a: Precheck("ok"),
                postchecks=lambda c, x, a, o: [CheckResult("ok", True)])
    base.update(over)
    return _tool(**base)


def test_registry_lists_the_phase_1_tools_and_omits_the_ones_that_would_bypass_business_rules():
    names = build_default_registry().names()
    assert names == sorted(["appointment.record_payment", "appointment.waive_consultation_fee",
                            "appointment.settle_free_visit", "patient.search", "patient.get", "appointment.search", "appointment.get", "doctor.get",
                            "department.get", "doctor_schedule.get", "queue.get", "encounter.get", "invoice.get",
                            "appointment.check_in"])
    assert "encounter.create" not in names and "queue.generate_token" not in names


def test_every_tool_declares_the_prompts_registry_fields():
    for tool in build_default_registry().describe_for(ctx(permissions=(
            "patient.read", "appointment.read", "appointment.check_in", "directory.read", "queue.read",
            "encounter.read", "invoice.read"))):
        assert {"name", "description", "operation", "risk", "requires_approval", "input_schema",
                "output_schema"} <= set(tool)
        assert tool["input_schema"]["additionalProperties"] is False


def test_writes_are_check_in_plus_three_approval_gated_financial_tools():
    reg = build_default_registry()
    writes = [n for n in reg.names() if reg.get(n).is_write]
    assert writes == ["appointment.check_in", "appointment.record_payment", "appointment.settle_free_visit",
                      "appointment.waive_consultation_fee"]
    t = reg.get("appointment.check_in")
    assert (t.operation, t.risk, t.requires_approval, t.idempotency_required) == ("write", "medium", False, True)
    for name in writes[1:]:
        t = reg.get(name)
        assert (t.operation, t.risk, t.requires_approval, t.irreversible, t.idempotency_required) == (
            "financial", "high", True, True, True), name


@pytest.mark.parametrize("tool,msg", [
    (_tool(operation="write", idempotency_required=False), "idempotency"),
    (_write(risk="high", requires_approval=False), "approval"),
    (_write(operation="financial", risk="medium"), "high"),
    (_tool(irreversible=True), "read tool"),
    (_write(risk="high", requires_approval=True), "what the approver is shown"),
    (_write(reference_only_args=("nope",)), "unknown field"),
    (_tool(operation="write", idempotency_required=True, postchecks=lambda *a: []), "precheck"),
    (_tool(operation="write", idempotency_required=True, precheck=lambda *a: Precheck("ok")), "postchecks"),
])
def test_registration_refuses_unsafe_tool_definitions(tool, msg):
    with pytest.raises(ValueError, match=msg):
        ToolRegistry().register(tool)


def test_duplicate_registration_is_refused():
    r = ToolRegistry()
    r.register(_tool())
    with pytest.raises(ValueError, match="already registered"):
        r.register(_tool())


def test_read_tools_return_the_expected_shapes(world):
    ravi = world.patient("Ravi Kumar")
    appt = world.appointment(ravi, hour=10, minute=30)
    full = ctx(actor_id=1, permissions=("patient.read", "appointment.read", "directory.read", "queue.read",
                                        "encounter.read", "invoice.read", "appointment.check_in"))
    reg = build_default_registry()
    seeded = world.seeded

    with world.db.cursor() as cur:
        found = reg.get("appointment.search").execute(cur, full, {"date": world.day.isoformat(), "time": "10:30"})
        assert found["count"] == 1 and found["appointments"][0] == {
            "id": appt, "patient_id": ravi["id"], "patient_name": "Ravi Kumar", "doctor_id": seeded["doctor_id"],
            "doctor_name": "Dr. Agent", "appointment_type_name": "Agent Visit", "date": world.day.isoformat(),
            "time": "10:30", "status": "CONFIRMED", "token_number": None, "payment_status": found["appointments"][0]["payment_status"]}
        assert reg.get("appointment.search").execute(cur, full, {"date": world.day.isoformat(), "time": "10:00"})["count"] == 0
        assert reg.get("appointment.get").execute(cur, full, {"appointment_id": appt})["status"] == "CONFIRMED"
        assert reg.get("doctor.get").execute(cur, full, {"doctor_id": seeded["doctor_id"]})["name"] == "Dr. Agent"
        assert reg.get("department.get").execute(cur, full, {"department_id": seeded["department_id"]})["name"] == "Agent Dept"
        sched = reg.get("doctor_schedule.get").execute(cur, full, {"doctor_id": seeded["doctor_id"]})
        assert len(sched["entries"]) == 5 and sched["entries"][0]["start_time"] == "09:00"
        enc = reg.get("encounter.get").execute(cur, full, {"appointment_id": appt})
        assert enc["encounter_status"] and enc["patient_name"] == "Ravi Kumar" and "patient_date_of_birth" not in enc
        inv = reg.get("invoice.get").execute(cur, full, {"appointment_id": appt})
        assert set(inv) == {"appointment_id", "invoice_number", "consultation_fee", "extra_charges_total",
                            "total_due", "line_items"}
        q = reg.get("queue.get").execute(cur, full, {"doctor_id": seeded["doctor_id"]})
        assert q["now_serving"] is None and q["waiting"] == [] and q["completed_count"] == 0


def test_queue_shows_nobody_before_payment_and_the_agents_check_in_does_not_issue_a_token(world):
    """check-in leaves token_number NULL (Phase 4 of the arrival workflow): the queue stays empty."""
    ravi = world.patient("Ravi Kumar")
    appt = world.appointment(ravi)
    full = ctx(permissions=("appointment.check_in", "appointment.read", "queue.read", "patient.read"))
    reg = build_default_registry()
    with world.db.cursor() as cur:
        out = reg.get("appointment.check_in").execute(cur, full, {"appointment_id": appt})
        assert out["status"] == "CHECKED_IN" and out["token_number"] is None
        q = reg.get("queue.get").execute(cur, full, {"doctor_id": world.seeded["doctor_id"]})
    world.db.commit()
    assert q["now_serving"] is None and q["waiting"] == []


def test_check_in_via_the_tool_matches_the_route_exactly(world, client):
    """The tool and POST /appointments/{id}/visit share one implementation: same state, same notifications."""
    a = world.appointment(world.patient("Ravi Kumar"), hour=10, minute=30)
    b = world.appointment(world.patient("Meera Shah"), hour=11, minute=0)
    full = ctx(permissions=("appointment.check_in", "appointment.read"))
    with world.db.cursor() as cur:
        build_default_registry().get("appointment.check_in").execute(cur, full, {"appointment_id": a})
    world.db.commit()
    assert client.post(f"/api/appointments/{b}/visit", headers=world.admin_headers).status_code == 200
    rows = world.rows("SELECT status, token_number, visited_at IS NOT NULL FROM appointments WHERE id IN (%s, %s) ORDER BY id",
                      (a, b))
    assert rows == [("CHECKED_IN", None, True)] * 2
    assert world.rows("SELECT count(*) FROM mock_sms_outbox WHERE kind = 'CHECK_IN'")[0][0] == 2
    assert world.rows("SELECT count(*) FROM notifications WHERE kind = 'PATIENT_ARRIVED'")[0][0] == 2


def test_check_in_refuses_a_non_confirmed_appointment_with_a_typed_error(world):
    appt = world.appointment(world.patient("Ravi Kumar"), confirm=False)
    full = ctx(permissions=("appointment.check_in", "appointment.read"))
    with world.db.cursor() as cur:
        with pytest.raises(ToolError, match="invalid_status"):
            build_default_registry().get("appointment.check_in").execute(cur, full, {"appointment_id": appt})
    world.db.rollback()
    assert world.status(appt) == "PENDING"


def test_search_results_are_capped_and_flagged(world):
    for i in range(22):
        world.patient(f"Ravi Number{i:02d}")
    out = None
    with world.db.cursor() as cur:
        out = build_default_registry().get("patient.search").execute(cur, ctx(permissions=("patient.read",)), {"query": "Ravi"})
    assert out["count"] == 20 and out["truncated"] is True
