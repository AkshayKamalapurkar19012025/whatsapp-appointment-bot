from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.staff_auth import require_role
from app.db.connection import get_connection

router = APIRouter(
    prefix="/doctors",
    tags=["Doctor Appointment Types"],
)


class DoctorAppointmentTypeCreate(BaseModel):
    duration_minutes: int = Field(gt=0, le=480)
    # Consultation fee for this doctor/appointment-type pairing (patient
    # arrival workflow Phase 3 -- get_consultation_charge_service reads
    # this at check-in/payment time). Defaults to 0 -- an admin who
    # doesn't pass this explicitly gets "no price configured yet", not
    # a fabricated fee.
    consultation_fee: float = Field(default=0, ge=0)


@router.get("/{doctor_id}/appointment-types")
def get_doctor_appointment_types(doctor_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    at.id,
                    at.name,
                    dat.duration_minutes,
                    dat.active,
                    dat.consultation_fee
                FROM doctor_appointment_types dat
                JOIN appointment_types at
                    ON at.id = dat.appointment_type_id
                WHERE dat.doctor_id = %s
                  AND dat.active = TRUE
                  AND at.active = TRUE
                ORDER BY at.name
                """,
                (doctor_id,),
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "duration_minutes": row[2],
            "active": row[3],
            "consultation_fee": row[4],
        }
        for row in rows
    ]


@router.post("/{doctor_id}/appointment-types/{appointment_type_id}")
def assign_appointment_type_to_doctor(
    doctor_id: int,
    appointment_type_id: int,
    appointment_type: DoctorAppointmentTypeCreate,
    admin: dict = Depends(require_role("ADMIN")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                """,
                (doctor_id,),
            )

            doctor = cur.fetchone()

            if doctor is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            cur.execute(
                """
                SELECT id, name
                FROM appointment_types
                WHERE id = %s
                  AND active = TRUE
                """,
                (appointment_type_id,),
            )

            appointment_type_row = cur.fetchone()

            if appointment_type_row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment type not found",
                )

            # doctor_appointment_types has a UNIQUE(doctor_id, appointment_type_id)
            # constraint (migrations/0001), and removal (DELETE below) is a soft
            # delete (active = FALSE), not a row delete. A plain INSERT would
            # therefore hit UniqueViolation not only for a still-active
            # assignment (correctly a 409 -- use PUT to change its duration)
            # but also for one that was previously removed and is now
            # inactive, permanently blocking re-assignment through this
            # endpoint. Check which case this is first, and reactivate
            # (UPDATE) the inactive row instead of trying to INSERT a
            # duplicate.
            cur.execute(
                """
                SELECT active
                FROM doctor_appointment_types
                WHERE doctor_id = %s
                  AND appointment_type_id = %s
                """,
                (doctor_id, appointment_type_id),
            )

            existing = cur.fetchone()

            if existing is not None and existing[0]:
                raise HTTPException(
                    status_code=409,
                    detail="Appointment type already assigned to doctor",
                )

            if existing is not None:
                cur.execute(
                    """
                    UPDATE doctor_appointment_types
                    SET duration_minutes = %s,
                        consultation_fee = %s,
                        active = TRUE,
                        updated_at = NOW()
                    WHERE doctor_id = %s
                      AND appointment_type_id = %s
                    RETURNING doctor_id,
                              appointment_type_id,
                              duration_minutes,
                              active,
                              consultation_fee
                    """,
                    (
                        appointment_type.duration_minutes,
                        appointment_type.consultation_fee,
                        doctor_id,
                        appointment_type_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO doctor_appointment_types (
                        doctor_id,
                        appointment_type_id,
                        duration_minutes,
                        consultation_fee
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING doctor_id,
                              appointment_type_id,
                              duration_minutes,
                              active,
                              consultation_fee
                    """,
                    (
                        doctor_id,
                        appointment_type_id,
                        appointment_type.duration_minutes,
                        appointment_type.consultation_fee,
                    ),
                )

            row = cur.fetchone()

    return {
        "doctor_id": row[0],
        "appointment_type_id": row[1],
        "duration_minutes": row[2],
        "active": row[3],
        "consultation_fee": row[4],
        "appointment_type_name": appointment_type_row[1],
    }


@router.put("/{doctor_id}/appointment-types/{appointment_type_id}")
def update_appointment_type_duration(
    doctor_id: int,
    appointment_type_id: int,
    appointment_type: DoctorAppointmentTypeCreate,
    admin: dict = Depends(require_role("ADMIN")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                """,
                (doctor_id,),
            )

            doctor = cur.fetchone()

            if doctor is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            cur.execute(
                """
                SELECT id, name
                FROM appointment_types
                WHERE id = %s
                  AND active = TRUE
                """,
                (appointment_type_id,),
            )

            appointment_type_row = cur.fetchone()

            if appointment_type_row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment type not found",
                )

            cur.execute(
                """
                UPDATE doctor_appointment_types
                SET duration_minutes = %s,
                    consultation_fee = %s,
                    active = TRUE
                WHERE doctor_id = %s
                  AND appointment_type_id = %s
                RETURNING doctor_id,
                          appointment_type_id,
                          duration_minutes,
                          active,
                          consultation_fee
                """,
                (
                    appointment_type.duration_minutes,
                    appointment_type.consultation_fee,
                    doctor_id,
                    appointment_type_id,
                ),
            )

            row = cur.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment type is not assigned to doctor",
                )

    return {
        "doctor_id": row[0],
        "appointment_type_id": row[1],
        "duration_minutes": row[2],
        "active": row[3],
        "consultation_fee": row[4],
        "appointment_type_name": appointment_type_row[1],
    }


@router.delete("/{doctor_id}/appointment-types/{appointment_type_id}")
def remove_appointment_type_from_doctor(
    doctor_id: int,
    appointment_type_id: int,
    admin: dict = Depends(require_role("ADMIN")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                """,
                (doctor_id,),
            )

            doctor = cur.fetchone()

            if doctor is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            cur.execute(
                """
                UPDATE doctor_appointment_types
                SET active = FALSE
                WHERE doctor_id = %s
                  AND appointment_type_id = %s
                  AND active = TRUE
                RETURNING doctor_id, appointment_type_id
                """,
                (doctor_id, appointment_type_id),
            )

            row = cur.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Appointment type is not assigned to doctor",
                )

    return {
        "doctor_id": row[0],
        "appointment_type_id": row[1],
        "message": "Appointment type removed from doctor",
    }