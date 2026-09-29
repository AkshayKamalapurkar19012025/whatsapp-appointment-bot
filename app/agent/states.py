"""
Task lifecycle (the orchestrator's state machine). The state names are
the ones in the master prompt; UNAUTHORIZED is the single addition -- an
authorization refusal is neither a validation failure nor an escalation,
and the prompt's own list has no state for it.

TRANSITIONS is the whole truth: the orchestrator refuses (raises) on any
move not listed here, so a bug can't silently jump a task from
EXECUTING to COMPLETED.
"""

RECEIVED = "RECEIVED"
INTAKE = "INTAKE"
INTAKE_VALIDATED = "INTAKE_VALIDATED"
AUTHORIZATION_CHECK = "AUTHORIZATION_CHECK"
PLANNING = "PLANNING"
PLANNED = "PLANNED"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
APPROVED = "APPROVED"
EXECUTING = "EXECUTING"
VERIFYING = "VERIFYING"
STEP_VERIFIED = "STEP_VERIFIED"
NEXT_STEP = "NEXT_STEP"
COMPLETED = "COMPLETED"

OUT_OF_SCOPE = "OUT_OF_SCOPE"
NEEDS_INPUT = "NEEDS_INPUT"
UNAUTHORIZED = "UNAUTHORIZED"
BLOCKED = "BLOCKED"
UNVERIFIABLE = "UNVERIFIABLE"
TOOL_ERROR = "TOOL_ERROR"
INPUT_PROBLEM = "INPUT_PROBLEM"
VERIFICATION_FAILED = "VERIFICATION_FAILED"
UNCERTAIN = "UNCERTAIN"
ESCALATED = "ESCALATED"
CANCELLED = "CANCELLED"

# Terminal: nothing further happens to the task.
TERMINAL_STATES = frozenset({
    COMPLETED, OUT_OF_SCOPE, NEEDS_INPUT, UNAUTHORIZED, BLOCKED, UNVERIFIABLE,
    TOOL_ERROR, INPUT_PROBLEM, VERIFICATION_FAILED, UNCERTAIN, ESCALATED, CANCELLED,
})

# Failure states any active stage may fall into. ESCALATED = a human must
# look; CANCELLED = a human (or the initiator) stopped it.
_ANYWHERE = {ESCALATED, CANCELLED}

TRANSITIONS: dict[str, frozenset[str]] = {
    RECEIVED: frozenset({INTAKE}) | _ANYWHERE,
    INTAKE: frozenset({INTAKE_VALIDATED, OUT_OF_SCOPE, NEEDS_INPUT}) | _ANYWHERE,
    INTAKE_VALIDATED: frozenset({AUTHORIZATION_CHECK}) | _ANYWHERE,
    AUTHORIZATION_CHECK: frozenset({PLANNING, UNAUTHORIZED}) | _ANYWHERE,
    PLANNING: frozenset({PLANNED, BLOCKED, UNVERIFIABLE}) | _ANYWHERE,
    PLANNED: frozenset({EXECUTING}) | _ANYWHERE,
    APPROVAL_REQUIRED: frozenset({APPROVED}) | _ANYWHERE,
    APPROVED: frozenset({EXECUTING}) | _ANYWHERE,
    EXECUTING: frozenset({
        VERIFYING, APPROVAL_REQUIRED, NEEDS_INPUT, BLOCKED, TOOL_ERROR,
        INPUT_PROBLEM, UNAUTHORIZED,
        STEP_VERIFIED,  # a precheck found the write already in effect: nothing to execute
    }) | _ANYWHERE,
    VERIFYING: frozenset({
        STEP_VERIFIED, VERIFICATION_FAILED, UNCERTAIN, EXECUTING, PLANNING,
        COMPLETED,  # final sign-off after the last step
    }) | _ANYWHERE,
    STEP_VERIFIED: frozenset({NEXT_STEP, COMPLETED, VERIFYING}) | _ANYWHERE,
    NEXT_STEP: frozenset({EXECUTING}) | _ANYWHERE,
    }
for _terminal in TERMINAL_STATES:
    TRANSITIONS.setdefault(_terminal, frozenset())


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in TRANSITIONS.get(from_state, frozenset())
