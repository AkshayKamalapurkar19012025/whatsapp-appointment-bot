"""
Persistence for the agent_* tables (migrations/0060). Every function takes
an open cursor and runs inside the caller's transaction, like the rest of
app/services. This module (and metrics.py) is the ONLY place under
app/agent that executes SQL, and it only ever touches agent_* tables --
hospital data is reached exclusively through registered tools.

Reconstruction: get_task_trace() returns, for one task, who initiated it,
the raw input, the interpreted spec, the authorization context, every
plan version, each step with its tool and exact arguments, every raw tool
response, deterministic and LLM verifications, approvals, and the ordered
event log (state transitions, model calls, retries, replans, outcome).
"""

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app.agent import states
from app.agent.models import AuthContext

_TASK_COLUMNS = (
    "id", "hospital_id", "initiated_by", "state", "raw_input", "auth_context", "task_spec",
    "task_type", "risk_tier", "idempotency_key", "replan_count", "final_reason", "result",
    "created_at", "updated_at", "finished_at",
)


class InvalidTransition(Exception):
    pass


class ApprovalError(Exception):
    """An approval token/decision is invalid, expired, spent, forged, or
    belongs to another task/step/argument set."""


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def args_hash(args: dict) -> str:
    return hash_value(json.dumps(args, sort_keys=True, default=str))


def _row(row) -> dict:
    return dict(zip(_TASK_COLUMNS, row))


# ---------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------

def create_task(cur, ctx: AuthContext, raw_input: str, idempotency_key: str | None) -> tuple[int, bool]:
    """Returns (task_id, created). A repeated submission with the same
    (hospital, idempotency_key) returns the ORIGINAL task and created=False
    -- the caller must not run it again."""
    cur.execute(
        """
        INSERT INTO agent_tasks (hospital_id, initiated_by, raw_input, auth_context, idempotency_key)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (hospital_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        RETURNING id
        """,
        (ctx.facility_id, ctx.actor_id, raw_input, Jsonb(ctx.model_dump(mode="json")), idempotency_key),
    )
    row = cur.fetchone()
    if row is not None:
        add_event(cur, row[0], "task_received", actor_id=ctx.actor_id,
                  details={"idempotency_key": idempotency_key})
        return row[0], True

    cur.execute(
        "SELECT id, initiated_by FROM agent_tasks WHERE hospital_id = %s AND idempotency_key = %s",
        (ctx.facility_id, idempotency_key),
    )
    task_id, initiated_by = cur.fetchone()
    if initiated_by != ctx.actor_id:
        # Same key from a different user must not hand them someone else's task.
        raise PermissionError("idempotency key belongs to another user's task")
    return task_id, False


def get_task(cur, task_id: int, *, hospital_id: int, for_update: bool = False) -> dict | None:
    cur.execute(
        f"SELECT {', '.join(_TASK_COLUMNS)} FROM agent_tasks WHERE id = %s AND hospital_id = %s"
        + (" FOR UPDATE" if for_update else ""),
        (task_id, hospital_id),
    )
    row = cur.fetchone()
    return _row(row) if row else None


def get_task_by_id(cur, task_id: int) -> dict | None:
    """Tenant-unscoped lookup for the worker, which is handed a job (not a
    caller's hospital). Never used by request handlers."""
    cur.execute(f"SELECT {', '.join(_TASK_COLUMNS)} FROM agent_tasks WHERE id = %s", (task_id,))
    row = cur.fetchone()
    return _row(row) if row else None


def add_event(cur, task_id: int, event: str, *, from_state=None, to_state=None, actor_id=None, details=None) -> None:
    cur.execute("SELECT id FROM agent_tasks WHERE id = %s FOR UPDATE", (task_id,))
    cur.execute(
        """
        INSERT INTO agent_audit_events (task_id, seq, event, from_state, to_state, actor_id, details)
        VALUES (%s, (SELECT COALESCE(MAX(seq), 0) + 1 FROM agent_audit_events WHERE task_id = %s),
                %s, %s, %s, %s, %s)
        """,
        (task_id, task_id, event, from_state, to_state, actor_id,
         Jsonb(details, dumps=lambda o: json.dumps(o, default=str)) if details is not None else None),
    )


