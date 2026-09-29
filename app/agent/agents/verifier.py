"""Agent 4 -- Verifier: does the output meet its criteria?

Architecture: tool result -> raw trace -> deterministic checks -> Verifier.
The model reads the evidence; `enforce` then applies the rules that must
not depend on the model being right:

  * any failed deterministic check makes the verdict `fail`;
  * every input criterion gets exactly one verdict (a missing one fails);
  * a `pass` needs evidence that actually appears in the output, trace or
    check details -- quoted text that isn't there is a fail, so a model
    can't pass a step by asserting success;
  * an empty trace can't verify anything.
"""

import json
import re

from app.agent import prompts
from app.agent.llm import LLMClient, LLMResponse
from app.agent.models import CriterionVerdict, VerifierResult, parse_model_json

_NORMALIZE = re.compile(r"[\"'{}\[\],:]+")


def _norm(text: str) -> str:
    return " ".join(_NORMALIZE.sub(" ", text).lower().split())


def run_verifier(
    llm: LLMClient,
    criteria: list[str],
    output: dict,
    trace: list[dict],
    deterministic_checks: list[dict],
) -> tuple[VerifierResult, LLMResponse]:
    payload = {
        "criteria": criteria,
        "output": output,
        "trace": trace,
        "deterministic_checks": deterministic_checks,
    }
    response = llm.complete(agent="verifier", system=prompts.VERIFIER, payload=payload)
    return parse_model_json(response.text, VerifierResult), response


def enforce(
    result: VerifierResult,
    criteria: list[str],
    output: dict,
    trace: list[dict],
    deterministic_checks: list[dict],
) -> VerifierResult:
    haystack = _norm(json.dumps({"output": output, "trace": trace, "checks": deterministic_checks}, default=str))

    by_text = {c.criterion.strip(): c for c in result.criteria}
    verdicts: list[CriterionVerdict] = []
    problems: list[str] = []

    for criterion in criteria:
        given = by_text.get(criterion.strip())
        if given is None:
            verdicts.append(CriterionVerdict(criterion=criterion, verdict="fail", evidence=""))
            problems.append(f"no verdict was given for criterion: {criterion!r}")
            continue
        if given.verdict == "pass":
            evidence = given.evidence.strip()
            if not trace:
                given = CriterionVerdict(criterion=criterion, verdict="fail", evidence="")
                problems.append("there is no tool trace to verify against")
            elif not evidence:
                given = CriterionVerdict(criterion=criterion, verdict="fail", evidence="")
                problems.append(f"criterion passed without evidence: {criterion!r}")
            elif _norm(evidence) not in haystack:
                given = CriterionVerdict(criterion=criterion, verdict="fail", evidence=evidence)
                problems.append(f"quoted evidence for {criterion!r} does not appear in the output or trace")
        verdicts.append(given)

    failed_checks = [c for c in deterministic_checks if not c.get("passed")]
    if failed_checks:
        problems.append("deterministic check(s) failed: " + ", ".join(c["name"] for c in failed_checks))

    if failed_checks or any(v.verdict == "fail" for v in verdicts) or result.verdict == "fail":
        verdict = "fail"
    elif result.verdict == "uncertain":
        verdict = "uncertain"
    else:
        verdict = "pass"

    hint = result.fix_hint.strip()
    if problems:
        hint = "; ".join([p for p in [hint, *problems] if p])

    return VerifierResult(verdict=verdict, criteria=verdicts, fix_hint=hint)
