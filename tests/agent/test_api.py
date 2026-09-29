"""HTTP surface: authentication, server-built authorization context, task
visibility, approval endpoints, metrics."""

import pytest

from app.agent import states
from app.agent.llm import FakeLLM
from app.agent.orchestrator import Orchestrator
from app.agent.tools.hospital import build_default_registry
from app.api.agent import get_orchestrator
from app.main import app
from tests.agent.support import dangerous_plan, dangerous_registry, slice_script
from tests.helpers import create_staff_and_get_headers


@pytest.fixture
def wired(world):
    """Override the orchestrator dependency with a scripted one; restores afterwards."""
    holder = {}

    def install(script, registry=None):
        llm = FakeLLM(script=script, repeat_last=True)
        orch = Orchestrator(llm, registry or build_default_registry(), today_fn=lambda: world.day)
        holder["llm"] = llm
        app.dependency_overrides[get_orchestrator] = lambda: orch
        return orch

    yield install
    app.dependency_overrides.pop(get_orchestrator, None)


def test_requires_authentication(client):
    assert client.post("/api/agent/tasks", json={"input": "x"}).status_code == 401
    assert client.get("/api/agent/tasks/1").status_code == 401


def test_task_creation_needs_the_agent_permission(client, db_connection, world, wired):
    wired(slice_script(day=world.day))
    for role in ("LAB_TECH", "PHARMACIST"):
        headers = create_staff_and_get_headers(db_connection, role=role)
        assert client.post("/api/agent/tasks", json={"input": "check in Ravi"}, headers=headers).status_code == 403


def test_not_enabled_by_default_returns_503(client, world):
    app.dependency_overrides.pop(get_orchestrator, None)
    resp = client.post("/api/agent/tasks", json={"input": "check in Ravi"}, headers=world.admin_headers)
    assert resp.status_code == 503


def test_end_to_end_over_http_uses_the_callers_own_identity(client, db_connection, world, wired):
    wired(slice_script(day=world.day))
    appt = world.appointment(world.patient("Ravi Kumar"))
    headers = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")

    resp = client.post("/api/agent/tasks", json={"input": "Check in today's 10:30 appointment for Ravi",
                                                 "idempotency_key": "k1"}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == states.COMPLETED and world.status(appt) == "CHECKED_IN"

    trace = client.get(f"/api/agent/tasks/{body['task_id']}", headers=headers).json()
    assert trace["task"]["state"] == "COMPLETED" and len(trace["tool_calls"]) == 3 and len(trace["events"]) > 10
    assert trace["task"]["auth_context"]["actor_role"] == "RECEPTIONIST"

    again = client.post("/api/agent/tasks", json={"input": "Check in today's 10:30 appointment for Ravi",
                                                  "idempotency_key": "k1"}, headers=headers).json()
    assert again["task_id"] == body["task_id"]


def test_the_request_body_cannot_smuggle_identity_or_permissions(client, world, wired):
    wired(slice_script(day=world.day))
    resp = client.post("/api/agent/tasks", json={"input": "x", "actor_role": "ADMIN", "permissions": ["*"]},
                       headers=create_staff_and_get_headers(world.db, role="BILLING"))
    assert resp.status_code == 422


def test_tasks_are_private_to_their_initiator_unless_admin(client, db_connection, world, wired):
    wired(slice_script(day=world.day))
    world.appointment(world.patient("Ravi Kumar"))
    owner = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")
    other = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")
    task_id = client.post("/api/agent/tasks", json={"input": "check in Ravi"}, headers=owner).json()["task_id"]
    assert client.get(f"/api/agent/tasks/{task_id}", headers=other).status_code == 404
    assert client.get(f"/api/agent/tasks/{task_id}", headers=owner).status_code == 200
    assert client.get(f"/api/agent/tasks/{task_id}", headers=world.admin_headers).status_code == 200


def test_approval_endpoints_enforce_permission_and_separation_of_duties(client, db_connection, world, wired):
    world.appointment(world.patient("Ravi Kumar"))
    executions = []
    script = slice_script(day=world.day)
    script["planner"] = [dangerous_plan(day=world.day)]
    wired(script, dangerous_registry(executions))
    receptionist = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")
    admin_a = create_staff_and_get_headers(db_connection, role="ADMIN")

    created = client.post("/api/agent/tasks", json={"input": "do the thing"}, headers=receptionist).json()
    assert created["state"] == states.APPROVAL_REQUIRED and created["pending_approval"]["tool"] == "test.dangerous_write"
    task_id = created["task_id"]

    assert client.post(f"/api/agent/tasks/{task_id}/approve", headers=receptionist).status_code == 403
    assert executions == []
    approved = client.post(f"/api/agent/tasks/{task_id}/approve", headers=admin_a)
    assert approved.status_code == 200 and approved.json()["state"] == states.COMPLETED and len(executions) == 1
    assert client.post(f"/api/agent/tasks/{task_id}/approve", headers=admin_a).status_code == 409


def test_an_admin_cannot_approve_a_task_they_started(client, world, wired):
    world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["planner"] = [dangerous_plan(day=world.day)]
    executions = []
    wired(script, dangerous_registry(executions))
    task = client.post("/api/agent/tasks", json={"input": "do the thing"}, headers=world.admin_headers).json()
    assert task["state"] == states.APPROVAL_REQUIRED
    assert client.post(f"/api/agent/tasks/{task['task_id']}/approve", headers=world.admin_headers).status_code == 409
    assert executions == []


def test_reject_and_cancel_endpoints(client, db_connection, world, wired):
    world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["planner"] = [dangerous_plan(day=world.day)]
    executions = []
    wired(script, dangerous_registry(executions))
    owner = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")
    stranger = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")
    task_id = client.post("/api/agent/tasks", json={"input": "do it"}, headers=owner).json()["task_id"]
    assert client.post(f"/api/agent/tasks/{task_id}/cancel", headers=stranger).status_code == 403
    assert client.post(f"/api/agent/tasks/{task_id}/reject", headers=world.admin_headers).json()["state"] == states.CANCELLED
    assert client.post(f"/api/agent/tasks/{task_id}/cancel", headers=owner).status_code == 409
    assert executions == []


def test_metrics_are_admin_only_and_reflect_runs(client, db_connection, world, wired):
    wired(slice_script(day=world.day))
    world.appointment(world.patient("Ravi Kumar"))
    receptionist = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")
    client.post("/api/agent/tasks", json={"input": "check in Ravi"}, headers=receptionist)
    assert client.get("/api/agent/metrics", headers=receptionist).status_code == 403
    m = client.get("/api/agent/metrics", headers=world.admin_headers).json()
    assert m["tasks_total"] == 1 and m["task_success_rate"] == 1.0 and m["escalation_rate"] == 0.0
    assert m["first_pass_verification_rate"] == 1.0 and m["average_steps_per_task"] == 3.0
    assert m["tool_error_rate"] == 0.0 and m["tokens_per_task"] > 0 and m["replan_rate"] == 0.0
