"""Phase 2: approval-gated fee settlement tools (record payment, waive fee,
settle free visit) -- and, through them, queue-token issuance."""

from decimal import Decimal

import pytest

from app.agent import states, store
from app.agent.agents.planner import PlanRejected, validate_plan
from app.agent.models import Plan
from app.agent.refs import RefError, resolve_args, well_formed
from app.agent.tools.hospital import build_default_registry
from app.agent.tools.registry import ToolError
from tests.agent.support import ctx, payment_plan, phase2_script, free_plan, waive_plan

PAY, WAIVE, FREE = "appointment_record_payment", "appointment_waive_fee", "appointment_settle_free_visit"


def _run(world, script, *, who="RECEPTIONIST", text="do it"):
    orch, llm = world.orchestrator(script, repeat_last=True)
    ctx_ = world.ctx(who)
    return orch, llm, ctx_, orch.submit(ctx_, text)


def _pay_script(world, **kw):
    return phase2_script(PAY, payment_plan(day=world.day, **kw), day=world.day, payment_method=kw.get("method", "CASH"))


# ---- record payment ------------------------------------------------------------------

def test_payment_waits_for_approval_showing_the_amount_then_records_and_issues_the_token(world):
    world.set_fee(500)
    appt = world.checked_in()
    orch, _, initiator, view = _run(world, _pay_script(world))

    assert view["state"] == states.APPROVAL_REQUIRED
    assert view["pending_approval"]["tool"] == "appointment.record_payment"
    assert view["pending_approval"]["args"] == {"appointment_id": appt, "method": "CASH", "expected_amount": "500.00"}
    assert view["pending_approval"]["preview"]["amount"] == "500.00"
    assert view["pending_approval"]["preview"]["patient"] == "Ravi Kumar"
    # nothing has moved: no money, no token
    before = world.appt(appt)
    assert before["payment_status"] == "UNPAID" and before["token_number"] is None

    done = orch.approve(view["task_id"], world.ctx("ADMIN"))

    assert done["state"] == states.COMPLETED, done
    after = world.appt(appt)
    assert (after["payment_status"], after["token_number"], float(after["payment_amount"])) == ("PAID", 1, 500.0)
    assert after["payment_method"] == "CASH"
    # same trail as the reception screen, plus the agent's own row
    actions = [r[0] for r in world.rows("SELECT action FROM audit_log ORDER BY id")]
    assert "bill.record_payment" in actions and "agent.appointment.record_payment" in actions
    assert world.rows("SELECT count(*) FROM mock_sms_outbox WHERE kind = 'QUEUE_TOKEN'")[0][0] == 1
    # the invoice/payments ledger got exactly one payment for exactly the amount due
    assert [(float(a), m) for a, m in world.rows("SELECT amount, method FROM payments")] == [(500.0, "CASH")]


def test_the_agent_path_matches_the_reception_screen_route(world, client):
    world.set_fee(500)
    via_route = world.checked_in("Meera Shah", hour=11, minute=0)
    assert client.post(f"/api/appointments/{via_route}/payment", json={"method": "CASH", "outcome": "PAID"},
                       headers=world.admin_headers).status_code == 200
    via_agent = world.checked_in("Ravi Kumar")
    orch, _, _, view = _run(world, _pay_script(world))
    orch.approve(view["task_id"], world.ctx("ADMIN"))
    a, b = world.appt(via_route), world.appt(via_agent)
    for key in ("payment_status", "payment_method", "payment_amount", "status"):
        assert a[key] == b[key], key
    assert (a["token_number"], b["token_number"]) == (1, 2)
    assert world.rows("SELECT count(*) FROM audit_log WHERE action = 'bill.record_payment'")[0][0] == 2
    assert world.rows("SELECT count(*) FROM mock_sms_outbox WHERE kind = 'QUEUE_TOKEN'")[0][0] == 2


def test_a_change_to_the_amount_due_after_the_human_was_shown_the_price_stops_the_payment(world, client):
    world.set_fee(500)
    appt = world.checked_in()
    orch, _, _, view = _run(world, _pay_script(world))
    assert view["pending_approval"]["args"]["expected_amount"] == "500.00"
    # a line item is added while the task waits: the bill is now 600
    r = client.post(f"/api/appointments/{appt}/invoice/line-items", json={"description": "Dressing", "amount": 100},
                    headers=world.admin_headers)
    assert r.status_code == 200, r.text
    done = orch.approve(view["task_id"], world.ctx("ADMIN"))
    assert done["state"] == states.BLOCKED and "600" in done["reason"]
    assert world.appt(appt)["payment_status"] == "UNPAID"
    assert world.rows("SELECT count(*) FROM payments")[0][0] == 0


def test_the_handler_itself_refuses_a_stale_amount_even_if_the_precheck_were_bypassed(world):
    world.set_fee(500)
    appt = world.checked_in()
    full = ctx(permissions=("bill.record_payment", "appointment.read", "invoice.read"))
    with world.db.cursor() as cur:
        with pytest.raises(ToolError, match="amount_changed"):
            build_default_registry().get("appointment.record_payment").execute(
                cur, full, {"appointment_id": appt, "method": "CASH", "expected_amount": "499.00"})
    world.db.rollback()
    assert world.appt(appt)["payment_status"] == "UNPAID"


