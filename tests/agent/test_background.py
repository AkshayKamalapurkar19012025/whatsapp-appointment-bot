"""Phase 3: background execution -- the job queue, the worker, crash
recovery, cancellation, and the API's 202 flow."""

import threading
import time

import pytest

from app.agent import states, store
from app.agent.llm import FakeLLM
from app.agent.orchestrator import Orchestrator
from app.agent.tools.hospital import build_default_registry
from app.agent.worker import AgentWorker, WorkerPool
from app.api.agent import get_orchestrator
from app.main import app
from tests.agent.support import (
    dangerous_plan, dangerous_registry, executor_proceeds, slice_script, verifier_passes,
)
from tests.helpers import create_staff_and_get_headers


class _Crash(BaseException):
    """Simulates the process dying mid-task (not an Exception: nothing in the orchestrator may swallow it)."""


def _crash(payload):
    raise _Crash()


def _worker(orch, name="test-worker"):
    return AgentWorker(orch, name=name)


def _job(world, task_id):
    return world.rows("SELECT kind, status, attempts, last_error FROM agent_jobs WHERE task_id = %s ORDER BY id", (task_id,))


def _expire_leases(world):
    world.rows("UPDATE agent_jobs SET lease_expires_at = NOW() - INTERVAL '1 minute' WHERE status = 'running'")


def _state(world, task_id):
    return world.rows("SELECT state FROM agent_tasks WHERE id = %s", (task_id,))[0][0]


# ---- the happy path -----------------------------------------------------------------------

