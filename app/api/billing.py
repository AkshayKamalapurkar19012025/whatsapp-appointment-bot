"""
Billing endpoints (OPD/HIMS master spec Phase 9): the new invoice/
charge/payment model (app/services/billing_services.py), nested under
/appointments/{appointment_id}/bill -- same nesting convention as
clinical/orders/prescription, but deliberately "bill", not "invoice":
app/api/appointments.py already owns GET /{appointment_id}/invoice for
the existing consultation-fee bill (migrations/0026's invoice_line_
items), mounted on this same /appointments prefix -- reusing "invoice"
here would silently shadow one of the two routes (FastAPI matches the
first-registered router for an identical path+method, so the older,
already-mounted one would always win and this new one would never be
reached; caught by this phase's own tests hitting the wrong response
shape before this was ever committed). RBAC mirrors the existing
appointment billing endpoints exactly: recording a payment and viewing
the bill are STAFF-permitted (day-to-day cashier work), everything that
changes what's owed or corrects a financial record after the fact --
adding/voiding a charge, setting discount/tax, voiding a payment, a
refund, voiding the whole bill -- is ADMIN-only, the same tier as
add_appointment_invoice_line_item/waive-payment/record-refund already
use.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.staff_auth import get_current_staff, require_permission
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.billing_services import (
    get_invoice_summary_service,
    update_invoice_terms_service,
    void_invoice_service,
    list_unbilled_sources_service,
    add_charge_service,
    void_charge_service,
    record_invoice_payment_service,
    void_invoice_payment_service,
    refund_invoice_payment_service,
    get_payment_receipt_service,
)
from app.services.notifications import KIND_RECEIPT, send_mock_notification

router = APIRouter(prefix="/appointments", tags=["Billing"])


class ChargeCreate(BaseModel):
    description: str = Field(min_length=1)
    amount: float = Field(gt=0)
    source_type: Literal[
        "CONSULTATION", "LAB", "RADIOLOGY", "PROCEDURE", "SERVICE", "PHARMACY", "PACKAGE", "CONSUMABLES", "OTHER"
    ] = "OTHER"
    source_order_id: int | None = None
    source_dispense_id: int | None = None
    source_package_id: int | None = None


class VoidRequest(BaseModel):
    reason: str = Field(min_length=1)


class InvoiceTermsUpdate(BaseModel):
    discount_amount: float | None = Field(default=None, ge=0)
    discount_reason: str | None = None
    tax_rate: float | None = Field(default=None, ge=0, le=100)
    bill_type: Literal["CASH", "SELF_PAY", "CORPORATE", "INSURANCE", "TPA", "GOVERNMENT_SCHEME"] | None = None


class PaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    method: Literal["CASH", "UPI", "CARD", "BANK_TRANSFER", "INSURANCE", "OTHER"]
    transaction_id: str | None = None


class RefundCreate(BaseModel):
    amount: float = Field(gt=0)
    reason: str = Field(min_length=1)


def _not_found(detail: str):
    return HTTPException(status_code=404, detail=detail)


@router.get("/{appointment_id}/bill")
def get_invoice(appointment_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return get_invoice_summary_service(cur, appointment_id, staff_id=staff["id"])
            except svc_exc.AppointmentNotFound:
                raise _not_found("Appointment not found")
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")


@router.get("/{appointment_id}/bill/unbilled")
def get_unbilled_sources(appointment_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return list_unbilled_sources_service(cur, appointment_id)
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")


@router.patch("/{appointment_id}/bill")
def update_invoice_terms(
    appointment_id: int,
    body: InvoiceTermsUpdate,
    admin: dict = Depends(require_permission("bill.update_terms")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = update_invoice_terms_service(cur, appointment_id, staff_id=admin["id"], **body.model_dump())
            except svc_exc.AppointmentNotFound:
                raise _not_found("Appointment not found")
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")
            except svc_exc.InvoiceVoided:
                raise HTTPException(status_code=409, detail="This invoice has been voided")
    return result


@router.post("/{appointment_id}/bill/void")
def void_invoice(appointment_id: int, body: VoidRequest, admin: dict = Depends(require_permission("bill.void"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = void_invoice_service(cur, appointment_id, staff_id=admin["id"], reason=body.reason)
            except svc_exc.InvoiceNotFound:
                raise _not_found("No invoice exists for this appointment")
            except svc_exc.InvoiceVoided:
                raise HTTPException(status_code=409, detail="This invoice is already voided")
            except svc_exc.InvoiceNotVoidable:
                raise HTTPException(
                    status_code=409,
                    detail="This invoice has payments recorded against it and can no longer be voided as a whole",
                )
    return result


@router.post("/{appointment_id}/bill/charges")
def add_charge(
    appointment_id: int,
    body: ChargeCreate,
    admin: dict = Depends(require_permission("bill.add_charge")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = add_charge_service(cur, appointment_id, staff_id=admin["id"], **body.model_dump())
            except svc_exc.AppointmentNotFound:
                raise _not_found("Appointment not found")
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")
            except svc_exc.InvoiceVoided:
                raise HTTPException(status_code=409, detail="This invoice has been voided")
            except svc_exc.InvalidChargeSource:
                raise HTTPException(
                    status_code=422,
                    detail="That order or dispense doesn't belong to this patient's visit",
                )
            except svc_exc.PackageNotFound:
                raise _not_found("Package not found")
            except svc_exc.DuplicateCharge:
                raise HTTPException(status_code=409, detail="That order or dispense has already been billed")
    return result


@router.post("/{appointment_id}/bill/charges/{charge_id}/void")
def void_charge(
    appointment_id: int,
    charge_id: int,
    body: VoidRequest,
    admin: dict = Depends(require_permission("bill.void_charge")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = void_charge_service(cur, appointment_id, charge_id, staff_id=admin["id"], reason=body.reason)
            except svc_exc.InvoiceNotFound:
                raise _not_found("No invoice exists for this appointment")
            except svc_exc.ChargeNotFound:
                raise _not_found("Charge not found")
            except svc_exc.ChargeAlreadyVoided:
                raise HTTPException(status_code=409, detail="This charge is already voided")
    return result


@router.post("/{appointment_id}/bill/payments")
def record_payment(
    appointment_id: int,
    body: PaymentCreate,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = record_invoice_payment_service(cur, appointment_id, staff_id=staff["id"], **body.model_dump())
            except svc_exc.InvoiceNotFound:
                raise _not_found("No invoice exists for this appointment")
            except svc_exc.InvoiceVoided:
                raise HTTPException(status_code=409, detail="This invoice has been voided")
            except svc_exc.PaymentExceedsBalance:
                raise HTTPException(status_code=422, detail="Payment amount exceeds the outstanding balance")
            except svc_exc.DuplicateTransactionId:
                raise HTTPException(
                    status_code=409,
                    detail="A payment with this transaction ID has already been recorded",
                )
    return result


@router.post("/{appointment_id}/bill/payments/{payment_id}/void")
def void_payment(
    appointment_id: int,
    payment_id: int,
    body: VoidRequest,
    admin: dict = Depends(require_permission("bill.void_payment")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = void_invoice_payment_service(
                    cur, appointment_id, payment_id, staff_id=admin["id"], reason=body.reason
                )
            except svc_exc.InvoiceNotFound:
                raise _not_found("No invoice exists for this appointment")
            except svc_exc.PaymentNotFound:
                raise _not_found("Payment not found")
            except svc_exc.PaymentAlreadyVoided:
                raise HTTPException(status_code=409, detail="This payment is already voided")
    return result


@router.post("/{appointment_id}/bill/payments/{payment_id}/refund")
def refund_payment(
    appointment_id: int,
    payment_id: int,
    body: RefundCreate,
    admin: dict = Depends(require_permission("bill.refund_payment")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = refund_invoice_payment_service(
                    cur, appointment_id, payment_id, staff_id=admin["id"], amount=body.amount, reason=body.reason
                )
            except svc_exc.InvoiceNotFound:
                raise _not_found("No invoice exists for this appointment")
            except svc_exc.PaymentNotFound:
                raise _not_found("Payment not found")
            except svc_exc.PaymentAlreadyVoided:
                raise HTTPException(status_code=409, detail="This payment is voided, nothing to refund")
            except svc_exc.PaymentRefundExceedsAmount:
                raise HTTPException(status_code=422, detail="Refund amount exceeds what's left to refund")
    return result


# ---------------------------------------------------------------------
# Receipt (master spec section 42) -- bare-staff, same tier as viewing
# the bill and recording a payment: reading or (re-)sending a receipt
# changes no financial state, unlike everything ADMIN-gated above.
# ---------------------------------------------------------------------


@router.get("/{appointment_id}/bill/payments/{payment_id}/receipt")
def get_payment_receipt(
    appointment_id: int,
    payment_id: int,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return get_payment_receipt_service(cur, appointment_id, payment_id)
            except svc_exc.InvoiceNotFound:
                raise _not_found("No invoice exists for this appointment")
            except svc_exc.PaymentNotFound:
                raise _not_found("Payment not found")


@router.post("/{appointment_id}/bill/payments/{payment_id}/receipt/send")
def send_payment_receipt(
    appointment_id: int,
    payment_id: int,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                receipt = get_payment_receipt_service(cur, appointment_id, payment_id)
            except svc_exc.InvoiceNotFound:
                raise _not_found("No invoice exists for this appointment")
            except svc_exc.PaymentNotFound:
                raise _not_found("Payment not found")

            cur.execute(
                """
                SELECT p.whatsapp_number
                FROM encounters e
                JOIN patients p ON p.id = e.patient_id
                WHERE e.id = %s
                """,
                (receipt["encounter_id"],),
            )
            (whatsapp_number,) = cur.fetchone()

            send_mock_notification(
                cur,
                whatsapp_number,
                KIND_RECEIPT,
                f"Receipt {receipt['receipt_number']} from {receipt['hospital_name']}: "
                f"₹{receipt['payment_amount']:.2f} received via {receipt['payment_method']} "
                f"for {receipt['patient_name']} (Bill {receipt['invoice_number']}). Thank you.",
            )

    return {"sent": True}