def test_a_typed_amount_is_rejected_at_plan_validation(world):
    plan = Plan.model_validate(payment_plan(day=world.day, amount_arg="500.00"))
    with pytest.raises(PlanRejected, match="never a typed value"):
        validate_plan(plan, build_default_registry(), world.ctx("RECEPTIONIST"))


def test_a_planner_that_keeps_typing_the_amount_is_escalated_and_nothing_is_paid(world):
    world.set_fee(500)
    appt = world.checked_in()
    script = phase2_script(PAY, payment_plan(day=world.day, amount_arg="1.00"), day=world.day, payment_method="CASH")
    _, _, _, view = _run(world, script)
    assert view["state"] == states.ESCALATED and world.appt(appt)["payment_status"] == "UNPAID"


def test_paying_twice_is_detected_not_repeated(world):
    world.set_fee(500)
    appt = world.checked_in()
    orch, _, _, view = _run(world, _pay_script(world))
    orch.approve(view["task_id"], world.ctx("ADMIN"))
    orch2, _, _, second = _run(world, _pay_script(world))
    assert second["state"] == states.COMPLETED and second["result"]["already_done"] is True
    assert world.rows("SELECT count(*) FROM payments")[0][0] == 1
    assert world.rows("SELECT count(*) FROM mock_sms_outbox WHERE kind = 'QUEUE_TOKEN'")[0][0] == 1


def test_a_visit_that_is_not_checked_in_is_blocked(world):
    world.set_fee(500)
    world.appointment(world.patient("Ravi Kumar"))  # CONFIRMED only
    _, _, _, view = _run(world, _pay_script(world))
    assert view["state"] == states.BLOCKED and "CHECKED_IN" in view["reason"]


def test_a_zero_fee_visit_cannot_be_paid_use_settle_free(world):
    world.checked_in()  # fee defaults to 0
    _, _, _, view = _run(world, _pay_script(world))
    assert view["state"] == states.INPUT_PROBLEM  # expected_amount must be > 0
    assert world.rows("SELECT count(*) FROM payments")[0][0] == 0


@pytest.mark.parametrize("bad", ["0", "-5", "10.001", "abc", None])
def test_expected_amount_must_be_a_positive_two_decimal_number(world, bad):
    full = ctx(permissions=("bill.record_payment", "appointment.read"))
    with world.db.cursor() as cur:
        with pytest.raises(ToolError, match="invalid_arguments"):
            build_default_registry().get("appointment.record_payment").execute(
                cur, full, {"appointment_id": 1, "method": "CASH", "expected_amount": bad})


def test_unknown_payment_method_is_rejected(world):
    full = ctx(permissions=("bill.record_payment", "appointment.read"))
    with world.db.cursor() as cur:
        with pytest.raises(ToolError, match="invalid_arguments"):
            build_default_registry().get("appointment.record_payment").execute(
                cur, full, {"appointment_id": 1, "method": "BITCOIN", "expected_amount": "5.00"})


def test_the_patient_is_now_in_the_queue_with_a_token(world):
    world.set_fee(500)
    world.checked_in()
    orch, _, _, view = _run(world, _pay_script(world))
    orch.approve(view["task_id"], world.ctx("ADMIN"))
    full = ctx(permissions=("queue.read", "patient.read"))
    with world.db.cursor() as cur:
        q = build_default_registry().get("queue.get").execute(cur, full, {"doctor_id": world.seeded["doctor_id"]})
    assert q["now_serving"]["token_number"] == 1 and q["now_serving"]["patient_name"] == "Ravi Kumar"


def test_roles_without_the_permissions_cannot_start_these_tasks(world):
    world.set_fee(500)
    world.checked_in()
    # LAB_TECH / PHARMACIST have no agent permission at all; a DOCTOR has no invoice.read so cannot plan a payment
    _, llm, _, view = _run(world, _pay_script(world), who="DOCTOR")
    assert view["state"] in (states.UNAUTHORIZED, states.ESCALATED)
    assert world.rows("SELECT count(*) FROM payments")[0][0] == 0


# ---- waive ------------------------------------------------------------------------------

def _prior_completed_visit(world, patient_id):
    """A COMPLETED visit for the same patient and doctor yesterday (the waiver policy's precondition)."""
    prior = world.appointment({"id": patient_id}, hour=9, minute=0)
    world.rows("UPDATE appointments SET status = 'COMPLETED', visited_at = NOW() - INTERVAL '1 day' WHERE id = %s", (prior,))


def _waive_script(world, reason="revisit within 3 days"):
    return phase2_script(WAIVE, waive_plan(day=world.day, reason=reason), day=world.day, waiver_reason=reason)