def transition(cur, task_id: int, from_state: str, to_state: str, *, reason: str | None = None,
               result: dict | None = None, actor_id: int | None = None) -> None:
    """Move a task between lifecycle states. Refuses illegal moves, and is
    optimistic: it only applies if the task is still in `from_state`."""
    if not states.can_transition(from_state, to_state):
        raise InvalidTransition(f"{from_state} -> {to_state} is not a legal transition")

    terminal = to_state in states.TERMINAL_STATES
    cur.execute(
        """
        UPDATE agent_tasks
        SET state = %s,
            updated_at = NOW(),
            final_reason = COALESCE(%s, final_reason),
            result = COALESCE(%s, result),
            finished_at = CASE WHEN %s THEN NOW() ELSE finished_at END
        WHERE id = %s AND state = %s
        """,
        (to_state, reason, Jsonb(result, dumps=lambda o: json.dumps(o, default=str)) if result is not None else None,
         terminal, task_id, from_state),
    )
    if cur.rowcount != 1:
        raise InvalidTransition(f"task {task_id} is no longer in state {from_state}")
    add_event(cur, task_id, "state_changed", from_state=from_state, to_state=to_state,
              actor_id=actor_id, details={"reason": reason} if reason else None)


def save_spec(cur, task_id: int, spec) -> None:
    cur.execute(
        "UPDATE agent_tasks SET task_spec = %s, task_type = %s, risk_tier = %s WHERE id = %s",
        (Jsonb(spec.model_dump(mode="json")), spec.task_type or None, spec.risk_tier, task_id),
    )


def bump_replan(cur, task_id: int) -> int:
    cur.execute("UPDATE agent_tasks SET replan_count = replan_count + 1 WHERE id = %s RETURNING replan_count", (task_id,))
    return cur.fetchone()[0]


# ---------------------------------------------------------------------
# Plans / steps
# ---------------------------------------------------------------------

def save_plan(cur, task_id: int, plan, prompt_version: str) -> tuple[int, int]:
    """Returns (plan_id, version)."""
    cur.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM agent_plans WHERE task_id = %s", (task_id,))
    version = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO agent_plans (task_id, version, status, plan, change_from_previous, prompt_version)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (task_id, version, plan.status, Jsonb(plan.model_dump(mode="json")), plan.change_from_previous, prompt_version),
    )
    plan_id = cur.fetchone()[0]
    for step in plan.steps:
        cur.execute(
            """
            INSERT INTO agent_steps (plan_id, task_id, step_no, action, tool, args, irreversible, depends_on)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (plan_id, task_id, step.id, step.action, step.tool, Jsonb(step.args), step.irreversible,
             Jsonb(step.depends_on)),
        )
    return plan_id, version


def current_plan(cur, task_id: int) -> dict | None:
    cur.execute(
        "SELECT id, version, plan FROM agent_plans WHERE task_id = %s ORDER BY version DESC LIMIT 1", (task_id,)
    )
    row = cur.fetchone()
    return {"id": row[0], "version": row[1], "plan": row[2]} if row else None


def list_steps(cur, plan_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT id, step_no, action, tool, args, irreversible, depends_on, state, attempts
        FROM agent_steps WHERE plan_id = %s ORDER BY step_no
        """,
        (plan_id,),
    )
    keys = ("id", "step_no", "action", "tool", "args", "irreversible", "depends_on", "state", "attempts")
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def set_step_state(cur, step_id: int, state: str, *, bump_attempt: bool = False) -> int:
    cur.execute(
        "UPDATE agent_steps SET state = %s, attempts = attempts + %s WHERE id = %s RETURNING attempts",
        (state, 1 if bump_attempt else 0, step_id),
    )
    return cur.fetchone()[0]


def step_state(cur, step_id: int) -> str:
    cur.execute("SELECT state FROM agent_steps WHERE id = %s", (step_id,))
    return cur.fetchone()[0]


def step_attempts(cur, step_id: int) -> int:
    cur.execute("SELECT attempts FROM agent_steps WHERE id = %s", (step_id,))
    return cur.fetchone()[0]


# ---------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------

def find_successful_call(cur, idempotency_key: str) -> dict | None:
    cur.execute(
        "SELECT id, raw_response FROM agent_tool_calls WHERE idempotency_key = %s AND status = 'success'",
        (idempotency_key,),
    )
    row = cur.fetchone()
    return {"id": row[0], "raw_response": row[1]} if row else None


