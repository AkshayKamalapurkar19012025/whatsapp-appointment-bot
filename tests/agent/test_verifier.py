"""Verifier agent: `enforce` is what makes a verdict trustworthy -- it must
not depend on the model being right."""

import json

from app.agent.agents.verifier import enforce, run_verifier
from app.agent.llm import FakeLLM
from app.agent.models import CriterionVerdict, VerifierResult

OUTPUT = {"appointment_id": 9, "status": "CHECKED_IN", "visited_at": "2030-01-07T10:31:00+05:30"}
TRACE = [{"kind": "tool_call", "step": 3, "tool": "appointment.check_in", "status": "success", "response": OUTPUT}]
CHECKS = [{"name": "appointment_status_is_CHECKED_IN", "passed": True, "detail": "status re-read: CHECKED_IN"}]
CRITERIA = ["The appointment status is CHECKED_IN"]


def _result(verdict="pass", evidence='"status": "CHECKED_IN"', criterion=CRITERIA[0], crit_verdict="pass", hint=""):
    return VerifierResult(verdict=verdict, criteria=[CriterionVerdict(criterion=criterion, verdict=crit_verdict,
                                                                     evidence=evidence)], fix_hint=hint)


def test_pass_with_evidence_quoted_from_the_output():
    out = enforce(_result(), CRITERIA, OUTPUT, TRACE, CHECKS)
    assert out.verdict == "pass" and out.criteria[0].verdict == "pass"


def test_evidence_may_come_from_the_trace_or_the_deterministic_checks():
    assert enforce(_result(evidence="status re-read: CHECKED_IN"), CRITERIA, OUTPUT, TRACE, CHECKS).verdict == "pass"


def test_pass_without_evidence_fails():
    out = enforce(_result(evidence=""), CRITERIA, OUTPUT, TRACE, CHECKS)
    assert out.verdict == "fail" and "without evidence" in out.fix_hint


def test_fabricated_evidence_fails():
    out = enforce(_result(evidence="status: CHECKED_IN (confirmed by the front desk)"), CRITERIA, OUTPUT, TRACE, CHECKS)
    assert out.verdict == "fail" and "does not appear" in out.fix_hint


def test_executor_notes_are_not_evidence():
    """A claim that only exists in the Executor's notes can't pass a criterion."""
    notes = "I checked the patient in and everything went perfectly"
    out = enforce(_result(evidence=notes), CRITERIA, OUTPUT, TRACE, CHECKS)
    assert out.verdict == "fail"


def test_a_failed_deterministic_check_overrides_a_model_pass():
    checks = [{"name": "appointment_status_is_CHECKED_IN", "passed": False, "detail": "status re-read: CONFIRMED"}]
    out = enforce(_result(), CRITERIA, OUTPUT, TRACE, checks)
    assert out.verdict == "fail" and "appointment_status_is_CHECKED_IN" in out.fix_hint


def test_insufficient_trace_cannot_verify_anything():
    out = enforce(_result(), CRITERIA, OUTPUT, [], CHECKS)
    assert out.verdict == "fail" and "no tool trace" in out.fix_hint


def test_ambiguous_result_is_uncertain_and_goes_to_a_human():
    out = enforce(_result(verdict="uncertain"), CRITERIA, OUTPUT, TRACE, CHECKS)
    assert out.verdict == "uncertain"


def test_uncertain_never_masks_a_hard_failure():
    out = enforce(_result(verdict="uncertain", evidence=""), CRITERIA, OUTPUT, TRACE, CHECKS)
    assert out.verdict == "fail"


def test_every_criterion_needs_its_own_verdict():
    two = [*CRITERIA, "Exactly one appointment was checked in"]
    out = enforce(_result(), two, OUTPUT, TRACE, CHECKS)
    assert out.verdict == "fail" and len(out.criteria) == 2 and out.criteria[1].verdict == "fail"


def test_extra_or_rewritten_criteria_from_the_model_are_ignored():
    r = VerifierResult(verdict="pass", criteria=[
        CriterionVerdict(criterion="Something easier", verdict="pass", evidence='"status": "CHECKED_IN"')])
    out = enforce(r, CRITERIA, OUTPUT, TRACE, CHECKS)
    assert out.verdict == "fail" and [c.criterion for c in out.criteria] == CRITERIA


def test_a_model_fail_is_kept_with_its_specific_hint():
    out = enforce(_result(verdict="fail", crit_verdict="fail", evidence="", hint="status is CONFIRMED, not CHECKED_IN"),
                  CRITERIA, OUTPUT, TRACE, CHECKS)
    assert out.verdict == "fail" and "CONFIRMED" in out.fix_hint


def test_run_verifier_sends_criteria_output_trace_and_checks():
    llm = FakeLLM(script={"verifier": [json.dumps({"verdict": "pass", "criteria": [], "fix_hint": ""})]})
    run_verifier(llm, CRITERIA, OUTPUT, TRACE, CHECKS)
    payload = llm.calls[0]["payload"]
    assert set(payload) == {"criteria", "output", "trace", "deterministic_checks"}
