-- AI Agent Automation Layer, Phase 3: background execution.
--
-- POST /api/agent/tasks used to run the whole pipeline (several model
-- calls) inside the HTTP request. It now records the task and enqueues a
-- job here; worker threads (app/agent/worker.py) claim jobs and run the
-- orchestrator. The queue is this table -- no broker: the app is a single
-- Postgres instance and FOR UPDATE SKIP LOCKED gives safe multi-worker
-- claiming (including several app processes) with nothing new to run.
--
-- A job carries NO secrets. Resuming after an approval doesn't need the
-- approval token: the worker reads the decided approval from
-- agent_approvals and re-verifies it (approved, unexpired, unspent,
-- bound to the exact arguments, approver != initiator).
--
-- Crash safety: a claimed job holds a lease. If the process dies, the lease
-- expires and another worker re-claims the job; the orchestrator is
-- re-entrant (completed steps are skipped, a write that already happened is
-- detected by its precheck or replayed from agent_tool_calls, never
-- repeated). Jobs that keep failing stop at max_attempts and the task is
-- escalated to a human.

CREATE TABLE agent_jobs (
    id                BIGSERIAL PRIMARY KEY,
    task_id           BIGINT NOT NULL REFERENCES agent_tasks(id),
    kind              TEXT NOT NULL CHECK (kind IN ('start', 'resume')),
    status            TEXT NOT NULL DEFAULT 'queued'
                          CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts          INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 3,
    run_after         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_by         TEXT,
    lease_expires_at  TIMESTAMPTZ,
    last_error        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ
);

-- At most one live job per task: a task is never worked by two workers.
CREATE UNIQUE INDEX agent_jobs_one_active_per_task_idx
    ON agent_jobs (task_id) WHERE status IN ('queued', 'running');
CREATE INDEX agent_jobs_claim_idx ON agent_jobs (status, run_after);
CREATE INDEX agent_jobs_task_idx ON agent_jobs (task_id);

COMMENT ON TABLE agent_jobs IS
    'Work queue for agent tasks (see app/agent/worker.py). A job is claimed with FOR UPDATE SKIP LOCKED and holds a lease while running; an expired lease means the worker died and the job is re-claimed. Contains no secrets.';
