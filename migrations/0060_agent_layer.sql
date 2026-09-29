-- AI Agent Automation Layer (docs/implementation/AI_AGENT_IMPLEMENTATION_AUDIT.md,
-- Phase 1). These tables record what the agent layer *did and why*; they
-- are NOT a system of record for hospital data -- every hospital fact
-- still lives in, and is only ever changed through, the existing
-- services/tables. Agent-initiated hospital writes additionally emit an
-- ordinary audit_log row (migrations/0033) in the same transaction.
--
-- Seven tables (the audit's consolidation of the master prompt's nine):
-- agent_task_inputs is folded into agent_tasks.raw_input, agent_failures
-- into agent_audit_events (a failure is an event with a cause).
--
-- Privacy: raw_input is free text typed by staff and can contain patient
-- names; raw_response holds only what a tool's whitelisted output schema
-- allows (no government id, no full phone number -- see app/agent/tools).
-- No secrets are stored: approval tokens are kept only as SHA-256 hashes,
-- same pattern as staff_sessions.token_hash.

CREATE TABLE agent_tasks (
    id                  BIGSERIAL PRIMARY KEY,
    hospital_id         BIGINT NOT NULL REFERENCES hospitals(id),
    initiated_by        BIGINT NOT NULL REFERENCES staff(id),
    state               TEXT NOT NULL DEFAULT 'RECEIVED'
                            CHECK (state IN (
                                'RECEIVED', 'INTAKE', 'INTAKE_VALIDATED',
                                'AUTHORIZATION_CHECK', 'PLANNING', 'PLANNED',
                                'APPROVAL_REQUIRED', 'APPROVED', 'EXECUTING',
                                'VERIFYING', 'STEP_VERIFIED', 'NEXT_STEP',
                                'COMPLETED',
                                'OUT_OF_SCOPE', 'NEEDS_INPUT', 'UNAUTHORIZED',
                                'BLOCKED', 'UNVERIFIABLE', 'TOOL_ERROR',
                                'INPUT_PROBLEM', 'VERIFICATION_FAILED',
                                'UNCERTAIN', 'ESCALATED', 'CANCELLED'
                            )),
    raw_input           TEXT NOT NULL,
    auth_context        JSONB NOT NULL,
    task_spec           JSONB,
    task_type           TEXT,
    risk_tier           TEXT CHECK (risk_tier IN ('low', 'medium', 'high')),
    idempotency_key     TEXT,
    replan_count        INTEGER NOT NULL DEFAULT 0,
    final_reason        TEXT,
    result              JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ
);

-- A client retrying the same submission must get the same task back, not
-- a second one (which could execute the same hospital action twice).
CREATE UNIQUE INDEX agent_tasks_idempotency_idx
    ON agent_tasks (hospital_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX agent_tasks_hospital_created_idx ON agent_tasks (hospital_id, created_at DESC);
CREATE INDEX agent_tasks_state_idx ON agent_tasks (state);

COMMENT ON TABLE agent_tasks IS
    'One row per submitted AI task. state is the orchestrator lifecycle (app/agent/orchestrator.py); every transition also appends an agent_audit_events row.';

CREATE TABLE agent_plans (
    id                    BIGSERIAL PRIMARY KEY,
    task_id               BIGINT NOT NULL REFERENCES agent_tasks(id),
    version               INTEGER NOT NULL,
    status                TEXT NOT NULL,
    plan                  JSONB NOT NULL,
    change_from_previous  TEXT,
    prompt_version        TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_id, version)
);

