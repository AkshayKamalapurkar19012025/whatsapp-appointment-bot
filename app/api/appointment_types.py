from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
import psycopg

from app.api.staff_auth import get_current_staff, require_permission
from app.db.connection import get_connection
from app.services.audit_log import record_audit_log
from app.utils.reference_cache import get_or_set, invalidate

router = APIRouter(
    prefix="/appointment-types",
    tags=["Appointment Types"],
)

# Only the bare, active-only list below is cached -- it's the one hit
# by every appointment-type dropdown across the app. GET /admin and
# GET /{id} are staff-only management views, read far less often, and
# GET /admin in particular wants to stay maximally fresh right after
# an edit on that same page -- not worth the added invalidation
# surface for a much colder path.
_CACHE_KEY = "appointment_types:active"


class AppointmentTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Appointment type name cannot be empty")

        return value


@router.get("")
def get_appointment_types():
    def _load():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, active
                    FROM appointment_types
                    WHERE active = TRUE
                    ORDER BY name
                    """
                )
                rows = cur.fetchall()

        return [
            {
                "id": row[0],
                "name": row[1],
                "active": row[2],
            }
            for row in rows
        ]

    return get_or_set(_CACHE_KEY, _load)


@router.get("/admin")
def get_appointment_types_admin(staff: dict = Depends(get_current_staff)):
    """
    The Appointment Types admin page's own listing -- unlike GET ""
    above (public, active-only, and depended on as-is by
    AppointmentsPanel's filter dropdown and DoctorWorkspace's
    "assign a new type" list, per the OPD Appointment Types redesign
    plan), this one is staff-authenticated and deliberately returns
    EVERY type, active or not, so admins can see and reactivate an
    inactive one. Registered before the "/{appointment_type_id}" routes
    below so a literal path segment here is never at risk of being
    parsed as one, even though appointment_type_id's int type already
    makes that impossible on its own.

    doctor_count mirrors exactly what get_doctor_appointment_types
    (app/api/doctor_appointment_types.py) and create_appointment_service
    already treat as "actually assigned": an active doctor_appointment_types
    row to a doctor who is themselves active. Never counts a doctor who
    was removed or deactivated.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    at.id,
                    at.name,
                    at.active,
                    COUNT(DISTINCT dat.doctor_id) FILTER (WHERE dat.active = TRUE AND d.active = TRUE)
                FROM appointment_types at
                LEFT JOIN doctor_appointment_types dat ON dat.appointment_type_id = at.id
                LEFT JOIN doctors d ON d.id = dat.doctor_id
                GROUP BY at.id, at.name, at.active
                ORDER BY at.name
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "active": row[2],
            "doctor_count": row[3],
        }
        for row in rows
    ]


@router.get("/{appointment_type_id}")
def get_appointment_type_detail(
    appointment_type_id: int,
    staff: dict = Depends(get_current_staff),
):
    """
    The Appointment Types admin page's "View" drawer -- one call for
    everything it shows (name, active status, and the doctor/duration/
    fee table), reading duration_minutes/consultation_fee straight from
    doctor_appointment_types (never duplicated onto appointment_types
    itself, preserving the existing "duration and fee are per doctor"
    architecture). Same active-assignment/active-doctor filter as
    GET /admin's doctor_count above, so the two numbers never disagree
    with each other.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, active FROM appointment_types WHERE id = %s",
                (appointment_type_id,),
            )
            appointment_type = cur.fetchone()

            if appointment_type is None:
                raise HTTPException(status_code=404, detail="Appointment type not found")

            cur.execute(
                """
                SELECT
                    d.id,
                    d.name,
                    dep.name,
                    dat.duration_minutes,
                    dat.consultation_fee
                FROM doctor_appointment_types dat
                JOIN doctors d ON d.id = dat.doctor_id
                LEFT JOIN doctor_departments dd ON dd.doctor_id = d.id
                LEFT JOIN departments dep ON dep.id = dd.department_id
                WHERE dat.appointment_type_id = %s
                  AND dat.active = TRUE
                  AND d.active = TRUE
                ORDER BY d.name
                """,
                (appointment_type_id,),
            )
            doctor_rows = cur.fetchall()

    # A doctor can be assigned to more than one department -- dd/dep
    # above picks up one row per department, which would silently
    # duplicate that doctor in this list. Collapse to whichever
    # department row comes first per doctor (the ORDER BY above only
    # orders by doctor name, so which specific department "wins" here
    # is arbitrary) -- good enough for this read-only display, which
    # only needs *a* department to show, not a ranked "primary" one.
    seen_doctor_ids: set[int] = set()
    doctors = []
    for doctor_id, doctor_name, department_name, duration_minutes, consultation_fee in doctor_rows:
        if doctor_id in seen_doctor_ids:
            continue
        seen_doctor_ids.add(doctor_id)
        doctors.append(
            {
                "doctor_id": doctor_id,
                "doctor_name": doctor_name,
                "department_name": department_name,
                "duration_minutes": duration_minutes,
                "consultation_fee": consultation_fee,
            }
        )

    return {
        "id": appointment_type[0],
        "name": appointment_type[1],
        "active": appointment_type[2],
        "doctor_count": len(doctors),
        "doctors": doctors,
    }


@router.post("")
def create_appointment_type(
    appointment_type: AppointmentTypeCreate,
    admin: dict = Depends(require_permission("appointment_type.manage")),
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO appointment_types (name)
                    VALUES (%s)
                    RETURNING id, name, active
                    """,
                    (appointment_type.name,),
                )
                row = cur.fetchone()

                record_audit_log(
                    cur,
                    hospital_id=admin["hospital_id"],
                    staff_id=admin["id"],
                    action="appointment_type.create",
                    resource_type="appointment_type",
                    resource_id=row[0],
                    details={"name": row[1]},
                )

        invalidate(_CACHE_KEY)
        return {
            "id": row[0],
            "name": row[1],
            "active": row[2],
        }

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="Appointment type already exists",
        )


