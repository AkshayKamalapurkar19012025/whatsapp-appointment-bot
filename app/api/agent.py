"""
AI Agent Automation Layer API (app/agent). Every endpoint authenticates
the human through the existing staff session and builds the task's
AuthContext server-side; nothing about identity or permissions is taken
from the request body. The orchestrator runs synchronously in the request
in Phase 1 (one short task) -- see the audit's risk #8.

  POST /agent/tasks                  agent.task.create   submit a task
  GET  /agent/tasks/{id}             agent.task.read     status + full trace
  POST /agent/tasks/{id}/approve     agent.task.approve  a DIFFERENT person approves a pending write
  POST /agent/tasks/{id}/reject      agent.task.approve  reject it (cancels the task)
  POST /agent/tasks/{id}/cancel      initiator/approver  stop a task
  GET  /agent/metrics                staff.manage        aggregate metrics
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.agent import metrics, store
from app.agent.authz import build_auth_context
from app.agent.llm import LLMUnavailable, UnavailableLLM, build_default_llm
from app.agent.orchestrator import Orchestrator
from app.agent.tools.hospital import build_default_registry
from app.api.staff_auth import get_current_staff, require_permission
from app.db.connection import get_connection
from app.logging_config import request_id_var
from app.services.staff_permissions import staff_has_permission

router = APIRouter(prefix="/agent", tags=["AI Agent"])


class TaskCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1, max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)
    # Optional narrowing of which patients the task may touch (e.g. a task
    # started from one patient's chart). Can only narrow, never widen.
    patient_ids: list[int] | None = Field(default=None, max_length=50)


def get_orchestrator() -> Orchestrator:
    """Overridable in tests (app.dependency_overrides). Without
    AGENT_LLM_PROVIDER the orchestrator gets an UnavailableLLM: cancel and
    reject still work, task creation answers 503."""
    try:
        llm = build_default_llm()
    except LLMUnavailable:
        llm = UnavailableLLM()
    return Orchestrator(llm, build_default_registry())


def _context(staff: dict, patient_ids: list[int] | None = None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return build_auth_context(cur, staff, patient_ids=patient_ids, session_id=request_id_var.get())


@router.post("/tasks")
def create_task(
    body: TaskCreateBody,
    staff: dict = Depends(require_permission("agent.task.create")),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    if isinstance(orchestrator.llm, UnavailableLLM):
        raise HTTPException(status_code=503, detail="The AI agent layer is not enabled")
    ctx = _context(staff, body.patient_ids)
    try:
        return orchestrator.submit(ctx, body.input, idempotency_key=body.idempotency_key)
    except PermissionError:
        raise HTTPException(status_code=409, detail="That idempotency key belongs to another user's task")


@router.get("/tasks/{task_id}")
def get_task(task_id: int, staff: dict = Depends(require_permission("agent.task.read"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            trace = store.get_task_trace(cur, task_id, hospital_id=staff["hospital_id"])
            # Everyone with agent.task.read sees their own tasks; seeing
            # others' (which include raw input) is an admin-tier privilege.
            if trace is not None and trace["task"]["initiated_by"] != staff["id"] and not staff_has_permission(
                cur, staff["id"], "staff.manage"
            ):
                trace = None
    if trace is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return trace


def _decide(task_id: int, staff: dict, orchestrator: Orchestrator, approve: bool):
    approver = _context(staff)
    try:
        return orchestrator.approve(task_id, approver, approve=approve)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    except store.ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/tasks/{task_id}/approve")
def approve_task(
    task_id: int,
    staff: dict = Depends(require_permission("agent.task.approve")),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    return _decide(task_id, staff, orchestrator, True)


@router.post("/tasks/{task_id}/reject")
def reject_task(
    task_id: int,
    staff: dict = Depends(require_permission("agent.task.approve")),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    return _decide(task_id, staff, orchestrator, False)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: int,
    staff: dict = Depends(get_current_staff),
    orchestrator: Orchestrator = Depends(get_orchestrator),
):
    try:
        return orchestrator.cancel(task_id, _context(staff))
    except PermissionError:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    except (store.ApprovalError, store.InvalidTransition) as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/metrics")
def get_metrics(staff: dict = Depends(require_permission("staff.manage"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return metrics.compute_metrics(cur, staff["hospital_id"])