def record_tool_call(cur, *, task_id: int, step_id: int, attempt: int, tool: str, args: dict,
                     idempotency_key: str, status: str, raw_response: dict | None, error: str | None,
                     duration_ms: int) -> int:
    cur.execute(
        """
        INSERT INTO agent_tool_calls
            (task_id, step_id, attempt, tool, args, idempotency_key, status, raw_response, error, duration_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (task_id, step_id, attempt, tool, Jsonb(args), idempotency_key, status,
         Jsonb(raw_response, dumps=lambda o: json.dumps(o, default=str)) if raw_response is not None else None,
         error, duration_ms),
    )
    return cur.fetchone()[0]


def successful_outputs(cur, task_id: int, plan_id: int) -> dict[int, dict]:
    """step_no -> raw output, for the CURRENT plan's steps that have a
    recorded successful call -- how a resumed task rebuilds its state."""
    cur.execute(
        """
        SELECT s.step_no, c.raw_response
        FROM agent_tool_calls c JOIN agent_steps s ON s.id = c.step_id
        WHERE s.plan_id = %s AND c.status = 'success'
        """,
        (plan_id,),
    )
    return {r[0]: r[1] for r in cur.fetchall()}


# ---------------------------------------------------------------------
# Verifications
# ---------------------------------------------------------------------

def add_verification(cur, *, task_id: int, step_id: int | None, kind: str, verdict: str,
                     criteria: list, detail: str | None = None) -> None:
    cur.execute(
        """
        INSERT INTO agent_verifications (task_id, step_id, kind, verdict, criteria, detail)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (task_id, step_id, kind, verdict, Jsonb(criteria, dumps=lambda o: json.dumps(o, default=str)), detail),
    )


def latest_deterministic_checks(cur, task_id: int, plan_id: int) -> list[dict]:
    """Deterministic checks of the CURRENT plan's steps (most recent per
    step) -- the evidence the final sign-off must respect."""
    cur.execute(
        """
        SELECT DISTINCT ON (v.step_id) v.criteria
        FROM agent_verifications v JOIN agent_steps s ON s.id = v.step_id
        WHERE v.task_id = %s AND s.plan_id = %s AND v.kind = 'deterministic'
        ORDER BY v.step_id, v.id DESC
        """,
        (task_id, plan_id),
    )
    checks: list[dict] = []
    for (criteria,) in cur.fetchall():
        checks.extend(criteria)
    return checks


# ---------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------

def request_approval(cur, task_id: int, step_id: int, args: dict, preview: dict | None = None) -> int:
    cur.execute(
        """
        INSERT INTO agent_approvals (task_id, step_id, args, args_hash, preview)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (step_id) DO UPDATE
            SET args = EXCLUDED.args, args_hash = EXCLUDED.args_hash, preview = EXCLUDED.preview
            WHERE agent_approvals.decision IS NULL
        RETURNING id
        """,
        (task_id, step_id, Jsonb(args), args_hash(args),
         Jsonb(preview, dumps=lambda o: json.dumps(o, default=str)) if preview is not None else None),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute("SELECT id FROM agent_approvals WHERE step_id = %s", (step_id,))
        row = cur.fetchone()
    return row[0]


def pending_approval(cur, task_id: int) -> dict | None:
    cur.execute(
        """
        SELECT a.id, a.step_id, s.tool, a.args, s.step_no, a.preview
        FROM agent_approvals a JOIN agent_steps s ON s.id = a.step_id
        WHERE a.task_id = %s AND a.decision IS NULL
        ORDER BY a.id DESC LIMIT 1
        """,
        (task_id,),
    )
    row = cur.fetchone()
    return ({"approval_id": row[0], "step_id": row[1], "tool": row[2], "args": row[3], "step_no": row[4],
             "preview": row[5]} if row else None)


def latest_approval(cur, task_id: int) -> dict | None:
    """The task's most recent approval request, decided or not."""
    cur.execute(
        """
        SELECT a.id, a.step_id, s.tool, a.args, s.step_no, a.preview
        FROM agent_approvals a JOIN agent_steps s ON s.id = a.step_id
        WHERE a.task_id = %s ORDER BY a.id DESC LIMIT 1
        """,
        (task_id,),
    )
    row = cur.fetchone()
    return ({"approval_id": row[0], "step_id": row[1], "tool": row[2], "args": row[3], "step_no": row[4],
             "preview": row[5]} if row else None)


def decide_approval(cur, approval_id: int, *, approver_staff_id: int, initiator_staff_id: int,
                    approve: bool, ttl_minutes: int) -> str | None:
    """Record a human decision. Returns the freshly minted approval token
    (only ever returned here, only stored hashed) for an approval, None for
    a rejection. The approver must not be the initiator: nobody approves
    their own agent's write."""
    if approver_staff_id == initiator_staff_id:
        raise ApprovalError("the person who started a task cannot approve it")

    token = secrets.token_urlsafe(32) if approve else None
    cur.execute(
        """
        UPDATE agent_approvals
        SET decision = %s, approver_staff_id = %s, decided_at = NOW(),
            token_hash = %s, expires_at = %s
        WHERE id = %s AND decision IS NULL
        """,
        ("approved" if approve else "rejected", approver_staff_id,
         hash_value(token) if token else None,
         datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes) if approve else None,
         approval_id),
    )
    if cur.rowcount != 1:
        raise ApprovalError("this approval was already decided")
    return token


