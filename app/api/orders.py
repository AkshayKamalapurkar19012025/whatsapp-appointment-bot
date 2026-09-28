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

Phase 7 resumed (migrations/0054_diagnostic_workflow.sql): sample
collection/rejection, marking an order in-progress, verification, and
release are gated by order.collect/order.verify/order.release
respectively -- same ADMIN/STAFF/DOCTOR/LAB_TECH grant set as
order.result, since this app has no distinct
phlebotomist/pathologist/senior-verifier role to gate more narrowly
against (confirmed by the source-of-truth audit). These five new
routes only apply to LAB/RADIOLOGY orders -- PROCEDURE/SERVICE/
EXTERNAL_REFERRAL orders 404 with a clear "not a diagnostic order" if
called against them (DiagnosticActionNotSupportedForOrderType).

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
    mark_order_in_progress_service,
    record_order_result_service,
    record_sample_collection_service,
    reject_sample_service,
    release_order_result_service,
    verify_order_result_service,
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


class SampleCollect(BaseModel):
    sample_type: str = Field(min_length=1)
    notes: str | None = None


class SampleReject(BaseModel):
    reason: str = Field(min_length=1)


def _diagnostic_error_response(exc: Exception):
    if isinstance(exc, svc_exc.DiagnosticActionNotSupportedForOrderType):
        return HTTPException(
            status_code=422,
            detail="This action only applies to LAB/RADIOLOGY orders",
        )
    if isinstance(exc, svc_exc.OrderNotCollectible):
        return HTTPException(
            status_code=409,
            detail="A sample can only be collected while the order is ORDERED, and rejected while it is COLLECTED",
        )
    if isinstance(exc, svc_exc.OrderNotStartable):
        return HTTPException(
            status_code=409,
            detail="This order isn't ready to be marked in-progress",
        )
    if isinstance(exc, svc_exc.OrderNotVerifiable):
        return HTTPException(
            status_code=409,
            detail="Only a drafted (RESULT_ENTERED) result can be verified",
        )
    if isinstance(exc, svc_exc.OrderNotReleasable):
        return HTTPException(
            status_code=409,
            detail="Only a verified (VERIFIED) result can be released",
        )
    if isinstance(exc, svc_exc.SameStaffCannotVerifyOwnResult):
        return HTTPException(
            status_code=403,
            detail="The staff account that entered this result cannot also verify it",
        )
    if isinstance(exc, svc_exc.SampleNotFound):
        return HTTPException(status_code=404, detail="Sample not found")
    if isinstance(exc, svc_exc.SampleAlreadyRejected):
        return HTTPException(status_code=409, detail="This sample was already rejected")
    return None


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
                    hospital_id=staff["hospital_id"],
                    **body.model_dump(),
                )
            except svc_exc.ModuleUnavailable:
                raise HTTPException(
                    status_code=403,
                    detail="The Lab/Radiology module isn't enabled for this hospital -- use External Referral instead",
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
                    detail="This order already has a drafted, verified, completed, or cancelled result",
                )

            # Master spec section 15's "lab result available" event no
            # longer fires here. PROCEDURE/SERVICE/EXTERNAL_REFERRAL
            # never fired it (only LAB/RADIOLOGY did, before Phase 7).
            # LAB/RADIOLOGY now only reach COMPLETED via
            # release_order_result below, once verified -- that's where
            # this notification fires for them instead, so a doctor is
            # never notified about a result nobody has verified yet.
    return result


def _notify_result_available(cur, *, hospital_id: int, appointment_id: int, result: dict):
    cur.execute(
        "SELECT name FROM patients WHERE id = (SELECT patient_id FROM appointments WHERE id = %s)",
        (appointment_id,),
    )
    (patient_name,) = cur.fetchone()
    create_notification(
        cur,
        hospital_id=hospital_id,
        kind="LAB_RESULT_AVAILABLE",
        message=f"{result['order_type'].title()} result available for {patient_name}: {result['description']}",
        appointment_id=appointment_id,
    )


