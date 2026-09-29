"""
Typed contracts between the orchestrator and the five agents. Every
model-produced JSON document is parsed into one of these with
extra="forbid" -- an unexpected key, a wrong type or a value outside the
allowed set is a rejection (ModelOutputError), never silently coerced.
"""

import json
from datetime import date, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

RiskTier = Literal["low", "medium", "high"]


class ModelOutputError(Exception):
    """A model returned something that isn't the JSON contract it was
    given. The orchestrator escalates rather than guessing at intent."""

    raw: str = ""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def parse_model_json(text: str, model: type[BaseModel]):
    """Strict: the whole response must be one JSON object (no prose, no
    code fence) that validates against `model`."""
    def reject(message: str, cause: Exception | None = None):
        error = ModelOutputError(message)
        error.raw = str(text)[:2000]
        raise error from cause

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        reject(f"not valid JSON: {exc}", exc)

    if not isinstance(data, dict):
        reject("expected a JSON object")

    try:
        return model.model_validate(data)
    except ValidationError as exc:
        reject(f"schema violation: {exc.errors(include_url=False)}", exc)


# ---------------------------------------------------------------------
# Authorization context
# ---------------------------------------------------------------------

class PatientScope(_Strict):
    """Which patients the task may touch. "hospital" = any patient of the
    actor's own hospital (the default: staff already have that reach
    through the existing endpoints); "patients" narrows to an explicit
    allow-list (e.g. a task started from one patient's chart)."""

    mode: Literal["hospital", "patients"] = "hospital"
    patient_ids: tuple[int, ...] = ()


class AuthContext(_Strict):
    """Built server-side from the authenticated staff session
    (app/agent/authz.py) -- never taken from a request body or a model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: int
    actor_role: str
    facility_id: int          # hospital_id; named per the master prompt
    department_id: int | None = None
    permissions: tuple[str, ...] = ()
    patient_scope: PatientScope = PatientScope()
    session_id: str = "-"

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def patient_allowed(self, patient_id: int) -> bool:
        return self.patient_scope.mode == "hospital" or patient_id in self.patient_scope.patient_ids


# ---------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------

class TaskSpec(_Strict):
    status: Literal["ok", "out_of_scope", "needs_input"]
    task_type: str = ""
    goal: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risk_tier: RiskTier = "low"
    deadline: str | None = None


# ---------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------

class PlanStep(_Strict):
    id: int
    action: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    expected_output: str = ""
    success_check: str
    irreversible: bool = False
    depends_on: list[int] = Field(default_factory=list)

    @field_validator("success_check")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("success_check must not be blank")
        return v


class Plan(_Strict):
    status: Literal["ok", "blocked", "unverifiable", "too_large"]
    steps: list[PlanStep] = Field(default_factory=list)
    change_from_previous: str | None = None
    reason: str | None = None


# ---------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------

class ExecutorDecision(_Strict):
    """What the Executor *model* is allowed to say before the call: proceed
    with the one planned tool, or refuse because the inputs look wrong.
    The orchestrator makes the call itself, with the planned arguments."""

    action: Literal["call_tool", "input_problem"]
    tool: str = ""
    notes: str = ""


class ExecutorResult(_Strict):
    status: Literal["done", "awaiting_approval", "tool_error", "input_problem"]
    output: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


# ---------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------

class CriterionVerdict(_Strict):
    criterion: str
    verdict: Literal["pass", "fail"]
    evidence: str = ""


class VerifierResult(_Strict):
    verdict: Literal["pass", "fail", "uncertain"]
    criteria: list[CriterionVerdict] = Field(default_factory=list)
    fix_hint: str = ""


# ---------------------------------------------------------------------
# Shared value shapes
# ---------------------------------------------------------------------

def parse_iso_date(value: Any) -> date:
    return date.fromisoformat(value)


def parse_hhmm(value: Any) -> time:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError("time must be HH:MM (24h)")
    return time.fromisoformat(value)
