"""
Triage/vitals and doctor consultation (OPD/HIMS master spec Phase 5).
Thin wrappers around app/services/clinical_services.py, nested under
/appointments/{appointment_id}/... -- same URL convention every other
appointment-scoped action in this app already uses (confirm/reject/
visit/complete/reschedule, the billing endpoints), rather than a new
/encounters/{id}/... surface the frontend would need a second lookup to
reach.

Recording vitals and writing/completing a consultation are gated by
vitals.record/consultation.write respectively (migrations/0048_
clinical_rbac_permissions.sql: NURSE/DOCTOR/STAFF/ADMIN for vitals,
DOCTOR/STAFF/ADMIN for consultation) -- amending an already-completed
one stays on the separate, narrower consultation.amend
(migrations/0043_role_based_access.sql, DOCTOR/ADMIN only, no STAFF).
Every read endpoint here (get_encounter, get_latest_vitals,
get_consultation, get_consultation_amendments) stays on bare
get_current_staff, unchanged.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.staff_auth import get_current_staff, require_permission
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.clinical_services import (
    get_encounter_summary_service,
    record_vitals_service,
    get_latest_vitals_service,
    get_or_create_consultation_service,
    save_consultation_draft_service,
    complete_consultation_service,
    amend_consultation_service,
    list_consultation_amendments_service,
)

router = APIRouter(
    prefix="/appointments",
    tags=["Clinical"],
)


def _not_found(detail: str):
    return HTTPException(status_code=404, detail=detail)


class VitalsCreate(BaseModel):
    bp_systolic: int | None = Field(default=None, gt=0)
    bp_diastolic: int | None = Field(default=None, gt=0)
    pulse: int | None = Field(default=None, gt=0)
    temperature_celsius: float | None = Field(default=None, gt=0)
    spo2: int | None = Field(default=None, gt=0, le=100)
    respiratory_rate: int | None = Field(default=None, gt=0)
    weight_kg: float | None = Field(default=None, gt=0)
    height_cm: float | None = Field(default=None, gt=0)
    pain_score: int | None = Field(default=None, ge=0, le=10)
    chief_complaint: str | None = None
    priority: Literal["ROUTINE", "URGENT", "EMERGENCY"] = "ROUTINE"
    nursing_notes: str | None = None


class ConsultationSave(BaseModel):
    chief_complaint: str | None = None
    history_notes: str | None = None
    examination_notes: str | None = None
    diagnosis: str | None = None
    clinical_notes: str | None = None
    follow_up_date: date | None = None
    follow_up_reason: str | None = None
    # master spec section 44-47's "Admit to IPD" disposition scaffold
    # (docs/OPD_HIMS_MASTER_SPEC_AUDIT.md gap) -- a stub, same category
    # as orders.order_type = EXTERNAL_REFERRAL: captures the choice,
    # doesn't itself trigger any IPD/referral workflow.
    disposition: Literal["FOLLOW_UP", "REFER", "ADMIT_TO_IPD", "EMERGENCY"] | None = None
    disposition_notes: str | None = None


class ConsultationAmend(ConsultationSave):
    reason: str = Field(min_length=1)


@router.get("/{appointment_id}/encounter")
def get_encounter(appointment_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return get_encounter_summary_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise _not_found("Appointment not found")
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")


@router.post("/{appointment_id}/vitals")
def create_vitals(
    appointment_id: int,
    body: VitalsCreate,
    staff: dict = Depends(require_permission("vitals.record")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = record_vitals_service(
                    cur,
                    appointment_id,
                    staff_id=staff["id"],
                    **body.model_dump(),
                )
            except svc_exc.AppointmentNotFound:
                raise _not_found("Appointment not found")
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")
            except svc_exc.EncounterClosed:
                raise HTTPException(
                    status_code=409,
                    detail="Patient is not currently checked in -- vitals can only be recorded while the visit is in progress",
                )
    return result


@router.get("/{appointment_id}/vitals/latest")
def get_latest_vitals(appointment_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return get_latest_vitals_service(cur, appointment_id)
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")


@router.get("/{appointment_id}/consultation")
def get_consultation(appointment_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return get_or_create_consultation_service(cur, appointment_id, staff_id=staff["id"])
            except svc_exc.AppointmentNotFound:
                raise _not_found("Appointment not found")
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")
            except svc_exc.EncounterClosed:
                raise HTTPException(
                    status_code=409,
                    detail="Patient is not currently checked in -- a consultation can only be started while the visit is in progress",
                )


@router.put("/{appointment_id}/consultation")
def save_consultation(
    appointment_id: int,
    body: ConsultationSave,
    staff: dict = Depends(require_permission("consultation.write")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = save_consultation_draft_service(
                    cur,
                    appointment_id,
                    staff_id=staff["id"],
                    **body.model_dump(),
                )
            except svc_exc.AppointmentNotFound:
                raise _not_found("Appointment not found")
            except svc_exc.EncounterNotFound:
                raise _not_found("No encounter exists for this appointment")
            except svc_exc.ConsultationAlreadyCompleted:
                raise HTTPException(
                    status_code=409,
                    detail="This consultation is already completed and can no longer be edited",
                )
            except svc_exc.EncounterClosed:
                raise HTTPException(
                    status_code=409,
                    detail="Patient is not currently checked in -- a consultation can only be saved while the visit is in progress",
                )
    return result


@router.post("/{appointment_id}/consultation/complete")
def complete_consultation(appointment_id: int, staff: dict = Depends(require_permission("consultation.write"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = complete_consultation_service(cur, appointment_id, staff_id=staff["id"])
            except svc_exc.AppointmentNotFound:
                raise _not_found("Appointment not found")
            except svc_exc.EncounterNotFound:
                raise _not_found("No consultation has been started for this appointment")
            except svc_exc.ConsultationAlreadyCompleted:
                raise HTTPException(
                    status_code=409,
                    detail="This consultation is already completed",
                )
            except svc_exc.EncounterClosed:
                raise HTTPException(
                    status_code=409,
                    detail="Patient is not currently checked in -- a consultation can only be completed while the visit is in progress",
                )
            except svc_exc.ConsultationIncomplete:
                raise HTTPException(
                    status_code=422,
                    detail="Chief complaint and diagnosis are required to complete the consultation",
                )
    return result


# ---------------------------------------------------------------------
# Amendment (master spec section 70) -- consultation.amend, not bare
# get_current_staff like every endpoint above: correcting a signed-off
# consultation is a materially more sensitive action than documenting
# one the first time.
# ---------------------------------------------------------------------


@router.post("/{appointment_id}/consultation/amend")
def amend_consultation(
    appointment_id: int,
    body: ConsultationAmend,
    admin: dict = Depends(require_permission("consultation.amend")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = amend_consultation_service(
                    cur,
                    appointment_id,
                    staff_id=admin["id"],
                    **body.model_dump(),
                )
            except svc_exc.EncounterNotFound:
                raise _not_found("No consultation exists for this appointment")
            except svc_exc.ConsultationNotAmendable:
                raise HTTPException(
                    status_code=409,
                    detail="Only a completed consultation can be amended -- a draft is simply edited",
                )
            except svc_exc.ConsultationIncomplete:
                raise HTTPException(
                    status_code=422,
                    detail="Chief complaint and diagnosis can't be left blank by an amendment",
                )
    return result


@router.get("/{appointment_id}/consultation/amendments")
def get_consultation_amendments(appointment_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return list_consultation_amendments_service(cur, appointment_id)
            except svc_exc.EncounterNotFound:
                raise _not_found("No consultation exists for this appointment")