def test_waiver_with_a_recent_visit_needs_a_second_admin_then_waives_and_issues_the_token(world):
    world.set_fee(500)
    appt = world.checked_in()
    (pid,) = world.rows("SELECT patient_id FROM appointments WHERE id = %s", (appt,))[0]
    _prior_completed_visit(world, pid)
    orch, _, _, view = _run(world, _waive_script(world), who="ADMIN")
    assert view["state"] == states.APPROVAL_REQUIRED
    assert view["pending_approval"]["preview"]["reason"] == "revisit within 3 days"
    done = orch.approve(view["task_id"], world.ctx("ADMIN"))
    assert done["state"] == states.COMPLETED, done
    after = world.appt(appt)
    assert (after["payment_status"], after["token_number"], after["waive_reason"]) == ("WAIVED", 1, "revisit within 3 days")
    actions = [r[0] for r in world.rows("SELECT action FROM audit_log")]
    assert "appointment.waive_payment" in actions and "agent.appointment.waive_consultation_fee" in actions


def test_waiver_without_the_revisit_precondition_fails_with_the_service_reason_and_changes_nothing(world):
    world.set_fee(500)
    appt = world.checked_in()
    orch, _, _, view = _run(world, _waive_script(world), who="ADMIN")
    done = orch.approve(view["task_id"], world.ctx("ADMIN"))
    assert done["state"] == states.TOOL_ERROR and "3 days" in done["reason"]
    after = world.appt(appt)
    assert after["payment_status"] == "UNPAID" and after["token_number"] is None


def test_only_someone_who_may_waive_can_start_a_waiver(world):
    world.set_fee(500)
    world.checked_in()
    _, _, _, view = _run(world, _waive_script(world), who="RECEPTIONIST")
    assert view["state"] == states.UNAUTHORIZED and "appointment.waive_payment" in view["reason"]


def test_a_blank_waiver_reason_is_needs_input(world):
    world.checked_in()
    script = _waive_script(world)
    script["intake"][0]["inputs"]["waiver_reason"] = "   "
    _, _, _, view = _run(world, script, who="ADMIN")
    assert view["state"] == states.NEEDS_INPUT and view["result"]["missing_fields"] == ["waiver_reason"]


# ---- settle free visit ------------------------------------------------------------------------

def test_a_free_visit_is_settled_after_approval_with_no_money_recorded(world):
    appt = world.checked_in()  # fee 0
    script = phase2_script(FREE, free_plan(day=world.day), day=world.day)
    orch, _, _, view = _run(world, script)
    assert view["state"] == states.APPROVAL_REQUIRED
    done = orch.approve(view["task_id"], world.ctx("ADMIN"))
    assert done["state"] == states.COMPLETED, done
    after = world.appt(appt)
    assert (after["payment_status"], after["token_number"], float(after["payment_amount"])) == ("WAIVED", 1, 0.0)
    assert world.rows("SELECT count(*) FROM payments")[0][0] == 0


def test_a_visit_with_a_fee_is_never_settled_as_free(world):
    world.set_fee(500)
    appt = world.checked_in()
    script = phase2_script(FREE, free_plan(day=world.day), day=world.day)
    _, _, _, view = _run(world, script)
    assert view["state"] == states.BLOCKED and "not free" in view["reason"]
    assert world.appt(appt)["token_number"] is None


# ---- approval integrity carries over to the new tools -----------------------------------------------

def test_a_forged_or_self_issued_approval_cannot_release_a_payment(world):
    world.set_fee(500)
    appt = world.checked_in()
    orch, _, initiator, view = _run(world, _pay_script(world), who="ADMIN")
    with pytest.raises(store.ApprovalError):
        orch.resume(view["task_id"], 1, "forged")
    with pytest.raises(store.ApprovalError, match="cannot approve"):
        orch.approve(view["task_id"], initiator)
    assert world.appt(appt)["payment_status"] == "UNPAID"


def test_rejecting_the_approval_leaves_the_visit_unpaid_and_out_of_the_queue(world):
    world.set_fee(500)
    appt = world.checked_in()
    orch, _, _, view = _run(world, _pay_script(world))
    assert orch.approve(view["task_id"], world.ctx("ADMIN"), approve=False)["state"] == states.CANCELLED
    after = world.appt(appt)
    assert after["payment_status"] == "UNPAID" and after["token_number"] is None


# ---- scalar references -----------------------------------------------------------------------------------

def test_scalar_references_resolve_from_a_single_object_output():
    assert resolve_args({"x": {"$from": {"step": 3, "field": "total_due"}}}, {3: {"total_due": "500.00"}}) == {"x": "500.00"}
    with pytest.raises(RefError):
        resolve_args({"x": {"$from": {"step": 3, "field": "nope"}}}, {3: {"total_due": "1"}})
    assert well_formed({"$from": {"step": 1, "field": "a"}}) and not well_formed({"$from": {"step": 1}})
    assert not well_formed({"$from": {"step": 1, "field": "a", "extra": 1}})


def test_a_scalar_reference_to_a_field_the_tool_does_not_output_is_rejected(world):
    plan = payment_plan(day=world.day, amount_arg={"$from": {"step": 3, "field": "password"}})
    with pytest.raises(PlanRejected, match="no field"):
        validate_plan(Plan.model_validate(plan), build_default_registry(), world.ctx("RECEPTIONIST"))