def test_submit_async_returns_immediately_and_a_worker_finishes_the_task(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    orch, llm = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    ctx = world.ctx()

    view = orch.submit_async(ctx, "Check in today's 10:30 appointment for Ravi")

    assert view["state"] == states.RECEIVED and view["job"]["status"] == "queued"
    assert llm.calls == [] and world.status(appt) == "CONFIRMED"     # nothing ran in the request

    worker = _worker(orch)
    assert worker.run_once() is True
    assert _state(world, view["task_id"]) == states.COMPLETED and world.status(appt) == "CHECKED_IN"
    assert _job(world, view["task_id"]) == [("start", "done", 1, None)]
    assert worker.run_once() is False                                 # queue is empty


def test_a_repeated_submission_queues_nothing_new(world):
    world.appointment(world.patient("Ravi Kumar"))
    orch, _ = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    ctx = world.ctx()
    a = orch.submit_async(ctx, "check in Ravi", idempotency_key="k")
    b = orch.submit_async(ctx, "check in Ravi", idempotency_key="k")
    assert a["task_id"] == b["task_id"]
    assert world.rows("SELECT count(*) FROM agent_jobs")[0][0] == 1


# ---- approvals in the background ---------------------------------------------------------------

def _script(world):
    script = slice_script(day=world.day)
    script["planner"] = [dangerous_plan(day=world.day)]
    return script


def _payment_orch(world):
    """An approval-gated task using the test-only high-risk tool (independent of billing).
    Returns (appointment_id, orchestrator, executions) -- executions records real runs of the gated step."""
    appt = world.appointment(world.patient("Ravi Kumar"))
    executions: list = []
    orch, _ = world.orchestrator(_script(world), registry=dangerous_registry(executions), repeat_last=True)
    return appt, orch, executions


def test_approval_is_recorded_now_and_the_step_runs_in_the_worker(world):
    appt, orch, executions = _payment_orch(world)
    view = orch.submit_async(world.ctx("RECEPTIONIST"), "do the gated thing")
    worker = _worker(orch)
    worker.drain()
    assert _state(world, view["task_id"]) == states.APPROVAL_REQUIRED and executions == []

    approved = orch.approve_async(view["task_id"], world.ctx("ADMIN"))

    assert approved["state"] == states.APPROVED and approved["job"]["status"] == "queued"
    assert executions == []                                          # the request itself ran nothing
    worker.drain()
    assert _state(world, view["task_id"]) == states.COMPLETED and executions == [appt]
    assert [j[0:2] for j in _job(world, view["task_id"])] == [("start", "done"), ("resume", "done")]


def test_a_second_approval_or_the_initiator_cannot_release_it_again(world):
    appt, orch, executions = _payment_orch(world)
    initiator = world.ctx("ADMIN")
    view = orch.submit_async(initiator, "do the gated thing")
    _worker(orch).drain()
    with pytest.raises(store.ApprovalError, match="cannot approve"):
        orch.approve_async(view["task_id"], initiator)
    assert world.rows("SELECT count(*) FROM agent_jobs WHERE kind = 'resume'")[0][0] == 0
    orch.approve_async(view["task_id"], world.ctx("ADMIN"))
    with pytest.raises(store.ApprovalError):
        orch.approve_async(view["task_id"], world.ctx("ADMIN"))
    assert world.rows("SELECT count(*) FROM agent_jobs WHERE kind = 'resume'")[0][0] == 1
    assert executions == []


def test_rejecting_in_background_mode_cancels_at_once_and_queues_nothing(world):
    appt, orch, executions = _payment_orch(world)
    view = orch.submit_async(world.ctx(), "do the gated thing")
    _worker(orch).drain()
    assert orch.approve_async(view["task_id"], world.ctx("ADMIN"), approve=False)["state"] == states.CANCELLED
    assert world.rows("SELECT count(*) FROM agent_jobs WHERE kind = 'resume'")[0][0] == 0
    assert executions == []


def test_no_secret_is_stored_for_a_background_resume(world):
    appt, orch, executions = _payment_orch(world)
    view = orch.submit_async(world.ctx(), "do the gated thing")
    _worker(orch).drain()
    orch.approve_async(view["task_id"], world.ctx("ADMIN"))
    cols = {r[0] for r in world.rows("SELECT column_name FROM information_schema.columns WHERE table_name = 'agent_jobs'")}
    assert not ({"token", "approval_token", "payload", "secret"} & cols)
    # the approval is still bound and single-use even though no bearer token was presented
    _worker(orch).drain()
    (row,) = world.rows("SELECT decision, consumed_at IS NOT NULL FROM agent_approvals")
    assert row == ("approved", True) and executions == [appt]


def test_a_forged_decision_in_the_database_cannot_run_a_worker_step(world):
    """The worker re-verifies the approval: an unapproved, expired or argument-mismatched one is not honored."""
    appt, orch, executions = _payment_orch(world)
    view = orch.submit_async(world.ctx(), "do the gated thing")
    _worker(orch).drain()
    orch.approve_async(view["task_id"], world.ctx("ADMIN"))
    world.rows("UPDATE agent_approvals SET args_hash = %s", ("0" * 64,))    # tampered
    _worker(orch).drain()
    assert _state(world, view["task_id"]) == states.ESCALATED and executions == []


def test_approve_async_backs_off_while_the_start_job_is_still_finishing(world, monkeypatch):
    appt, orch, _ = _payment_orch(world)
    view = orch.submit_async(world.ctx(), "do the gated thing")
    _worker(orch).drain()
    # simulate the parked task's own start job still being 'running'
    world.rows("UPDATE agent_jobs SET status = 'running', lease_expires_at = NOW() + INTERVAL '5 minutes'")
    monkeypatch.setattr("app.agent.orchestrator._time.sleep", lambda s: None)
    with pytest.raises(store.ApprovalError, match="still being processed"):
        orch.approve_async(view["task_id"], world.ctx("ADMIN"))
    # the failed attempt rolled back completely: the task is still waiting, undecided
    assert _state(world, view["task_id"]) == states.APPROVAL_REQUIRED
    assert world.rows("SELECT decision FROM agent_approvals") == [(None,)]


# ---- crash recovery ------------------------------------------------------------------------------

def _crashing_script(world, *, executor=None, verifier=None):
    script = slice_script(day=world.day)
    script["executor"] = executor or [executor_proceeds]
    script["verifier"] = verifier or [verifier_passes]
    return script


def _recover(world, task_id, script=None):
    """A fresh worker (a restarted process) picks up the abandoned job."""
    _expire_leases(world)
    orch, llm = world.orchestrator(script or slice_script(day=world.day), repeat_last=True)
    _worker(orch, "recovery-worker").drain()
    return llm


def test_a_worker_that_dies_before_starting_the_task_is_replaced_seamlessly(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    orch, _ = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    view = orch.submit_async(world.ctx(), "check in Ravi")
    # a worker claims the job and dies before doing anything
    with world.db.cursor() as cur:
        store.claim_job(cur, "doomed")
    world.db.commit()
    _recover(world, view["task_id"])
    assert _state(world, view["task_id"]) == states.COMPLETED and world.status(appt) == "CHECKED_IN"
    assert _job(world, view["task_id"])[0][1:3] == ("done", 2)


def test_a_crash_mid_plan_resumes_from_the_recorded_state_without_redoing_finished_steps(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    orch, _ = world.orchestrator(_crashing_script(world, executor=[executor_proceeds, executor_proceeds, _crash]),
                                 repeat_last=True)
    view = orch.submit_async(world.ctx(), "check in Ravi")
    with pytest.raises(_Crash):
        _worker(orch).run_once()
    assert _state(world, view["task_id"]) == states.EXECUTING and world.status(appt) == "CONFIRMED"

    llm = _recover(world, view["task_id"])

    assert _state(world, view["task_id"]) == states.COMPLETED and world.status(appt) == "CHECKED_IN"
    assert llm.calls_for("intake") == [] and llm.calls_for("planner") == []      # no re-planning
    assert [c for c in world.rows("SELECT tool FROM agent_tool_calls ORDER BY id")].count(("patient.search",)) == 1


def test_a_crash_after_the_write_never_repeats_it(world):
    """The nastiest window: the hospital write committed, the process died before verification."""
    appt = world.appointment(world.patient("Ravi Kumar"))
    orch, _ = world.orchestrator(_crashing_script(world, verifier=[verifier_passes, verifier_passes, _crash]),
                                 repeat_last=True)
    view = orch.submit_async(world.ctx(), "check in Ravi")
    with pytest.raises(_Crash):
        _worker(orch).run_once()
    assert world.status(appt) == "CHECKED_IN" and _state(world, view["task_id"]) == states.VERIFYING

    _recover(world, view["task_id"])

    assert _state(world, view["task_id"]) == states.COMPLETED
    assert world.rows("SELECT count(*) FROM audit_log WHERE action = 'agent.appointment.check_in'")[0][0] == 1
    assert world.rows("SELECT count(*) FROM mock_sms_outbox WHERE kind = 'CHECK_IN'")[0][0] == 1
    assert world.rows("SELECT count(*) FROM agent_tool_calls WHERE tool = 'appointment.check_in'")[0][0] == 1


def test_a_crash_between_approval_and_the_write_honors_the_approval_once(world):
    appt, orch, executions = _payment_orch(world)
    view = orch.submit_async(world.ctx(), "do the gated thing")
    _worker(orch).drain()
    orch.approve_async(view["task_id"], world.ctx("ADMIN"))
    # the resume job is claimed, the task moves to EXECUTING, then the process dies at the executor call
    crashing = _script(world) | {"executor": [_crash]}
    orch2, _ = world.orchestrator(crashing, registry=dangerous_registry(executions), repeat_last=True)
    with pytest.raises(_Crash):
        _worker(orch2).run_once()
    assert executions == [] and _state(world, view["task_id"]) == states.EXECUTING

    _expire_leases(world)
    orch3, _ = world.orchestrator(_script(world), registry=dangerous_registry(executions), repeat_last=True)
    _worker(orch3, "recovery-worker").drain()

    assert _state(world, view["task_id"]) == states.COMPLETED      # no second approval was requested
    assert executions == [appt]
    assert world.rows("SELECT count(*) FROM agent_approvals")[0][0] == 1


def test_a_crash_before_a_plan_exists_escalates_because_a_human_should_resubmit(world):
    world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)
    script["planner"] = [_crash]
    orch, _ = world.orchestrator(script, repeat_last=True)
    view = orch.submit_async(world.ctx(), "check in Ravi")
    with pytest.raises(_Crash):
        _worker(orch).run_once()
    assert _state(world, view["task_id"]) == states.PLANNING
    _recover(world, view["task_id"])
    assert _state(world, view["task_id"]) == states.ESCALATED
    assert "resubmit" in world.rows("SELECT final_reason FROM agent_tasks")[0][0]


# ---- job failure, cancellation, inactive initiator -------------------------------------------------

def test_an_exception_escaping_the_orchestrator_requeues_with_backoff_then_escalates(world, monkeypatch):
    world.appointment(world.patient("Ravi Kumar"))
    orch, _ = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    view = orch.submit_async(world.ctx(), "check in Ravi")

    def boom(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(orch, "process_job", boom)
    worker = _worker(orch)
    assert worker.run_once() is True
    (job,) = _job(world, view["task_id"])
    assert job[1:3] == ("queued", 1) and "database went away" in job[3]
    assert worker.run_once() is False                       # not due yet (backoff)
    for expected_attempts in (2, 3):
        world.rows("UPDATE agent_jobs SET run_after = NOW() - INTERVAL '1 second'")
        assert worker.run_once() is True
    (job,) = _job(world, view["task_id"])
    assert job[1] == "failed" and job[2] == 3
    assert _state(world, view["task_id"]) == states.ESCALATED
    assert "background job failed" in world.rows("SELECT final_reason FROM agent_tasks")[0][0]


def test_cancelling_a_queued_task_means_the_worker_does_nothing(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    orch, llm = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    ctx = world.ctx()
    view = orch.submit_async(ctx, "check in Ravi")
    assert orch.cancel(view["task_id"], ctx)["state"] == states.CANCELLED
    _worker(orch).drain()
    assert llm.calls == [] and world.status(appt) == "CONFIRMED"
    assert _job(world, view["task_id"])[0][1] == "done"


def test_a_deactivated_initiator_is_not_run(world):
    world.appointment(world.patient("Ravi Kumar"))
    orch, llm = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    ctx = world.ctx()
    view = orch.submit_async(ctx, "check in Ravi")
    world.rows("UPDATE staff SET active = FALSE WHERE id = %s", (ctx.actor_id,))
    _worker(orch).drain()
    assert _state(world, view["task_id"]) == states.ESCALATED and llm.calls == []


# ---- the queue itself ---------------------------------------------------------------------------------

def test_one_live_job_per_task_and_leases_gate_reclaiming(world):
    orch, _ = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    view = orch.submit_async(world.ctx(), "check in Ravi")
    with world.db.cursor() as cur:
        assert store.enqueue_job(cur, view["task_id"], "start") is None          # already queued
        first = store.claim_job(cur, "w1")
        assert first["task_id"] == view["task_id"] and first["attempts"] == 1
        assert store.claim_job(cur, "w2") is None                                 # running, lease valid
        assert store.enqueue_job(cur, view["task_id"], "resume") is None          # still one live job
    world.db.commit()
    _expire_leases(world)
    with world.db.cursor() as cur:
        again = store.claim_job(cur, "w2")
    world.db.commit()
    assert again["id"] == first["id"] and again["attempts"] == 2


def test_concurrent_workers_never_claim_the_same_job(world):
    orch, _ = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    for i in range(6):
        orch.submit_async(world.ctx(), f"task {i}")
    claimed, barrier = [], threading.Barrier(6)

    def claim(name):
        from app.db.connection import get_connection

        barrier.wait()
        with get_connection() as conn:
            with conn.cursor() as cur:
                job = store.claim_job(cur, name)
        claimed.append(job["id"] if job else None)

    threads = [threading.Thread(target=claim, args=(f"w{i}",)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert None not in claimed and len(set(claimed)) == 6


def test_a_worker_pool_thread_runs_tasks_end_to_end(world):
    appt = world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)

    def make():
        return Orchestrator(FakeLLM(script=script, repeat_last=True), build_default_registry(),
                            today_fn=lambda: world.day)

    orch, _ = world.orchestrator(script, repeat_last=True)
    view = orch.submit_async(world.ctx(), "check in Ravi")
    pool = WorkerPool(make, threads=2, poll_seconds=0.05)
    pool.start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline and _state(world, view["task_id"]) != states.COMPLETED:
            time.sleep(0.1)
    finally:
        pool.stop()
    assert _state(world, view["task_id"]) == states.COMPLETED and world.status(appt) == "CHECKED_IN"
    assert world.rows("SELECT count(*) FROM audit_log WHERE action = 'agent.appointment.check_in'")[0][0] == 1


def test_the_worker_pool_is_off_without_a_provider_or_in_inline_mode(monkeypatch):
    from app import config
    from app.agent.worker import build_worker_pool

    monkeypatch.setattr(config, "AGENT_LLM_PROVIDER", "")
    assert build_worker_pool() is None
    monkeypatch.setattr(config, "AGENT_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "AGENT_EXECUTION_MODE", "inline")
    assert build_worker_pool() is None
    monkeypatch.setattr(config, "AGENT_EXECUTION_MODE", "background")
    monkeypatch.setattr(config, "AGENT_WORKER_THREADS", 0)
    assert build_worker_pool() is None


# ---- API -----------------------------------------------------------------------------------------------

@pytest.fixture
def background_api(world):
    holder = {}

    def install(script, registry=None):
        orch = Orchestrator(FakeLLM(script=script, repeat_last=True), registry or build_default_registry(),
                            today_fn=lambda: world.day)
        orch.execution_mode = "background"
        holder["orch"] = orch
        app.dependency_overrides[get_orchestrator] = lambda: orch
        return orch

    yield install
    app.dependency_overrides.pop(get_orchestrator, None)


def test_the_api_answers_202_queued_then_completes_after_a_worker_runs(client, db_connection, world, background_api):
    appt = world.appointment(world.patient("Ravi Kumar"))
    orch = background_api(slice_script(day=world.day))
    headers = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")

    resp = client.post("/api/agent/tasks", json={"input": "check in Ravi"}, headers=headers)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["state"] == states.RECEIVED and body["job"]["status"] == "queued"
    assert world.status(appt) == "CONFIRMED"

    _worker(orch).drain()
    trace = client.get(f"/api/agent/tasks/{body['task_id']}", headers=headers).json()
    assert trace["task"]["state"] == "COMPLETED" and trace["jobs"][0]["status"] == "done"
    assert world.status(appt) == "CHECKED_IN"


def test_the_approval_endpoint_queues_the_step_in_background_mode(client, db_connection, world, background_api):
    appt = world.appointment(world.patient("Ravi Kumar"))
    executions: list = []
    script = slice_script(day=world.day)
    script["planner"] = [dangerous_plan(day=world.day)]
    orch = background_api(script, dangerous_registry(executions))
    receptionist = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")
    admin = create_staff_and_get_headers(db_connection, role="ADMIN")

    task_id = client.post("/api/agent/tasks", json={"input": "do the gated thing"}, headers=receptionist).json()["task_id"]
    _worker(orch).drain()
    waiting = client.get(f"/api/agent/tasks/{task_id}", headers=receptionist).json()
    assert waiting["task"]["state"] == "APPROVAL_REQUIRED"

    approved = client.post(f"/api/agent/tasks/{task_id}/approve", headers=admin)
    assert approved.status_code == 200 and approved.json()["state"] == "APPROVED"
    assert executions == []
    _worker(orch).drain()
    assert executions == [appt]


def test_metrics_report_the_queue(client, db_connection, world, background_api):
    orch = background_api(slice_script(day=world.day))
    world.appointment(world.patient("Ravi Kumar"))
    headers = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")
    client.post("/api/agent/tasks", json={"input": "check in Ravi"}, headers=headers)
    m = client.get("/api/agent/metrics", headers=world.admin_headers).json()
    assert m["jobs_queued"] == 1 and m["average_queue_wait_s"] is None and m["job_failure_rate"] is None
    _worker(orch).drain()
    m = client.get("/api/agent/metrics", headers=world.admin_headers).json()
    assert m["jobs_queued"] == 0 and m["average_queue_wait_s"] >= 0 and m["job_failure_rate"] == 0.0


# ---- lease heartbeat and takeover ------------------------------------------------------------------

def test_a_long_running_job_keeps_its_lease_and_cannot_be_claimed_by_another_worker(world, monkeypatch):
    world.appointment(world.patient("Ravi Kumar"))
    orch, _ = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    view = orch.submit_async(world.ctx(), "check in Ravi")
    started, release = threading.Event(), threading.Event()

    def slow(task_id, *, kind):
        started.set()
        release.wait(10)
        return "slow_done"

    monkeypatch.setattr(orch, "process_job", slow)
    worker = AgentWorker(orch, name="slow-worker", lease_seconds=2)      # heartbeat every ~0.67s
    t = threading.Thread(target=worker.run_once)
    t.start()
    assert started.wait(5)
    time.sleep(3)                                                        # longer than the whole lease
    with world.db.cursor() as cur:
        assert store.claim_job(cur, "other-worker") is None              # heartbeat kept the lease alive
    world.db.commit()
    release.set()
    t.join(10)
    assert _job(world, view["task_id"])[0][1] == "done"


def test_a_worker_whose_job_was_taken_over_neither_closes_it_nor_keeps_beating(world):
    orch, _ = world.orchestrator(slice_script(day=world.day), repeat_last=True)
    view = orch.submit_async(world.ctx(), "check in Ravi")
    with world.db.cursor() as cur:
        job = store.claim_job(cur, "old-owner", lease_seconds=60)
    world.db.commit()
    _expire_leases(world)
    with world.db.cursor() as cur:
        assert store.claim_job(cur, "new-owner")["id"] == job["id"]      # taken over
        assert store.extend_lease(cur, job["id"], "old-owner") is False   # the old owner can't renew
        assert store.finish_job(cur, job["id"], status="done", worker_id="old-owner") is False
        assert store.finish_job(cur, job["id"], status="done", worker_id="new-owner") is True
    world.db.commit()
    from app.agent.worker import _Heartbeat
    from app.db.connection import get_connection

    world.rows("UPDATE agent_jobs SET status = 'running', locked_by = 'new-owner', finished_at = NULL")
    with _Heartbeat(get_connection, job["id"], "old-owner", 60, 0.05) as beat:
        time.sleep(0.3)
    assert beat.lost is True


def test_a_worker_that_lost_the_task_to_someone_else_stands_down_instead_of_escalating(world):
    """Another actor moves the task while this worker is mid-step: the worker must not clobber it."""
    world.appointment(world.patient("Ravi Kumar"))
    script = slice_script(day=world.day)

    def meanwhile_someone_else_takes_over(payload):
        world.rows("UPDATE agent_tasks SET state = 'STEP_VERIFIED' WHERE state = 'EXECUTING'")  # a takeover moved it
        return {"action": "call_tool", "tool": payload["step"]["tool"], "notes": ""}

    script["executor"] = [meanwhile_someone_else_takes_over]
    orch, _ = world.orchestrator(script, repeat_last=True)
    view = orch.submit_async(world.ctx(), "check in Ravi")
    _worker(orch).drain()
    assert _state(world, view["task_id"]) != states.ESCALATED
    assert world.rows("SELECT final_reason FROM agent_tasks")[0][0] is None
