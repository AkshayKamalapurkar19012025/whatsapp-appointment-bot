"""
Deterministic argument references.

A Planner plans before anything runs, so it can't know the ids a search
will return -- and it must not be allowed to *type* them either (that is
how a model invents or mis-copies a patient id or an amount). A step
argument may instead be a reference:

    {"$from": {"step": 1, "list": "patients", "field": "id"}}   # from a list
    {"$from": {"step": 3, "field": "total_due"}}                # from a single-object output

The orchestrator substitutes it from the captured raw output of that
step; for a list, only if it has EXACTLY ONE item. Zero or several items
stops the task with NEEDS_INPUT and the candidates listed -- the system
never picks "the first Ravi".
"""

from typing import Any

REF_KEY = "$from"


class RefError(Exception):
    """A reference can't be resolved from the recorded outputs (missing
    step/list/field): a plan defect -> INPUT_PROBLEM."""


class RefAmbiguous(Exception):
    """A reference points at a list with 0 or >1 items -> NEEDS_INPUT."""

    def __init__(self, step: int, list_name: str, candidates: list):
        self.step = step
        self.list_name = list_name
        self.candidates = candidates
        super().__init__(
            f"step {step}'s '{list_name}' has {len(candidates)} item(s); exactly one is required"
        )


def is_ref(value: Any) -> bool:
    return isinstance(value, dict) and REF_KEY in value


def well_formed(ref: Any) -> bool:
    if not (isinstance(ref, dict) and set(ref) == {REF_KEY}):
        return False
    body = ref[REF_KEY]
    return (
        isinstance(body, dict)
        and set(body) in ({"step", "list", "field"}, {"step", "field"})
        and isinstance(body["step"], int)
        and not isinstance(body["step"], bool)
        and isinstance(body.get("list", ""), str)
        and isinstance(body["field"], str)
    )


def ref_step(ref: dict) -> int:
    return ref[REF_KEY]["step"]


def resolve_args(args: dict, outputs: dict[int, dict]) -> dict:
    """Return `args` with every reference substituted. `outputs` maps a
    step id to that step's captured raw tool output."""
    resolved = {}
    for name, value in args.items():
        if not is_ref(value):
            resolved[name] = value
            continue
        if not well_formed(value):
            raise RefError(f"argument {name!r} has a malformed reference")
        body = value[REF_KEY]
        output = outputs.get(body["step"])
        if output is None:
            raise RefError(f"argument {name!r} references step {body['step']}, which has no recorded output")
        if "list" not in body:
            if body["field"] not in output:
                raise RefError(f"step {body['step']} output has no field {body['field']!r}")
            resolved[name] = output[body["field"]]
            continue
        items = output.get(body["list"])
        if not isinstance(items, list):
            raise RefError(f"step {body['step']} output has no list {body['list']!r}")
        if len(items) != 1:
            raise RefAmbiguous(body["step"], body["list"], items)
        item = items[0]
        if not isinstance(item, dict) or body["field"] not in item:
            raise RefError(f"step {body['step']} item has no field {body['field']!r}")
        resolved[name] = item[body["field"]]
    return resolved
