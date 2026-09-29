"""
Phase 2 tools: the three front-desk actions that put a checked-in patient
in the queue by settling the consultation fee -- record a payment, waive
the fee, settle a zero-fee visit.

All three are FINANCIAL, HIGH risk and approval-gated (registry
invariants), performed by the existing services through
app/services/front_desk_billing_service.py -- the same service call,
audit_log row and queue-token notification as the reception screens. The
agent never issues a token itself: a token appears only as the effect of
one of these actions, which is why there is still no standalone
"generate token" tool.

Money is never typed by a model. record_payment takes `expected_amount`,
which must be a $from reference to invoice.get's total_due (enforced at
plan validation); the precheck and the handler both re-read the invoice
and refuse if the amount due is no longer that figure, and the amount the
service actually charges is server-computed. The approver sees the amount.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator

from app.agent.models import AuthContext
from app.agent.tools.hospital import (
    _In,
    _Out,
    _get_appointment,
    _not_found,
    _require_appointment_access,
)
from app.agent.tools.registry import CheckResult, Precheck, Tool, ToolError, ToolRegistry
from app.services import exceptions as svc_exc
from app.services.appointment_services import get_invoice_service
from app.services.front_desk_billing_service import (
    front_desk_record_payment_service,
    front_desk_settle_free_visit_service,
    front_desk_waive_fee_service,
)

PaymentMethod = Literal["CASH", "UPI", "CARD", "OTHER"]


class SettlementOut(_Out):
    appointment_id: int
    payment_status: str
    payment_method: str | None = None
    payment_amount: Decimal | None = None
    waive_reason: str | None = None
    token_number: int | None = None
    token_just_issued: bool


def _settlement(result: dict) -> dict:
    return {
        "appointment_id": result["id"],
        "payment_status": result["payment_status"],
        "payment_method": result.get("payment_method"),
        "payment_amount": result.get("payment_amount"),
        "waive_reason": result.get("waive_reason"),
        "token_number": result.get("token_number"),
        "token_just_issued": result["token_just_issued"],
    }


def _amount_due(cur, appointment_id: int) -> Decimal:
    try:
        return Decimal(str(get_invoice_service(cur, appointment_id)["total_due"]))
    except svc_exc.AppointmentTypeNotAssigned as exc:
        raise ToolError("conflict", "this appointment has no configured consultation fee") from exc


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _describe(item: dict) -> dict:
    start = datetime.fromisoformat(item["start_at"])  # already doctor-local
    return {
        "patient": item["patient_name"], "doctor": item["doctor_name"],
        "appointment": start.strftime("%Y-%m-%d %H:%M"), "status": item["status"],
    }


def _eligibility(item: dict | None, *, done_status: str) -> Precheck | None:
    """Shared gate: must exist, be CHECKED_IN, and not be settled another
    way. Returns a Precheck to stop on, or None to continue."""
    if item is None:
        return Precheck("blocked", "appointment not found or not visible to this task")
    detail = {"appointment_id": item["id"], "status": item["status"], "payment_status": item["payment_status"]}
    if item["status"] != "CHECKED_IN":
        return Precheck("blocked", f"the appointment is {item['status']}; it must be CHECKED_IN first", detail)
    ps = item["payment_status"]
    if ps == done_status:
        return Precheck("already_done", f"the consultation fee is already {done_status}", detail)
    if ps in ("PAID", "WAIVED", "REFUNDED"):
        return Precheck("blocked", f"the consultation fee is already {ps}; this action does not apply", detail)
    return None


def _token_and_status_checks(item: dict | None, output: dict, *, expect_status: str) -> list[CheckResult]:
    return [
        CheckResult("appointment_still_CHECKED_IN", item is not None and item["status"] == "CHECKED_IN",
                    f"status re-read: {item['status'] if item else 'not found'}"),
        CheckResult(f"payment_status_is_{expect_status}", item is not None and item["payment_status"] == expect_status,
                    f"payment_status re-read: {item['payment_status'] if item else 'not found'}"),
        CheckResult("queue_token_issued", item is not None and item["token_number"] is not None,
                    f"token_number re-read: {item['token_number'] if item else 'not found'}"),
        CheckResult("tool_reported_the_same_appointment", True if output.get("already_done") else
                    output.get("appointment_id") == (item or {}).get("id"),
                    f"output appointment_id={output.get('appointment_id')}"),
    ]


# ---- appointment.record_payment ---------------------------------------------

class RecordPaymentIn(_In):
    appointment_id: int
    method: PaymentMethod
    expected_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2,
                                     description="the amount due, from invoice.get total_due (a $from reference)")


def _record_payment(cur, ctx: AuthContext, args: RecordPaymentIn) -> dict:
    _require_appointment_access(cur, ctx, args.appointment_id)
    due = _amount_due(cur, args.appointment_id)
    if due != args.expected_amount:
        raise ToolError("amount_changed", f"the amount due is now {due}, not the {args.expected_amount} that was approved")
    try:
        result = front_desk_record_payment_service(
            cur, args.appointment_id, method=args.method, outcome="PAID",
            staff_id=ctx.actor_id, hospital_id=ctx.facility_id)
    except svc_exc.AppointmentNotFound as exc:
        raise _not_found("appointment") from exc
    except svc_exc.InvalidStatusTransition as exc:
        raise ToolError("invalid_status", "payment can only be recorded for a Checked-In appointment") from exc
    except svc_exc.PaymentStateConflict as exc:
        raise ToolError("conflict", "the consultation fee is already waived or refunded") from exc
    except svc_exc.AppointmentTypeNotAssigned as exc:
        raise ToolError("conflict", "this appointment has no configured consultation fee") from exc
    return _settlement(result)


def _record_payment_precheck(cur, ctx: AuthContext, args: RecordPaymentIn) -> Precheck:
    item = _get_appointment(cur, ctx, args.appointment_id)
    stop = _eligibility(item, done_status="PAID")
    if stop is not None:
        if stop.status == "already_done" and _dec(item["payment_amount"]) != args.expected_amount:
            return Precheck("blocked", f"already paid, but for {item['payment_amount']} not {args.expected_amount}", stop.detail)
        return stop
    due = _amount_due(cur, args.appointment_id)
    detail = {"appointment_id": item["id"], "status": item["status"], "amount_due": str(due)}
    if due != args.expected_amount:
        return Precheck("blocked", f"the amount due is {due}, but the task expected {args.expected_amount}", detail)
    return Precheck("ok", "", detail)


def _record_payment_postchecks(cur, ctx, args: RecordPaymentIn, output: dict) -> list[CheckResult]:
    item = _get_appointment(cur, ctx, args.appointment_id)
    checks = _token_and_status_checks(item, output, expect_status="PAID")
    checks.append(CheckResult(
        "payment_amount_equals_the_approved_amount",
        item is not None and _dec(item["payment_amount"]) == args.expected_amount,
        f"payment_amount re-read: {item['payment_amount'] if item else 'not found'}, approved: {args.expected_amount}"))
    return checks


def _record_payment_preview(cur, ctx, args: RecordPaymentIn) -> dict:
    item = _get_appointment(cur, ctx, args.appointment_id) or {}
    return {"action": f"Record a {args.method} payment received at the front desk",
            "amount": str(args.expected_amount), **(_describe(item) if item else {})}


# ---- appointment.waive_consultation_fee ----------------------------------------

class WaiveFeeIn(_In):
    appointment_id: int
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("a reason is required")
        return v


def _waive(cur, ctx: AuthContext, args: WaiveFeeIn) -> dict:
    _require_appointment_access(cur, ctx, args.appointment_id)
    try:
        result = front_desk_waive_fee_service(
            cur, args.appointment_id, reason=args.reason, staff_id=ctx.actor_id, hospital_id=ctx.facility_id)
    except svc_exc.AppointmentNotFound as exc:
        raise _not_found("appointment") from exc
    except svc_exc.InvalidStatusTransition as exc:
        raise ToolError("invalid_status", "the fee can only be waived for a Checked-In appointment") from exc
    except svc_exc.PaymentStateConflict as exc:
        raise ToolError("conflict", "the consultation fee is already paid or refunded") from exc
    except svc_exc.WaiverNotEligible as exc:
        raise ToolError("not_eligible", "a waiver requires a completed visit with this doctor in the last 3 days") from exc
    return _settlement(result)


def _waive_precheck(cur, ctx: AuthContext, args: WaiveFeeIn) -> Precheck:
    return _eligibility(_get_appointment(cur, ctx, args.appointment_id), done_status="WAIVED") or Precheck("ok")


def _waive_postchecks(cur, ctx, args: WaiveFeeIn, output: dict) -> list[CheckResult]:
    return _token_and_status_checks(_get_appointment(cur, ctx, args.appointment_id), output, expect_status="WAIVED")


def _waive_preview(cur, ctx, args: WaiveFeeIn) -> dict:
    item = _get_appointment(cur, ctx, args.appointment_id) or {}
    return {"action": "Waive the consultation fee (3-day revisit policy)", "reason": args.reason,
            **(_describe(item) if item else {})}


# ---- appointment.settle_free_visit ------------------------------------------------

class SettleFreeIn(_In):
    appointment_id: int


def _settle_free(cur, ctx: AuthContext, args: SettleFreeIn) -> dict:
    _require_appointment_access(cur, ctx, args.appointment_id)
    try:
        result = front_desk_settle_free_visit_service(cur, args.appointment_id)
    except svc_exc.AppointmentNotFound as exc:
        raise _not_found("appointment") from exc
    except svc_exc.InvalidStatusTransition as exc:
        raise ToolError("invalid_status", "a visit can only be settled as free when Checked-In") from exc
    except svc_exc.PaymentStateConflict as exc:
        raise ToolError("conflict", "the consultation fee is already paid or refunded") from exc
    except svc_exc.FreeVisitNotEligible as exc:
        raise ToolError("not_eligible", "this visit has a fee or extra charges and cannot be settled as free") from exc
    except svc_exc.AppointmentTypeNotAssigned as exc:
        raise ToolError("conflict", "this appointment has no configured consultation fee") from exc
    return _settlement(result)


def _settle_free_precheck(cur, ctx: AuthContext, args: SettleFreeIn) -> Precheck:
    item = _get_appointment(cur, ctx, args.appointment_id)
    stop = _eligibility(item, done_status="WAIVED")
    if stop is not None:
        return stop
    if _amount_due(cur, args.appointment_id) != 0:
        return Precheck("blocked", "this visit has a consultation fee or extra charges; it is not free",
                        {"appointment_id": item["id"], "status": item["status"]})
    return Precheck("ok", "", {"appointment_id": item["id"], "status": item["status"]})


def _settle_free_postchecks(cur, ctx, args: SettleFreeIn, output: dict) -> list[CheckResult]:
    item = _get_appointment(cur, ctx, args.appointment_id)
    checks = _token_and_status_checks(item, output, expect_status="WAIVED")
    checks.append(CheckResult("no_money_recorded", item is not None and (_dec(item["payment_amount"]) or 0) == 0,
                              f"payment_amount re-read: {item['payment_amount'] if item else 'not found'}"))
    return checks


def _settle_free_preview(cur, ctx, args: SettleFreeIn) -> dict:
    item = _get_appointment(cur, ctx, args.appointment_id) or {}
    return {"action": "Settle a zero-fee visit and add the patient to the queue", **(_describe(item) if item else {})}


def register_billing_tools(r: ToolRegistry) -> None:
    def financial(name, description, permissions, in_model, out_model, handler, precheck, postchecks, preview,
                  reference_only=()):
        r.register(Tool(
            name=name, description=description, operation="financial", risk="high", requires_approval=True,
            irreversible=True, idempotency_required=True, required_permissions=permissions,
            input_model=in_model, output_model=out_model, handler=handler, precheck=precheck, postchecks=postchecks,
            audit_resource=lambda a, out: ("appointment", out.get("appointment_id")),
            approval_preview=preview, reference_only_args=reference_only,
        ))

    financial(
        "appointment.record_payment",
        "Record that the patient PAID the consultation fee at the front desk (method CASH/UPI/CARD/OTHER), "
        "for a Checked-In appointment; this issues the queue token. expected_amount MUST be a $from reference "
        "to invoice.get's total_due (read the invoice first). Needs human approval.",
        ("bill.record_payment", "appointment.read"), RecordPaymentIn, SettlementOut, _record_payment,
        _record_payment_precheck, _record_payment_postchecks, _record_payment_preview, ("expected_amount",))
    financial(
        "appointment.waive_consultation_fee",
        "Waive the consultation fee for a Checked-In appointment under the 3-day revisit policy, with a reason; "
        "this issues the queue token. Needs human approval.",
        ("appointment.waive_payment", "appointment.read"), WaiveFeeIn, SettlementOut, _waive,
        _waive_precheck, _waive_postchecks, _waive_preview)
    financial(
        "appointment.settle_free_visit",
        "Settle a Checked-In visit that has NO consultation fee and no extra charges, adding the patient to the "
        "queue. Refused for anything with a charge. Needs human approval.",
        ("bill.record_payment", "appointment.read"), SettleFreeIn, SettlementOut, _settle_free,
        _settle_free_precheck, _settle_free_postchecks, _settle_free_preview)
