"""Shared helpers for the agent-layer tests: scripted model responses for
the check-in vertical slice, and small builders."""

import json
from datetime import date

from app.agent.models import AuthContext


def intake_ok(*, patient="Ravi", day: date, at="10:30", criteria=None, risk="medium") -> dict:
    return {
        "status": "ok",
        "task_type": "appointment_check_in",
        "goal": f"Check in {patient}'s {at} appointment on {day.isoformat()}",
        "inputs": {"patient_reference": patient, "appointment_date": day.isoformat(), "appointment_time": at},
        "missing_fields": [],
        "acceptance_criteria": criteria if criteria is not None else [
            f"The appointment for {patient} on {day.isoformat()} at {at} has status CHECKED_IN"],
        "risk_tier": risk,
        "deadline": None,
    }


def ref(step, list_name, field="id"):
    return {"$from": {"step": step, "list": list_name, "field": field}}


def slice_plan(*, patient="Ravi", day: date, at="10:30") -> dict:
    return {
        "status": "ok",
        "steps": [
            {"id": 1, "action": "Find the patient", "tool": "patient.search",
             "args": {"query": patient}, "expected_output": "exactly one matching patient",
             "success_check": "Exactly one patient matching the reference was returned",
             "irreversible": False, "depends_on": []},
            {"id": 2, "action": "Find the appointment", "tool": "appointment.search",
             "args": {"patient_id": ref(1, "patients"), "date": day.isoformat(), "time": at},
             "expected_output": "exactly one appointment", "success_check": "Exactly one appointment was returned",
             "irreversible": False, "depends_on": [1]},
            {"id": 3, "action": "Check the patient in", "tool": "appointment.check_in",
             "args": {"appointment_id": ref(2, "appointments")},
             "expected_output": "appointment CHECKED_IN",
             "success_check": "The appointment status is CHECKED_IN", "irreversible": False, "depends_on": [2]},
        ],
        "change_from_previous": None,
        "reason": None,
    }


def executor_proceeds(payload: dict) -> dict:
    return {"action": "call_tool", "tool": payload["step"]["tool"], "notes": ""}


def verifier_passes(payload: dict) -> dict:
    """Passes every criterion, quoting the whole output (a real substring
    of what the verifier was given)."""
    evidence = json.dumps(payload["output"])
    return {
        "verdict": "pass",
        "criteria": [{"criterion": c, "verdict": "pass", "evidence": evidence} for c in payload["criteria"]],
        "fix_hint": "",
    }


def verifier_fails(payload: dict) -> dict:
    return {
        "verdict": "fail",
        "criteria": [{"criterion": c, "verdict": "fail", "evidence": ""} for c in payload["criteria"]],
        "fix_hint": "the output does not satisfy the criterion",
    }


def slice_script(*, day: date, patient="Ravi", at="10:30") -> dict:
    return {
        "intake": [intake_ok(patient=patient, day=day, at=at)],
        "planner": [slice_plan(patient=patient, day=day, at=at)],
        "executor": [executor_proceeds],
        "verifier": [verifier_passes],
    }


def ctx(**overrides) -> AuthContext:
    base = dict(actor_id=1, actor_role="RECEPTIONIST", facility_id=1,
                permissions=("agent.task.create", "appointment.check_in", "appointment.read", "patient.read"))
    base.update(overrides)
    return AuthContext(**base)


# ---------------------------------------------------------------------
# Test-only tools
# ---------------------------------------------------------------------

import dataclasses  # noqa: E402

from pydantic import BaseModel, ConfigDict  # noqa: E402

from app.agent.tools.hospital import build_default_registry  # noqa: E402
from app.agent.tools.registry import CheckResult, Precheck, Tool, ToolError  # noqa: E402
from app.services.audit_log import record_audit_log  # noqa: E402


class _DangerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appointment_id: int


class _DangerOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appointment_id: int
    done: bool


def dangerous_registry(executions: list):
    """The default registry plus test.dangerous_write: a HIGH-risk,
    irreversible, approval-gated write (nothing in Phase 1's real tool set
    needs approval, so approval is exercised through this stand-in)."""
    r = build_default_registry()

    def handler(cur, ctx, args):
        executions.append(args.appointment_id)
        record_audit_log(cur, hospital_id=ctx.facility_id, staff_id=ctx.actor_id, action="test.dangerous_write",
                         resource_type="appointment", resource_id=args.appointment_id)
        return {"appointment_id": args.appointment_id, "done": True}

    r.register(Tool(
        name="test.dangerous_write", description="test-only high-risk write", operation="external", risk="high",
        requires_approval=True, irreversible=True, idempotency_required=True,
        required_permissions=("appointment.check_in",), input_model=_DangerIn, output_model=_DangerOut,
        handler=handler, precheck=lambda cur, ctx, a: Precheck("ok"),
        postchecks=lambda cur, ctx, a, out: [CheckResult("done_flag", out["done"], f"done={out['done']}")],
        audit_resource=lambda a, out: ("appointment", out["appointment_id"]),
    ))
    return r


def dangerous_plan(*, day: date, at="10:30") -> dict:
    plan = slice_plan(day=day, at=at)
    plan["steps"][2] = {
        "id": 3, "action": "Do the dangerous thing", "tool": "test.dangerous_write",
        "args": {"appointment_id": ref(2, "appointments")}, "expected_output": "done",
        "success_check": "done is true", "irreversible": True, "depends_on": [2],
    }
    return plan


def swap_tool(registry, name: str, **changes):
    """Replace a registered tool with a modified copy (e.g. a flaky handler)."""
    registry._tools[name] = dataclasses.replace(registry._tools[name], **changes)
    return registry


def failing_handler(counter: list, *, fail_times: int, retryable: bool, inner):
    def handler(cur, ctx, args):
        counter.append(1)
        if len(counter) <= fail_times:
            raise ToolError("backend_down", "the hospital service is unavailable", retryable=retryable)
        return inner(cur, ctx, args)
    return handler
