from datetime import datetime
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.appointment_services import (
    create_appointment_service,
    cancel_appointment_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/appointments",
    tags=["Appointments"],
)


class AppointmentCreate(BaseModel):
    doctor_id: int
    patient_id: int
    appointment_type_id: int
    start_at: datetime


@router.get("")
def get_appointments():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.id,
                    a.doctor_id,
                    d.name,
                    a.patient_id,
                    p.name,
                    p.whatsapp_number,
                    a.appointment_type_id,
                    at.name,
                    a.start_at,
                    a.end_at,
                    a.status
                FROM appointments a
                JOIN doctors d
                    ON d.id = a.doctor_id
                JOIN patients p
                    ON p.id = a.patient_id
                JOIN appointment_types at
                    ON at.id = a.appointment_type_id
                ORDER BY a.start_at
                """
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "doctor_id": row[1],
            "doctor_name": row[2],
            "patient_id": row[3],
            "patient_name": row[4],
            "whatsapp_number": row[5],
            "appointment_type_id": row[6],
            "appointment_type_name": row[7],
            "start_at": row[8].isoformat(),
            "end_at": row[9].isoformat(),
            "status": row[10],
        }
        for row in rows
    ]


@router.post("")
def create_appointment(appointment: AppointmentCreate):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = create_appointment_service(
                    cur,
                    doctor_id=appointment.doctor_id,
                    patient_id=appointment.patient_id,
                    appointment_type_id=appointment.appointment_type_id,
                    start_at=appointment.start_at,
                    # enforce_booking_window intentionally left at its
                    # default (False) -- this is the pre-existing REST
                    # endpoint, unauthenticated and not web-facing yet.
                    # The web booking endpoint (WEB P3/P4) will pass
                    # enforce_booking_window=True.
                )
            except svc_exc.DoctorNotFound:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )
            except svc_exc.PatientNotFound:
                raise HTTPException(
                    status_code=404,
                    detail="Patient not found",
                )
            except svc_exc.AppointmentTypeNotAssigned:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment type is not assigned to doctor",
                )
            except svc_exc.OutsideDoctorSchedule:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment is outside doctor's working schedule",
                )
            except svc_exc.DoctorBlockConflict:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment overlaps with doctor block",
                )
            except svc_exc.OutsideBookingWindow:
                raise HTTPException(
                    status_code=409,
                    detail="Requested date is outside the allowed booking window",
                )
            except svc_exc.SlotOverlap:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment overlaps with existing appointment",
                )

    return result


@router.delete("/{appointment_id}")
def cancel_appointment(appointment_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = cancel_appointment_service(cur, appointment_id)
            except svc_exc.AppointmentNotFound:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment not found",
                )
            except svc_exc.AlreadyCancelled:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment is already cancelled",
                )

    return {
        "id": result["id"],
        "status": result["status"],
        "message": "Appointment cancelled",
    }