@router.put("/{appointment_type_id}")
def update_appointment_type(
    appointment_type_id: int,
    appointment_type: AppointmentTypeCreate,
    admin: dict = Depends(require_permission("appointment_type.manage")),
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE appointment_types
                    SET name = %s,
                        updated_at = NOW()
                    WHERE id = %s
                      AND active = TRUE
                    RETURNING id, name, active
                    """,
                    (appointment_type.name, appointment_type_id),
                )
                row = cur.fetchone()

                if row is None:
                    raise HTTPException(status_code=404, detail="Appointment type not found")

                record_audit_log(
                    cur,
                    hospital_id=admin["hospital_id"],
                    staff_id=admin["id"],
                    action="appointment_type.update",
                    resource_type="appointment_type",
                    resource_id=row[0],
                    details={"name": row[1]},
                )

        invalidate(_CACHE_KEY)
        return {
            "id": row[0],
            "name": row[1],
            "active": row[2],
        }

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="Appointment type already exists",
        )


class AppointmentTypeActiveUpdate(BaseModel):
    active: bool


@router.patch("/{appointment_type_id}/active")
def update_appointment_type_active(
    appointment_type_id: int,
    body: AppointmentTypeActiveUpdate,
    admin: dict = Depends(require_permission("appointment_type.manage")),
):
    """
    Deactivate/reactivate an appointment type (the redesigned Appointment
    Types page's Deactivate/Activate action) -- a direct copy of the
    already-shipped PATCH /doctors/{id}/active pattern (app/api/doctors.py),
    not a new concept. Deliberately no "AND active = TRUE" guard, since
    this is the one endpoint that has to work in both directions
    (reactivating an inactive type is exactly the point). The existing
    DELETE endpoint below already did the deactivate half of this by
    itself; this is additive, not a replacement for it.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE appointment_types
                SET active = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, active
                """,
                (body.active, appointment_type_id),
            )
            row = cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Appointment type not found")

            record_audit_log(
                cur,
                hospital_id=admin["hospital_id"],
                staff_id=admin["id"],
                action="appointment_type.active_update",
                resource_type="appointment_type",
                resource_id=row[0],
                details={"active": row[1]},
            )

    invalidate(_CACHE_KEY)
    return {"id": row[0], "active": row[1]}


@router.delete("/{appointment_type_id}")
def delete_appointment_type(
    appointment_type_id: int,
    admin: dict = Depends(require_permission("appointment_type.manage")),
):
    # Soft delete only, same as departments -- appointment_types is
    # referenced by doctor_appointment_types and appointments.appointment_
    # type_id, so a hard DELETE would either fail on the FK or silently
    # orphan rows. Every listing endpoint already filters on active = TRUE.
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE appointment_types
                SET active = FALSE,
                    updated_at = NOW()
                WHERE id = %s
                  AND active = TRUE
                RETURNING id
                """,
                (appointment_type_id,),
            )

            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Appointment type not found")

            record_audit_log(
                cur,
                hospital_id=admin["hospital_id"],
                staff_id=admin["id"],
                action="appointment_type.delete",
                resource_type="appointment_type",
                resource_id=appointment_type_id,
            )

    invalidate(_CACHE_KEY)
    return {
        "id": appointment_type_id,
        "message": "Appointment type removed",
    }