def verify_approval(cur, *, task_id: int, step_id: int, args: dict, token: str | None, initiator_staff_id: int) -> int:
    """Check (without spending) that `token` is a valid, unexpired,
    unspent approval for exactly this task, step and argument set.

    token=None is the SERVER-INTERNAL form used by the background worker to
    continue a task whose approval a human already decided: every check
    below still applies (approved, unexpired, unspent, same arguments,
    approver != initiator) except the bearer-token comparison, since there
    is no caller to authenticate -- the worker reads the decision from this
    table itself. Public entry points (Orchestrator.resume) never accept
    an empty token."""
    cur.execute(
        """
        SELECT id, decision, token_hash, expires_at, consumed_at, args_hash, approver_staff_id
        FROM agent_approvals WHERE task_id = %s AND step_id = %s
        """,
        (task_id, step_id),
    )
    row = cur.fetchone()
    if row is None:
        raise ApprovalError("no approval was requested for this step")
    approval_id, decision, token_hash, expires_at, consumed_at, stored_args_hash, approver = row
    if decision != "approved" or token_hash is None:
        raise ApprovalError("this step has not been approved")
    if token is not None and not secrets.compare_digest(token_hash, hash_value(token)):
        raise ApprovalError("approval token does not match")
    if consumed_at is not None:
        raise ApprovalError("approval token was already used")
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        raise ApprovalError("approval token has expired")
    if approver == initiator_staff_id:
        raise ApprovalError("the person who started a task cannot approve it")
    if stored_args_hash != args_hash(args):
        raise ApprovalError("the arguments changed since the approval was granted")
    return approval_id


def decided_unspent_approval(cur, step_id: int) -> bool:
    """True if a human approved this step and the approval hasn't been spent."""
    cur.execute(
        "SELECT 1 FROM agent_approvals WHERE step_id = %s AND decision = 'approved' AND consumed_at IS NULL",
        (step_id,),
    )
    return cur.fetchone() is not None


def consume_approval(cur, approval_id: int) -> None:
    cur.execute(
        "UPDATE agent_approvals SET consumed_at = NOW() WHERE id = %s AND consumed_at IS NULL", (approval_id,)
    )
    if cur.rowcount != 1:
        raise ApprovalError("approval token was already used")


# ---------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------

def get_task_trace(cur, task_id: int, *, hospital_id: int) -> dict | None:
    task = get_task(cur, task_id, hospital_id=hospital_id)
    if task is None:
        return None

    def rows(sql, keys):
        cur.execute(sql, (task_id,))
        return [dict(zip(keys, r)) for r in cur.fetchall()]

    return {
        "task": task,
        "plans": rows("SELECT id, version, status, plan, change_from_previous, prompt_version, created_at "
                      "FROM agent_plans WHERE task_id = %s ORDER BY version",
                      ("id", "version", "status", "plan", "change_from_previous", "prompt_version", "created_at")),
        "steps": rows("SELECT id, plan_id, step_no, action, tool, args, irreversible, depends_on, state, attempts "
                      "FROM agent_steps WHERE task_id = %s ORDER BY plan_id, step_no",
                      ("id", "plan_id", "step_no", "action", "tool", "args", "irreversible", "depends_on",
                       "state", "attempts")),
        "tool_calls": rows("SELECT id, step_id, attempt, tool, args, idempotency_key, status, raw_response, error, "
                           "duration_ms, created_at FROM agent_tool_calls WHERE task_id = %s ORDER BY id",
                           ("id", "step_id", "attempt", "tool", "args", "idempotency_key", "status",
                            "raw_response", "error", "duration_ms", "created_at")),
        "approvals": rows("SELECT id, step_id, args, args_hash, preview, requested_at, decision, approver_staff_id, "
                          "decided_at, expires_at, consumed_at FROM agent_approvals WHERE task_id = %s ORDER BY id",
                          ("id", "step_id", "args", "args_hash", "preview", "requested_at", "decision", "approver_staff_id",
                           "decided_at", "expires_at", "consumed_at")),
        "verifications": rows("SELECT id, step_id, kind, verdict, criteria, detail, created_at "
                              "FROM agent_verifications WHERE task_id = %s ORDER BY id",
                              ("id", "step_id", "kind", "verdict", "criteria", "detail", "created_at")),
        "jobs": rows("SELECT id, kind, status, attempts, max_attempts, last_error, created_at, started_at, finished_at "
                     "FROM agent_jobs WHERE task_id = %s ORDER BY id",
                     ("id", "kind", "status", "attempts", "max_attempts", "last_error", "created_at", "started_at",
                      "finished_at")),
        "events": rows("SELECT seq, event, from_state, to_state, actor_id, details, created_at "
                       "FROM agent_audit_events WHERE task_id = %s ORDER BY seq",
                       ("seq", "event", "from_state", "to_state", "actor_id", "details", "created_at")),
    }


