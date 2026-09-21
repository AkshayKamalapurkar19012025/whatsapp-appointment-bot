"""
Order spine endpoints (OPD/HIMS master spec Phase 6). Thin wrappers
around app/services/order_services.py, nested under
/appointments/{appointment_id}/orders -- same convention as
app/api/clinical.py. Every endpoint requires an authenticated staff
session (see app/api/clinical.py's module docstring for why there's no
separate DOCTOR role gating this differently yet).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.order_services import (
    create_order_service,
    list_orders_service,
    cancel_order_service,
)

router = APIRouter(
    prefix="/appointments",
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
    staff: dict = Depends(get_current_staff),
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
