"""
Order spine endpoints (OPD/HIMS master spec Phase 6). Thin wrappers
around app/services/order_services.py, nested under
/appointments/{appointment_id}/orders -- same convention as
app/api/clinical.py. Creating an order is gated by order.create
(migrations/0048_clinical_rbac_permissions.sql, DOCTOR/STAFF/ADMIN);
recording a result is gated by order.result (migrations/0051, adds
LAB_TECH -- the Lab/Radiology Worklist screen's whole reason to exist).
Listing and cancelling stay on bare get_current_staff, still out of
scope for either migration.

worklist_router (prefix /orders, not /appointments/{id}/orders) is the
one cross-patient endpoint here: every other route in this file is
scoped to a single appointment's own encounter.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.staff_auth import get_current_staff, require_permission
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.order_services import (
    create_order_service,
    list_orders_service,
    list_worklist_orders_service,
    cancel_order_service,
    record_order_result_service,
)
from app.services.notification_center_service import create_notification

router = APIRouter(
    prefix="/appointments",
    tags=["Orders"],
)

worklist_router = APIRouter(
    prefix="/orders",
    tags=["Orders"],
)


class OrderCreate(BaseModel):
    order_type: Literal["LAB", "RADIOLOGY", "PROCEDURE", "SERVICE", "EXTERNAL_REFERRAL"]
    description: str = Field(min_length=1)
    clinical_indication: str | None = None
    priority: Literal["ROUTINE", "URGENT", "STAT"] = "ROUTINE"
    external_destination: str | None = None


class OrderCancel(BaseModel):
    reason: str = Field(min_length=1)


class OrderResultItem(BaseModel):
    parameter: str = Field(min_length=1)
    result_value: str = Field(min_length=1)
    unit: str | None = None
    reference_range: str | None = None
    is_abnormal: bool = False
    is_critical: bool = False


class OrderResultCreate(BaseModel):
    items: list[OrderResultItem] = Field(min_length=1)


@router.get("/{appointment_id}/orders")
def get_orders(appointment_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return list_orders_service(cur, appointment_id)
            except svc_exc.EncounterNotFound:
                raise HTTPException(status_code=404, detail="No encounter exists for this appointment")


@router.post("/{appointment_id}/orders")
def create_order(
    appointment_id: int,
    body: OrderCreate,
    staff: dict = Depends(require_permission("order.create")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = create_order_service(
                    cur,
                    appointment_id,
                    staff_id=staff["id"],
                    **body.model_dump(),
                )
            except svc_exc.AppointmentNotFound:
                raise HTTPException(status_code=404, detail="Appointment not found")
            except svc_exc.EncounterNotFound:
                raise HTTPException(status_code=404, detail="No encounter exists for this appointment")
            except svc_exc.EncounterClosed:
                raise HTTPException(
                    status_code=409,
                    detail="Patient is not currently checked in -- orders can only be created while the visit is in progress",
                )
            except svc_exc.ExternalReferralDestinationRequired:
                raise HTTPException(
                    status_code=422,
                    detail="A destination is required for an external referral",
                )
    return result


@router.post("/{appointment_id}/orders/{order_id}/cancel")
def cancel_order(
    appointment_id: int,
    order_id: int,
    body: OrderCancel,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = cancel_order_service(
                    cur,
                    appointment_id,
                    order_id,
                    staff_id=staff["id"],
                    reason=body.reason,
                )
            except svc_exc.EncounterNotFound:
                raise HTTPException(status_code=404, detail="No encounter exists for this appointment")
            except svc_exc.OrderNotFound:
                raise HTTPException(status_code=404, detail="Order not found")
            except svc_exc.OrderNotCancellable:
                raise HTTPException(
                    status_code=409,
                    detail="This order is already completed or cancelled",
                )
    return result


@worklist_router.get("/worklist")
def get_worklist(
    order_type: Literal["LAB", "RADIOLOGY", "PROCEDURE", "SERVICE", "EXTERNAL_REFERRAL"] | None = None,
    status: Literal["ORDERED", "IN_PROGRESS", "COMPLETED", "CANCELLED"] | None = None,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_worklist_orders_service(
                cur, staff["hospital_id"], order_type=order_type, status=status
            )


@router.post("/{appointment_id}/orders/{order_id}/result")
def record_order_result(
    appointment_id: int,
    order_id: int,
    body: OrderResultCreate,
    staff: dict = Depends(require_permission("order.result")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = record_order_result_service(
                    cur,
                    appointment_id,
                    order_id,
                    staff_id=staff["id"],
                    items=[item.model_dump() for item in body.items],
                )
            except svc_exc.EncounterNotFound:
                raise HTTPException(status_code=404, detail="No encounter exists for this appointment")
            except svc_exc.OrderNotFound:
                raise HTTPException(status_code=404, detail="Order not found")
            except svc_exc.OrderNotResultable:
                raise HTTPException(
                    status_code=409,
                    detail="This order is already completed or cancelled",
                )

            # Master spec section 15's "lab result available" event --
            # only LAB/RADIOLOGY, not every order type (a PROCEDURE/
            # SERVICE/EXTERNAL_REFERRAL "result" isn't a report someone
            # is waiting to review the way a lab/imaging one is).
            if result["order_type"] in ("LAB", "RADIOLOGY"):
                cur.execute(
                    "SELECT name FROM patients WHERE id = (SELECT patient_id FROM appointments WHERE id = %s)",
                    (appointment_id,),
                )
                (patient_name,) = cur.fetchone()
                create_notification(
                    cur,
                    hospital_id=staff["hospital_id"],
                    kind="LAB_RESULT_AVAILABLE",
                    message=f"{result['order_type'].title()} result available for {patient_name}: {result['description']}",
                    appointment_id=appointment_id,
                )
    return result