def plan_trace(cur, task_id: int, plan_id: int, plan_version: int) -> list[dict]:
    """The raw trace for the current plan, in execution order: every tool
    call (success and error) as captured by the orchestrator, plus any
    step skipped because a precheck found the write already in effect."""
    cur.execute(
        """
        SELECT c.id, s.step_no, c.tool, c.attempt, c.args, c.status, c.raw_response, c.error
        FROM agent_tool_calls c JOIN agent_steps s ON s.id = c.step_id
        WHERE s.plan_id = %s ORDER BY c.id
        """,
        (plan_id,),
    )
    trace = [
        {"kind": "tool_call", "call_id": r[0], "step": r[1], "tool": r[2], "attempt": r[3], "args": r[4],
         "status": r[5], "response": r[6], "error": r[7]}
        for r in cur.fetchall()
    ]
    cur.execute(
        """
        SELECT details FROM agent_audit_events
        WHERE task_id = %s AND event = 'step_skipped_already_done' AND (details->>'plan_version')::int = %s
        ORDER BY seq
        """,
        (task_id, plan_version),
    )
    trace.extend({"kind": "precheck_already_done", **r[0]} for r in cur.fetchall())
    return trace


# ---------------------------------------------------------------------
# Jobs (background execution)
# ---------------------------------------------------------------------

JOB_LEASE_MINUTES = 10


def enqueue_job(cur, task_id: int, kind: str) -> int | None:
    """Queue a job for the task. Returns None if the task already has a live
    (queued or running) job -- the one-active-job-per-task index."""
    cur.execute(
        """
        INSERT INTO agent_jobs (task_id, kind) VALUES (%s, %s)
        ON CONFLICT (task_id) WHERE status IN ('queued', 'running') DO NOTHING
        RETURNING id
        """,
        (task_id, kind),
    )
    row = cur.fetchone()
    if row is not None:
        add_event(cur, task_id, "job_enqueued", details={"job_id": row[0], "kind": kind})
    return row[0] if row else None


def claim_job(cur, worker_id: str) -> dict | None:
    """Claim the oldest runnable job: queued and due, or running with an
    expired lease (its worker died). FOR UPDATE SKIP LOCKED lets any number
    of workers -- threads or processes -- claim distinct jobs safely."""
    cur.execute(
        f"""
        UPDATE agent_jobs
        SET status = 'running', attempts = attempts + 1, locked_by = %s,
            lease_expires_at = NOW() + INTERVAL '{JOB_LEASE_MINUTES} minutes',
            started_at = COALESCE(started_at, NOW())
        WHERE id = (
            SELECT id FROM agent_jobs
            WHERE (status = 'queued' AND run_after <= NOW())
               OR (status = 'running' AND lease_expires_at < NOW())
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, task_id, kind, attempts, max_attempts
        """,
        (worker_id,),
    )
    row = cur.fetchone()
    return dict(zip(("id", "task_id", "kind", "attempts", "max_attempts"), row)) if row else None


def finish_job(cur, job_id: int, *, status: str, error: str | None = None) -> None:
    cur.execute(
        "UPDATE agent_jobs SET status = %s, last_error = %s, finished_at = NOW(), lease_expires_at = NULL WHERE id = %s",
        (status, error, job_id),
    )


def latest_job(cur, task_id: int) -> dict | None:
    cur.execute(
        "SELECT id, kind, status, attempts, last_error, created_at, started_at, finished_at "
        "FROM agent_jobs WHERE task_id = %s ORDER BY id DESC LIMIT 1",
        (task_id,),
    )
    row = cur.fetchone()
    return dict(zip(("id", "kind", "status", "attempts", "last_error", "created_at", "started_at", "finished_at"),
                    row)) if row else None


def requeue_job(cur, job_id: int, *, delay_seconds: int, error: str) -> None:
    """Put a job that raised back on the queue after a backoff."""
    cur.execute(
        """
        UPDATE agent_jobs
        SET status = 'queued', last_error = %s, locked_by = NULL, lease_expires_at = NULL,
            run_after = NOW() + make_interval(secs => %s)
        WHERE id = %s
        """,
        (error, delay_seconds, job_id),
    )
