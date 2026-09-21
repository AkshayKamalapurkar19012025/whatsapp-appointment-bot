"""
Prescription + Pharmacy endpoints (OPD/HIMS master spec Phase 8). Two
routers: prescription_router follows the existing /appointments/{id}/...
convention (same as app/api/clinical.py, app/api/orders.py);
pharmacy_router is a new top-level /pharmacy/... surface, since the
queue/stock/dispense actions are cross-patient -- a pharmacist works
from the queue, not from one patient's chart. Every endpoint requires
an authenticated staff session; creating a stock batch is ADMIN-only
(same tier as recurring schedule management -- an inventory/pricing
change, not a day-to-day operational action), everything else accepts
ADMIN or STAFF, matching the RBAC split every other operational action
in this app already uses.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.staff_auth import get_current_staff, require_permission
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.pharmacy_services import (
    get_or_create_prescription_service,
    add_prescription_item_service,
    remove_prescription_item_service,
    prescribe_service,
    cancel_prescription_service,
    list_pharmacy_queue_service,
    list_pharmacy_stock_service,
    create_pharmacy_stock_service,
    record_dispense_service,
)

prescription_router = APIRouter(prefix="/appointments", tags=["Prescription"])
pharmacy_router = APIRouter(prefix="/pharmacy", tags=["Pharmacy"])


class PrescriptionItemCreate(BaseModel):
    medicine_name: str = Field(min_length=1)
    generic_name: str | None = None
    dosage: str | None = None
    route: str | None = None
    frequency: str | None = None
    duration: str | None = None
    quantity: int = Field(gt=0)
    food_instructions: str | None = None
    special_instructions: str | None = None


class PrescriptionCancel(BaseModel):
    reason: str = Field(min_length=1)


class StockCreate(BaseModel):
    medicine_name: str = Field(min_length=1)
    batch_number: str = Field(min_length=1)
    expiry_date: date
    quantity_on_hand: int = Field(ge=0)
    unit_price: float = Field(default=0, ge=0)


class DispenseCreate(BaseModel):
    quantity: int = Field(gt=0)
    pharmacy_stock_id: int | None = None
    unit_price: float | None = Field(default=None, ge=0)


def _not_found(detail: str):
    return HTTPException(status_code=404, detail=detail)


def _encounter_closed():
    return HTTPException(
        status_code=409,
        detail="Patient is not currently checked in -- the prescription can only be edited while the visit is in progress",
    )


@prescription_router.get("/{appointment_id}/prescription")
def get_prescription(appointment_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return get_or_create_prescription_service(cur, appointment_id, staff_id=staff["id"])
            except svc_exc.AppointmentNotFound:
                raise _not_found("Appointment not found")
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")
            except svc_exc.EncounterClosed:
                raise _encounter_closed()


@prescription_router.post("/{appointment_id}/prescription/items")
def add_prescription_item(
    appointment_id: int,
    body: PrescriptionItemCreate,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = add_prescription_item_service(
                    cur, appointment_id, staff_id=staff["id"], **body.model_dump()
                )
            except svc_exc.AppointmentNotFound:
                raise _not_found("Appointment not found")
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")
            except svc_exc.EncounterClosed:
                raise _encounter_closed()
            except svc_exc.PrescriptionAlreadyPrescribed:
                raise HTTPException(
                    status_code=409,
                    detail="This prescription has already been sent to pharmacy and can no longer be edited",
                )
    return result


@prescription_router.delete("/{appointment_id}/prescription/items/{item_id}")
def remove_prescription_item(
    appointment_id: int,
    item_id: int,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = remove_prescription_item_service(cur, appointment_id, item_id, staff_id=staff["id"])
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")
            except svc_exc.PrescriptionNotFound:
                raise _not_found("No prescription exists for this appointment")
            except svc_exc.PrescriptionAlreadyPrescribed:
                raise HTTPException(
                    status_code=409,
                    detail="This prescription has already been sent to pharmacy and can no longer be edited",
                )
            except svc_exc.PrescriptionItemNotFound:
                raise _not_found("Prescription item not found")
    return result


@prescription_router.post("/{appointment_id}/prescription/prescribe")
def prescribe(appointment_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = prescribe_service(cur, appointment_id, staff_id=staff["id"])
            except svc_exc.AppointmentNotFound:
                raise _not_found("Appointment not found")
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")
            except svc_exc.PrescriptionNotFound:
                raise _not_found("No prescription exists for this appointment")
            except svc_exc.PrescriptionAlreadyPrescribed:
                raise HTTPException(status_code=409, detail="This prescription has already been sent to pharmacy")
            except svc_exc.EncounterClosed:
                raise _encounter_closed()
            except svc_exc.PrescriptionEmpty:
                raise HTTPException(status_code=422, detail="Add at least one medicine before sending to pharmacy")
    return result


@prescription_router.post("/{appointment_id}/prescription/cancel")
def cancel_prescription(
    appointment_id: int,
    body: PrescriptionCancel,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = cancel_prescription_service(
                    cur, appointment_id, staff_id=staff["id"], reason=body.reason
                )
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")
            except svc_exc.PrescriptionNotFound:
                raise _not_found("No prescription exists for this appointment")
            except svc_exc.PrescriptionNotCancellable:
                raise HTTPException(
                    status_code=409,
                    detail="This prescription can no longer be cancelled -- it is not pending, or medicine has already been dispensed against it",
                )
    return result


@pharmacy_router.get("/queue")
def get_pharmacy_queue(staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_pharmacy_queue_service(cur)


@pharmacy_router.get("/stock")
def get_pharmacy_stock(medicine_name: str | None = None, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_pharmacy_stock_service(cur, medicine_name=medicine_name)


@pharmacy_router.post("/stock")
def create_pharmacy_stock(body: StockCreate, admin: dict = Depends(require_permission("pharmacy.manage_stock"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = create_pharmacy_stock_service(cur, staff_id=admin["id"], **body.model_dump())
            except svc_exc.DuplicateStockBatch:
                raise HTTPException(
                    status_code=409,
                    detail="A stock batch with this medicine name and batch number already exists",
                )
    return result


@pharmacy_router.post("/items/{item_id}/dispense")
def dispense_prescription_item(
    item_id: int,
    body: DispenseCreate,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = record_dispense_service(cur, item_id, staff_id=staff["id"], **body.model_dump())
            except svc_exc.PrescriptionItemNotFound:
                raise _not_found("Prescription item not found")
            except svc_exc.PrescriptionItemNotDispensable:
                raise HTTPException(
                    status_code=409,
                    detail="This prescription hasn't been sent to pharmacy, or is cancelled",
                )
            except svc_exc.DispenseQuantityExceedsRemaining:
                raise HTTPException(
                    status_code=422,
                    detail="Dispense quantity exceeds what's left to dispense for this item",
                )
            except svc_exc.PharmacyStockNotFound:
                raise _not_found("Stock batch not found")
            except svc_exc.MedicineMismatch:
                raise HTTPException(
                    status_code=422,
                    detail="That stock batch is for a different medicine than what was prescribed",
                )
            except svc_exc.InsufficientStock:
                raise HTTPException(status_code=409, detail="Not enough stock on hand in that batch")
    return result