@router.post("/{appointment_id}/orders/{order_id}/collect-sample")
def collect_sample(
    appointment_id: int,
    order_id: int,
    body: SampleCollect,
    staff: dict = Depends(require_permission("order.collect")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = record_sample_collection_service(
                    cur, appointment_id, order_id,
                    staff_id=staff["id"], sample_type=body.sample_type, notes=body.notes,
                )
            except svc_exc.EncounterNotFound:
                raise HTTPException(status_code=404, detail="No encounter exists for this appointment")
            except svc_exc.OrderNotFound:
                raise HTTPException(status_code=404, detail="Order not found")
            except (svc_exc.DiagnosticActionNotSupportedForOrderType, svc_exc.OrderNotCollectible) as exc:
                raise _diagnostic_error_response(exc)
    return result


@router.post("/{appointment_id}/orders/{order_id}/samples/{sample_id}/reject")
def reject_sample(
    appointment_id: int,
    order_id: int,
    sample_id: int,
    body: SampleReject,
    staff: dict = Depends(require_permission("order.collect")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = reject_sample_service(
                    cur, appointment_id, order_id, sample_id,
                    staff_id=staff["id"], reason=body.reason,
                )
            except svc_exc.EncounterNotFound:
                raise HTTPException(status_code=404, detail="No encounter exists for this appointment")
            except svc_exc.OrderNotFound:
                raise HTTPException(status_code=404, detail="Order not found")
            except (
                svc_exc.DiagnosticActionNotSupportedForOrderType,
                svc_exc.OrderNotCollectible,
                svc_exc.SampleNotFound,
                svc_exc.SampleAlreadyRejected,
            ) as exc:
                raise _diagnostic_error_response(exc)
    return result


@router.post("/{appointment_id}/orders/{order_id}/start-processing")
def start_order_processing(
    appointment_id: int,
    order_id: int,
    staff: dict = Depends(require_permission("order.collect")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = mark_order_in_progress_service(cur, appointment_id, order_id, staff_id=staff["id"])
            except svc_exc.EncounterNotFound:
                raise HTTPException(status_code=404, detail="No encounter exists for this appointment")
            except svc_exc.OrderNotFound:
                raise HTTPException(status_code=404, detail="Order not found")
            except (svc_exc.DiagnosticActionNotSupportedForOrderType, svc_exc.OrderNotStartable) as exc:
                raise _diagnostic_error_response(exc)
    return result


@router.post("/{appointment_id}/orders/{order_id}/verify")
def verify_order_result(
    appointment_id: int,
    order_id: int,
    staff: dict = Depends(require_permission("order.verify")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = verify_order_result_service(
                    cur, appointment_id, order_id,
                    staff_id=staff["id"], staff_role=staff["role"],
                )
            except svc_exc.EncounterNotFound:
                raise HTTPException(status_code=404, detail="No encounter exists for this appointment")
            except svc_exc.OrderNotFound:
                raise HTTPException(status_code=404, detail="Order not found")
            except (
                svc_exc.DiagnosticActionNotSupportedForOrderType,
                svc_exc.OrderNotVerifiable,
                svc_exc.SameStaffCannotVerifyOwnResult,
            ) as exc:
                raise _diagnostic_error_response(exc)
    return result


@router.post("/{appointment_id}/orders/{order_id}/release")
def release_order_result(
    appointment_id: int,
    order_id: int,
    staff: dict = Depends(require_permission("order.release")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = release_order_result_service(cur, appointment_id, order_id, staff_id=staff["id"])
            except svc_exc.EncounterNotFound:
                raise HTTPException(status_code=404, detail="No encounter exists for this appointment")
            except svc_exc.OrderNotFound:
                raise HTTPException(status_code=404, detail="Order not found")
            except (svc_exc.DiagnosticActionNotSupportedForOrderType, svc_exc.OrderNotReleasable) as exc:
                raise _diagnostic_error_response(exc)

            # Master spec section 15's "lab result available" event --
            # fires here, not at result-entry time, so a doctor is
            # never notified about an unverified draft (see
            # record_order_result's own comment above).
            _notify_result_available(
                cur, hospital_id=staff["hospital_id"], appointment_id=appointment_id, result=result
            )
    return result
