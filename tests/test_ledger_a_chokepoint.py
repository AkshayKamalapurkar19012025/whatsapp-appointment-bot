"""
Phase 10B (Billing Ledger Coexistence, ADR-009 Option B) structural
guard: appointments.payment_status must be written from exactly one
place, app/services/appointment_services.py's
_write_consultation_payment_status -- the chokepoint that also performs
the same-transaction Ledger B mirror (billing_services.
mirror_legacy_consultation_payment_service). A second write path would
be a silent way for the two ledgers to diverge without the mirror ever
running, exactly the failure mode this phase exists to prevent.

Same category of test as tests/test_hospital_tenant_coverage.py's
schema-introspection coverage check -- a structural property enforced
by scanning the actual source, not a behavioral assertion. Uses `ast`
(scoped to each function's own body, not line-number ranges) so it
survives the chokepoint moving within its file or other functions being
added/removed/reordered around it.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app"

CHOKEPOINT_FUNCTION = "_write_consultation_payment_status"
CHOKEPOINT_FILE = APP_DIR / "services" / "appointment_services.py"


def _string_value(node: ast.AST) -> str | None:
    """The literal text of a plain string or f-string node, or None for
    anything else (covers both `"UPDATE appointments ..."` and
    f"UPDATE appointments ... {x}" shapes)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def _sql_string_constants(node: ast.AST):
    """The SQL text of every `<cursor>.execute(...)` call reachable from
    this AST node -- deliberately NOT every string constant in scope,
    which would also sweep up docstrings and comments-as-strings. A
    docstring explaining that "the Ledger A UPDATE" exists and that
    "appointments itself stores no ledger-2 reference" and describing
    "payment_status" and "settle_free_visit_service" in prose contains
    all four of UPDATE/APPOINTMENTS/PAYMENT_STATUS/SET as substrings
    without being a SQL write at all -- scoping to actual .execute(...)
    call arguments avoids that class of false positive entirely."""
    for child in ast.walk(node):
        if not (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "execute"):
            continue
        if not child.args:
            continue
        text = _string_value(child.args[0])
        if text is not None:
            yield text


def _writes_appointments_payment_status(sql_text: str) -> bool:
    upper = sql_text.upper()
    return "UPDATE" in upper and "APPOINTMENTS" in upper and "PAYMENT_STATUS" in upper and "SET" in upper


def _find_violations():
    violations = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text()
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            is_chokepoint = path == CHOKEPOINT_FILE and node.name == CHOKEPOINT_FUNCTION

            for sql_text in _sql_string_constants(node):
                if _writes_appointments_payment_status(sql_text) and not is_chokepoint:
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} (in {node.name})")

    return violations


def test_appointments_payment_status_is_written_only_by_the_chokepoint():
    assert CHOKEPOINT_FILE.exists(), f"Chokepoint file not found: {CHOKEPOINT_FILE}"

    violations = _find_violations()

    assert violations == [], (
        "appointments.payment_status must be written only from "
        f"{CHOKEPOINT_FILE.relative_to(REPO_ROOT)}::{CHOKEPOINT_FUNCTION} "
        "(the same-transaction Ledger B mirror lives there -- a second "
        "write path would silently skip it). Found writes outside the "
        "chokepoint at:\n" + "\n".join(f"  - {v}" for v in violations)
    )


def test_chokepoint_itself_actually_writes_payment_status():
    """The inverse check -- proves the scanner isn't vacuously passing
    because it can no longer find the chokepoint at all (e.g. it was
    renamed and every real write site now shows up as a "violation" the
    first test would catch, but this confirms the positive case too)."""
    source = CHOKEPOINT_FILE.read_text()
    tree = ast.parse(source, filename=str(CHOKEPOINT_FILE))

    chokepoint_node = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == CHOKEPOINT_FUNCTION
        ),
        None,
    )
    assert chokepoint_node is not None, f"{CHOKEPOINT_FUNCTION} not found in {CHOKEPOINT_FILE}"

    found = any(
        _writes_appointments_payment_status(sql_text) for sql_text in _sql_string_constants(chokepoint_node)
    )
    assert found, f"{CHOKEPOINT_FUNCTION} does not appear to write appointments.payment_status at all"