CREATE TABLE agent_steps (
    id            BIGSERIAL PRIMARY KEY,
    plan_id       BIGINT NOT NULL REFERENCES agent_plans(id),
    task_id       BIGINT NOT NULL REFERENCES agent_tasks(id),
    step_no       INTEGER NOT NULL,
    action        TEXT NOT NULL,
    tool          TEXT NOT NULL,
    args          JSONB NOT NULL,
    irreversible  BOOLEAN NOT NULL,
    depends_on    JSONB NOT NULL DEFAULT '[]',
    state         TEXT NOT NULL DEFAULT 'PENDING'
                      CHECK (state IN ('PENDING', 'RUNNING', 'AWAITING_APPROVAL',
                                       'VERIFIED', 'FAILED', 'SKIPPED')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (plan_id, step_no)
);

CREATE TABLE agent_tool_calls (
    id               BIGSERIAL PRIMARY KEY,
    task_id          BIGINT NOT NULL REFERENCES agent_tasks(id),
    step_id          BIGINT NOT NULL REFERENCES agent_steps(id),
    attempt          INTEGER NOT NULL,
    tool             TEXT NOT NULL,
    args             JSONB NOT NULL,
    idempotency_key  TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('success', 'error', 'denied')),
    raw_response     JSONB,
    error            TEXT,
    duration_ms      INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The DB-level idempotency guard: at most one *successful* call per
-- (task, plan, step) key, however many times the orchestrator is retried
-- or resumed. Failed attempts are kept (that's the trace) but don't block.
CREATE UNIQUE INDEX agent_tool_calls_success_idempotency_idx
    ON agent_tool_calls (idempotency_key)
    WHERE status = 'success';
CREATE INDEX agent_tool_calls_task_idx ON agent_tool_calls (task_id);

CREATE TABLE agent_approvals (
    id                BIGSERIAL PRIMARY KEY,
    task_id           BIGINT NOT NULL REFERENCES agent_tasks(id),
    step_id           BIGINT NOT NULL REFERENCES agent_steps(id),
    args              JSONB NOT NULL,
    args_hash         TEXT NOT NULL,
    preview           JSONB,
    requested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decision          TEXT CHECK (decision IN ('approved', 'rejected')),
    approver_staff_id BIGINT REFERENCES staff(id),
    decided_at        TIMESTAMPTZ,
    token_hash        TEXT,
    expires_at        TIMESTAMPTZ,
    consumed_at       TIMESTAMPTZ,
    UNIQUE (step_id)
);

COMMENT ON COLUMN agent_approvals.preview IS
    'What the approver was shown besides the raw arguments (patient, doctor, amount due...), captured when approval was requested.';

COMMENT ON COLUMN agent_approvals.args_hash IS
    'SHA-256 of the exact resolved tool arguments the human was shown. The step only executes with arguments that hash to this: an approval for one action can never be spent on a different one.';

COMMENT ON COLUMN agent_approvals.token_hash IS
    'SHA-256 of the approval token, minted server-side only when a human approves; never a model output. Bound to exactly one (task, step).';

CREATE TABLE agent_verifications (
    id         BIGSERIAL PRIMARY KEY,
    task_id    BIGINT NOT NULL REFERENCES agent_tasks(id),
    step_id    BIGINT REFERENCES agent_steps(id),
    kind       TEXT NOT NULL CHECK (kind IN ('deterministic', 'llm')),
    verdict    TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'uncertain')),
    criteria   JSONB NOT NULL DEFAULT '[]',
    detail     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE agent_audit_events (
    id          BIGSERIAL PRIMARY KEY,
    task_id     BIGINT NOT NULL REFERENCES agent_tasks(id),
    seq         INTEGER NOT NULL,
    event       TEXT NOT NULL,
    from_state  TEXT,
    to_state    TEXT,
    actor_id    BIGINT REFERENCES staff(id),
    details     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_id, seq)
);

COMMENT ON TABLE agent_audit_events IS
    'Append-only, ordered per task. Nothing updates or deletes a row. Model calls are recorded here (agent, prompt version, token usage, raw model output) so a run can be reconstructed end to end.';

-- Permissions. Read permissions are new names (no read-gating existed --
-- see the audit's finding 4); the agent never exceeds the initiating
-- human's own permissions, so these gate what the *agent* may read on a
-- user's behalf, not what the existing endpoints allow.
INSERT INTO permissions (name) VALUES
    ('agent.task.create'),
    ('agent.task.read'),
    ('agent.task.approve'),
    ('patient.read'),
    ('appointment.read'),
    ('queue.read'),
    ('encounter.read'),
    ('invoice.read'),
    ('directory.read'),
    ('appointment.check_in');

-- ADMIN: everything (same convention as migrations/0048).
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'ADMIN'), id
FROM permissions
WHERE name IN ('agent.task.create', 'agent.task.read', 'agent.task.approve',
               'patient.read', 'appointment.read', 'queue.read', 'encounter.read',
               'invoice.read', 'directory.read', 'appointment.check_in');

-- STAFF: full-access fallback like migrations/0048, minus approval
-- (approving is an admin-tier decision, cf. migrations/0043).
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'STAFF'), id
FROM permissions
WHERE name IN ('agent.task.create', 'agent.task.read',
               'patient.read', 'appointment.read', 'queue.read', 'encounter.read',
               'invoice.read', 'directory.read', 'appointment.check_in');

-- RECEPTIONIST: front desk -- the only non-admin role that checks in.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'RECEPTIONIST'), id
FROM permissions
WHERE name IN ('agent.task.create', 'agent.task.read',
               'patient.read', 'appointment.read', 'queue.read', 'invoice.read',
               'directory.read', 'appointment.check_in');

-- DOCTOR / NURSE: clinical reads, no check-in, no invoices.
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.name IN ('DOCTOR', 'NURSE')
  AND p.name IN ('agent.task.create', 'agent.task.read',
                 'patient.read', 'appointment.read', 'queue.read', 'encounter.read',
                 'directory.read');

-- BILLING: patient/appointment/invoice reads only.
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE name = 'BILLING'), id
FROM permissions
WHERE name IN ('agent.task.create', 'agent.task.read',
               'patient.read', 'appointment.read', 'invoice.read', 'directory.read');

-- LAB_TECH / PHARMACIST: none -- the agent has no lab/pharmacy tools yet.
