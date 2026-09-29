"""
Formal Hospital Tool Registry.

A Tool is the single, audited bridge from a model's intent to an existing
hospital service. Invariants enforced at registration (not by convention):

  * every non-read tool requires idempotency, a deterministic precheck and
    deterministic postchecks;
  * a `high` risk tool -- and every financial/external one -- requires
    approval;
  * input and output are pydantic models with extra="forbid", so the
    arguments a model supplies are validated before any service is called
    and the output is a whitelist: a field the schema doesn't declare
    (a full phone number, a government id) cannot reach a trace, a model
    or a log, whatever the underlying service returned.

Authorization is checked in Tool.execute against the AuthContext built
from the human's own session -- the model has no say in it.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ValidationError

from app.agent.models import AuthContext

Operation = Literal["read", "prepare", "write", "external", "financial"]
Risk = Literal["low", "medium", "high"]

WRITE_OPERATIONS = frozenset({"write", "external", "financial"})


class ToolError(Exception):
    """A tool refused or failed. `retryable` is a fact about the failure
    (a transient DB error), not a hope; domain refusals (bad status,
    not found) are never retryable."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class ToolPermissionDenied(ToolError):
    def __init__(self, message: str):
        super().__init__("permission_denied", message)


@dataclass(frozen=True)
class Precheck:
    """Deterministic pre-write gate, run by the orchestrator (never a
    model) after arguments are resolved and before the Executor is asked
    to proceed."""

    status: Literal["ok", "already_done", "blocked", "needs_input"]
    reason: str = ""
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    operation: Operation
    risk: Risk
    requires_approval: bool
    irreversible: bool
    idempotency_required: bool
    required_permissions: tuple[str, ...]
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[Any, AuthContext, BaseModel], dict]
    precheck: Callable[[Any, AuthContext, BaseModel], Precheck] | None = None
    postchecks: Callable[[Any, AuthContext, BaseModel, dict], list[CheckResult]] | None = None
    audit_resource: Callable[[BaseModel, dict], tuple[str, int | None]] | None = None

    @property
    def is_write(self) -> bool:
        return self.operation in WRITE_OPERATIONS

    def describe(self) -> dict:
        """What the Planner is shown (never the handler or permissions)."""
        return {
            "name": self.name,
            "description": self.description,
            "operation": self.operation,
            "risk": self.risk,
            "requires_approval": self.requires_approval,
            "irreversible": self.irreversible,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
        }

    def permitted(self, ctx: AuthContext) -> bool:
        return all(ctx.has(p) for p in self.required_permissions)

    def validate_args(self, args: dict) -> BaseModel:
        try:
            return self.input_model.model_validate(args)
        except ValidationError as exc:
            raise ToolError("invalid_arguments", str(exc.errors(include_url=False))) from exc

    def check_permission(self, ctx: AuthContext) -> None:
        missing = [p for p in self.required_permissions if not ctx.has(p)]
        if missing:
            raise ToolPermissionDenied(f"actor lacks permission(s): {', '.join(missing)}")

    def execute(self, cur, ctx: AuthContext, args: dict) -> dict:
        """Permission check -> argument validation -> existing service ->
        output whitelist. Raises ToolError; never returns unvalidated data."""
        self.check_permission(ctx)
        parsed = self.validate_args(args)
        raw = self.handler(cur, ctx, parsed)
        try:
            return self.output_model.model_validate(raw).model_dump(mode="json")
        except ValidationError as exc:
            raise ToolError(
                "invalid_output", f"tool output violated its schema: {exc.errors(include_url=False)}"
            ) from exc


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        if tool.is_write and not tool.idempotency_required:
            raise ValueError(f"{tool.name}: every non-read tool must require idempotency")
        if tool.operation in ("external", "financial") and tool.risk != "high":
            raise ValueError(f"{tool.name}: external/financial tools must be risk=high")
        if tool.risk == "high" and not tool.requires_approval:
            raise ValueError(f"{tool.name}: high-risk tools must require approval")
        if not tool.is_write and (tool.irreversible or tool.requires_approval):
            raise ValueError(f"{tool.name}: a read tool cannot be irreversible or need approval")
        if tool.is_write and tool.precheck is None:
            raise ValueError(f"{tool.name}: every write tool needs a deterministic precheck")
        if tool.is_write and tool.postchecks is None:
            raise ValueError(f"{tool.name}: every write tool needs deterministic postchecks")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe_for(self, ctx: AuthContext) -> list[dict]:
        """Only the tools this actor may use: a Planner can't plan with a
        tool the human couldn't run themselves."""
        return [self._tools[n].describe() for n in sorted(self._tools) if self._tools[n].permitted(ctx)]
