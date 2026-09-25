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
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.staff_auth import get_current_staff, require_permission
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.audit_log import record_audit_log
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
from app.services.medication_services import (
    list_medications_service,
    create_medication_service,
    update_medication_service,
    set_medication_active_service,
)
from app.services.notification_center_service import create_notification

prescription_router = APIRouter(prefix="/appointments", tags=["Prescription"])
pharmacy_router = APIRouter(prefix="/pharmacy", tags=["Pharmacy"])

# P0 clinical safety audit actions (app/services/allergy_check_service.py),
# named consistently with every other action this app writes to audit_log
# (e.g. "appointment.waive_payment") rather than a one-off format.
_ALLERGY_AUDIT_ACTION_BY_OUTCOME = {
    "warning_shown": "prescription.allergy_warning_shown",
    "cancelled": "prescription.allergy_warning_cancelled",
    "overridden": "prescription.allergy_warning_overridden",
}


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
    # Phase 5 (migrations/0054_medication_master.sql): optional, set
    # when the clinician picked a Medication Master search result.
    # medicine_name/generic_name above stay required either way -- see
    # add_prescription_item_service's own comment on why.
    medication_id: int | None = None
    # P0 clinical safety: None on the clinician's first attempt. Set to
    # "continue"/"cancel" only when resubmitting after seeing an allergy
    # warning -- see app/services/pharmacy_services.py's
    # add_prescription_item_service docstring.
    allergy_decision: Literal["continue", "cancel"] | None = None


class PrescriptionCancel(BaseModel):
    reason: str = Field(min_length=1)


class StockCreate(BaseModel):
    medicine_name: str = Field(min_length=1)
    batch_number: str = Field(min_length=1)
    expiry_date: date
    quantity_on_hand: int = Field(ge=0)
    unit_price: float = Field(default=0, ge=0)
    # Phase 5: optional Medication Master link, same as above.
    medication_id: int | None = None


class DispenseCreate(BaseModel):
    quantity: int = Field(gt=0)
    pharmacy_stock_id: int | None = None
    unit_price: float | None = Field(default=None, ge=0)


class MedicationCreate(BaseModel):
    generic_name: str = Field(min_length=1)
    brand_name: str | None = None
    strength: str | None = None
    dosage_form: str | None = None
    default_route: str | None = None


class MedicationActiveUpdate(BaseModel):
    active: bool


def _module_unavailable():
    return HTTPException(
        status_code=403,
        detail="The Pharmacy module isn't enabled for this hospital",
    )


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
    staff: dict = Depends(require_permission("prescription.create")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = add_prescription_item_service(
                    cur, appointment_id, staff_id=staff["id"], hospital_id=staff["hospital_id"], **body.model_dump()
                )
            except svc_exc.ModuleUnavailable:
                raise _module_unavailable()
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
            except svc_exc.MedicationNotFound:
                raise _not_found("Medication not found")
            except svc_exc.MedicationInactive:
                raise HTTPException(status_code=409, detail="This medication is no longer active")

            action = _ALLERGY_AUDIT_ACTION_BY_OUTCOME.get(result["outcome"])
            if action is not None:
                details = {"medicine_name": body.medicine_name}
                conflicts = (result.get("allergy_warning") or {}).get("conflicts") or result.get(
                    "overridden_conflicts"
                )
                if conflicts:
                    details["conflicts"] = conflicts
                record_audit_log(
                    cur,
                    hospital_id=staff["hospital_id"],
                    staff_id=staff["id"],
                    action=action,
                    resource_type="prescription",
                    resource_id=result["prescription"]["id"],
                    details=details,
                )

    return {"prescription": result["prescription"], "allergy_warning": result["allergy_warning"]}


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
def prescribe(appointment_id: int, staff: dict = Depends(require_permission("prescription.create"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = prescribe_service(cur, appointment_id, staff_id=staff["id"], hospital_id=staff["hospital_id"])
            except svc_exc.ModuleUnavailable:
                raise _module_unavailable()
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
def get_pharmacy_stock(
    medicine_name: str | None = None,
    medication_id: int | None = None,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_pharmacy_stock_service(cur, medicine_name=medicine_name, medication_id=medication_id)


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
            except svc_exc.MedicationNotFound:
                raise _not_found("Medication not found")
            except svc_exc.MedicationInactive:
                raise HTTPException(status_code=409, detail="This medication is no longer active")
    return result


# ---------------------------------------------------------------------
# Medication Master (Phase 5, migrations/0054_medication_master.sql)
# ---------------------------------------------------------------------


@pharmacy_router.get("/medications")
def get_medications(
    search: str | None = None,
    include_inactive: bool = False,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_medications_service(cur, search=search, active_only=not include_inactive)


@pharmacy_router.post("/medications")
def create_medication(body: MedicationCreate, admin: dict = Depends(require_permission("pharmacy.manage_stock"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return create_medication_service(cur, staff_id=admin["id"], **body.model_dump())
            except svc_exc.DuplicateMedication:
                raise HTTPException(
                    status_code=409,
                    detail="A medication with this generic name, brand, strength, and form already exists",
                )


@pharmacy_router.patch("/medications/{medication_id}")
def update_medication(
    medication_id: int,
    body: MedicationCreate,
    admin: dict = Depends(require_permission("pharmacy.manage_stock")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return update_medication_service(cur, medication_id, **body.model_dump())
            except svc_exc.MedicationNotFound:
                raise _not_found("Medication not found")
            except svc_exc.DuplicateMedication:
                raise HTTPException(
                    status_code=409,
                    detail="A medication with this generic name, brand, strength, and form already exists",
                )


@pharmacy_router.patch("/medications/{medication_id}/active")
def update_medication_active(
    medication_id: int,
    body: MedicationActiveUpdate,
    admin: dict = Depends(require_permission("pharmacy.manage_stock")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return set_medication_active_service(cur, medication_id, active=body.active)
            except svc_exc.MedicationNotFound:
                raise _not_found("Medication not found")


@pharmacy_router.post("/items/{item_id}/dispense")
def dispense_prescription_item(
    item_id: int,
    body: DispenseCreate,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = record_dispense_service(
                    cur, item_id, staff_id=staff["id"], hospital_id=staff["hospital_id"], **body.model_dump()
                )
            except svc_exc.ModuleUnavailable:
                raise _module_unavailable()
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

            # Master spec section 15's "prescription ready" event --
            # fires once, exactly when the last still-outstanding item
            # on this prescription is fully dispensed (not on every
            # partial dispense along the way).
            cur.execute(
                "SELECT NOT EXISTS (SELECT 1 FROM prescription_items WHERE prescription_id = %s AND quantity_dispensed < quantity)",
                (result["prescription_id"],),
            )
            (fully_dispensed,) = cur.fetchone()
            if fully_dispensed:
                cur.execute(
                    """
                    SELECT a.id, p.name
                    FROM prescriptions pr
                    JOIN encounters e ON e.id = pr.encounter_id
                    JOIN patients p ON p.id = e.patient_id
                    JOIN appointments a ON a.encounter_id = e.id
                    WHERE pr.id = %s
                    """,
                    (result["prescription_id"],),
                )
                appointment_id, patient_name = cur.fetchone()
                create_notification(
                    cur,
                    hospital_id=staff["hospital_id"],
                    kind="PRESCRIPTION_READY",
                    message=f"Prescription ready for {patient_name}",
                    appointment_id=appointment_id,
                )
    return result
