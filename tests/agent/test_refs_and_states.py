"""Deterministic argument references and the lifecycle state machine."""

import pytest

from app.agent import states
from app.agent.refs import RefAmbiguous, RefError, resolve_args
from tests.agent.support import ref

OUT = {1: {"patients": [{"id": 5, "name": "Ravi Kumar"}]}}


def test_a_reference_resolves_from_recorded_output():
    assert resolve_args({"patient_id": ref(1, "patients"), "date": "2030-01-07"}, OUT) == {
        "patient_id": 5, "date": "2030-01-07"}


@pytest.mark.parametrize("patients", [[], [{"id": 1}, {"id": 2}]])
def test_zero_or_several_candidates_never_pick_one(patients):
    with pytest.raises(RefAmbiguous) as exc:
        resolve_args({"patient_id": ref(1, "patients")}, {1: {"patients": patients}})
    assert exc.value.candidates == patients


@pytest.mark.parametrize("args,outputs", [
    ({"x": ref(9, "patients")}, OUT), ({"x": ref(1, "nope")}, OUT), ({"x": ref(1, "patients", "nope")}, OUT),
    ({"x": {"$from": {"step": 1}}}, OUT),
])
def test_broken_references_are_input_problems(args, outputs):
    with pytest.raises(RefError):
        resolve_args(args, outputs)


def test_every_state_in_the_prompt_exists_and_illegal_jumps_are_refused():
    assert states.can_transition("RECEIVED", "INTAKE") and states.can_transition("PLANNED", "EXECUTING")
    for bad in [("RECEIVED", "COMPLETED"), ("PLANNED", "COMPLETED"), ("EXECUTING", "COMPLETED"),
                ("COMPLETED", "EXECUTING"), ("CANCELLED", "EXECUTING"), ("NEEDS_INPUT", "PLANNING")]:
        assert not states.can_transition(*bad), bad


def test_terminal_states_have_no_exits():
    for s in states.TERMINAL_STATES:
        assert states.TRANSITIONS[s] == frozenset()


def test_any_active_state_can_escalate_or_cancel():
    for s in set(states.TRANSITIONS) - states.TERMINAL_STATES:
        assert states.can_transition(s, "ESCALATED") and states.can_transition(s, "CANCELLED"), s
