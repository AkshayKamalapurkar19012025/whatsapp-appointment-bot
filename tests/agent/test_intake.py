"""Intake agent: TaskSpec production and the deterministic hardening the
orchestrator applies on top of whatever the model says."""

from datetime import date

import pytest

from app.agent.agents.intake import harden_spec, run_intake
from app.agent.llm import FakeLLM
from app.agent.models import ModelOutputError, TaskSpec
from app.agent.task_types import TASK_TYPES
from tests.agent.support import intake_ok

DAY = date(2030, 1, 7)


def _run(response):
    llm = FakeLLM(script={"intake": [response]})
    spec, _ = run_intake(llm, "Check in today's 10:30 appointment for Ravi", today=DAY, timezone="Asia/Kolkata")
    return harden_spec(spec), llm


def test_valid_task():
    spec, llm = _run(intake_ok(day=DAY))
    assert spec.status == "ok" and spec.task_type == "appointment_check_in"
    assert spec.inputs == {"patient_reference": "Ravi", "appointment_date": "2030-01-07", "appointment_time": "10:30"}
    # the model was given today's date and the task types in scope, and nothing else about the hospital
    payload = llm.calls[0]["payload"]
    assert payload["context"] == {"today": "2030-01-07", "timezone": "Asia/Kolkata"}
    assert [t["task_type"] for t in payload["task_types"]] == [
        "appointment_check_in", "appointment_record_payment", "appointment_waive_fee", "appointment_settle_free_visit"]


def test_unknown_task_is_out_of_scope():
    spec, _ = _run({"status": "out_of_scope"})
    assert spec.status == "out_of_scope"


def test_model_claiming_a_task_type_that_does_not_exist_is_out_of_scope():
    bad = intake_ok(day=DAY) | {"task_type": "delete_all_patients"}
    spec, _ = _run(bad)
    assert spec.status == "out_of_scope"


def test_missing_required_field_is_needs_input_and_names_the_field():
    spec, _ = _run({"status": "needs_input", "task_type": "appointment_check_in", "missing_fields": ["appointment_time"],
                    "inputs": {"patient_reference": "Ravi", "appointment_date": "2030-01-07"}})
    assert spec.status == "needs_input" and spec.missing_fields == ["appointment_time"]


def test_model_cannot_claim_ok_while_a_required_field_is_missing():
    bad = intake_ok(day=DAY)
    del bad["inputs"]["appointment_time"]
    spec, _ = _run(bad)
    assert spec.status == "needs_input" and spec.missing_fields == ["appointment_time"]


@pytest.mark.parametrize("field,value", [
    ("appointment_time", "10.30"), ("appointment_time", "25:00"), ("appointment_time", "10:30 AM"),
    ("appointment_date", "today"), ("appointment_date", "07/01/2030"), ("patient_reference", "   "),
])
def test_invalid_field_values_are_not_accepted_or_defaulted(field, value):
    bad = intake_ok(day=DAY)
    bad["inputs"][field] = value
    spec, _ = _run(bad)
    assert spec.status == "needs_input" and spec.missing_fields == [field]
    assert field not in spec.inputs  # no silent fix-up


def test_ambiguous_reference_without_a_time_asks_for_it():
    spec, _ = _run({"status": "needs_input", "task_type": "appointment_check_in", "missing_fields": ["appointment_time"],
                    "inputs": {"patient_reference": "Ravi", "appointment_date": "2030-01-07"}})
    assert spec.status == "needs_input"


def test_only_required_fields_are_kept_in_inputs():
    bad = intake_ok(day=DAY)
    bad["inputs"]["also_delete_patient"] = True
    spec, _ = _run(bad)
    assert "also_delete_patient" not in spec.inputs


def test_acceptance_criteria_keep_the_models_and_always_add_the_mandatory_ones():
    spec, _ = _run(intake_ok(day=DAY, criteria=["Ravi's 10:30 appointment on 2030-01-07 is CHECKED_IN"]))
    assert spec.acceptance_criteria[0] == "Ravi's 10:30 appointment on 2030-01-07 is CHECKED_IN"
    for mandatory in TASK_TYPES["appointment_check_in"].mandatory_criteria:
        assert mandatory in spec.acceptance_criteria


def test_missing_model_criteria_still_yield_binary_criteria():
    spec, _ = _run(intake_ok(day=DAY, criteria=[]))
    assert spec.acceptance_criteria == list(TASK_TYPES["appointment_check_in"].mandatory_criteria)


def test_duplicate_and_blank_criteria_are_dropped():
    m = TASK_TYPES["appointment_check_in"].mandatory_criteria[0]
    spec, _ = _run(intake_ok(day=DAY, criteria=["  ", m, m]))
    assert spec.acceptance_criteria.count(m) == 1 and "" not in spec.acceptance_criteria


def test_risk_can_never_be_classified_below_the_task_types_floor():
    spec, _ = _run(intake_ok(day=DAY, risk="low"))
    assert spec.risk_tier == "medium"


def test_a_higher_risk_from_the_model_is_kept():
    spec, _ = _run(intake_ok(day=DAY, risk="high"))
    assert spec.risk_tier == "high"


@pytest.mark.parametrize("raw", [
    "not json", "[]", '{"status": "ok"} trailing', '```json\n{"status": "out_of_scope"}\n```',
    '{"status": "maybe"}', '{"status": "ok", "unexpected": 1}', '{"status": "ok", "risk_tier": "extreme"}',
])
def test_malformed_model_output_is_rejected(raw):
    llm = FakeLLM(script={"intake": [raw]})
    with pytest.raises(ModelOutputError):
        run_intake(llm, "x", today=DAY, timezone="Asia/Kolkata")


def test_raw_input_is_truncated_before_it_reaches_the_model():
    llm = FakeLLM(script={"intake": [{"status": "out_of_scope"}]})
    run_intake(llm, "a" * 10_000, today=DAY, timezone="Asia/Kolkata")
    assert len(llm.calls[0]["payload"]["raw_input"]) == 2000


def test_intake_system_prompt_is_static():
    """Per-call data travels in the payload; the system prompt never varies."""
    llm = FakeLLM(script={"intake": [{"status": "out_of_scope"}, {"status": "out_of_scope"}]})
    run_intake(llm, "one", today=DAY, timezone="Asia/Kolkata")
    run_intake(llm, "two", today=date(2031, 2, 2), timezone="UTC")
    assert llm.calls[0]["system"] == llm.calls[1]["system"]
    assert TaskSpec.model_fields  # sanity
