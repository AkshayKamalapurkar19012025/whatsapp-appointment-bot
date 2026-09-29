"""
The registered hospital tools (Phase 1): ten read-only tools plus one
controlled write, appointment.check_in.

Every handler here is a thin adapter over an EXISTING service in
app/services -- it adds only what the agent layer must own: the tenant
check (several services take a bare id and never look at hospital_id),
the patient-scope check, and the output whitelist. No hospital SQL lives
in this file; that is what keeps the database off the model's reachable
surface and the business rules where they already are.

Not registered, deliberately (see the audit): encounter.create (an OPD
encounter is created inside appointment booking; a standalone creator
would be a second encounter path) and queue.generate_token (the token is
issued only when the fee is paid or waived, so exposing it alone would
bypass that gate).
"""

from datetime import date as Date, datetime, time as Time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.models import AuthContext
from app.agent.tools.registry import CheckResult, Precheck, Tool, ToolError, ToolRegistry
from app.services import exceptions as svc_exc
from app.services.appointment_services import list_appointments_service
from app.services.check_in_service import front_desk_check_in_service
from app.services.clinical_services import get_encounter_summary_service
from app.services.appointment_services import get_invoice_service
from app.services.directory_read_service import get_department_service
from app.services.doctor_schedule_read_service import get_doctor_schedule_service
from app.services.patient_lookup_service import (
    SEARCH_RESULT_LIMIT,
    get_patient_service,
    search_patients_service,
)
from app.services.queue_read_service import get_doctor_queue_service
from app.services.tenant_scope import appointment_hospital_id, appointment_patient_id, doctor_hospital_id


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


APPOINTMENT_SEARCH_LIMIT = 50


# ---------------------------------------------------------------------
# Shared shapes / helpers
# ---------------------------------------------------------------------

class PatientBrief(_Out):
    """The most a model ever sees of a patient: enough to tell two Ravis
    apart (name, UHID, last four digits) and no more -- no date of birth,
    government id, address or full phone number."""

    id: int
    name: str
    uhid: str | None = None
    gender: str | None = None
    phone_last4: str | None = None


def _phone_last4(number: str | None) -> str | None:
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else None


def _patient_brief(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "uhid": row.get("uhid"),
        "gender": row.get("gender"),
        "phone_last4": _phone_last4(row.get("whatsapp_number")),
    }


class AppointmentBrief(_Out):
    id: int
    patient_id: int
    patient_name: str
    doctor_id: int
    doctor_name: str
    appointment_type_name: str | None = None
    date: str
    time: str
    status: str
    token_number: int | None = None
    payment_status: str | None = None


def _appointment_brief(item: dict) -> dict:
    # start_at is already doctor-local (list_appointments_service converts it).
    start = datetime.fromisoformat(item["start_at"])
    return {
        "id": item["id"],
        "patient_id": item["patient_id"],
        "patient_name": item["patient_name"],
        "doctor_id": item["doctor_id"],
        "doctor_name": item["doctor_name"],
        "appointment_type_name": item.get("appointment_type_name"),
        "date": start.date().isoformat(),
        "time": start.strftime("%H:%M"),
        "status": item["status"],
        "token_number": item["token_number"],
        "payment_status": item.get("payment_status"),
    }


def _not_found(what: str) -> ToolError:
    # Same answer for "doesn't exist" and "belongs to another hospital":
    # an agent must not learn that another tenant's record exists.
    return ToolError("not_found", f"{what} not found")


def _require_patient_in_scope(ctx: AuthContext, patient_id: int) -> None:
    if not ctx.patient_allowed(patient_id):
        raise ToolError("patient_out_of_scope", "patient is outside this task's patient scope")


def _require_appointment_access(cur, ctx: AuthContext, appointment_id: int) -> None:
    if appointment_hospital_id(cur, appointment_id) != ctx.facility_id:
        raise _not_found("appointment")
    patient_id = appointment_patient_id(cur, appointment_id)
    if patient_id is None:
        raise _not_found("appointment")
    _require_patient_in_scope(ctx, patient_id)


def _get_appointment(cur, ctx: AuthContext, appointment_id: int) -> dict | None:
    """One appointment through the existing listing service, tenant- and
    patient-scoped. None if it doesn't exist / isn't visible."""
    if appointment_hospital_id(cur, appointment_id) != ctx.facility_id:
        return None
    result = list_appointments_service(
        cur, appointment_id=appointment_id, hospital_id=ctx.facility_id, limit=1
    )
    if not result["items"]:
        return None
    item = result["items"][0]
    if not ctx.patient_allowed(item["patient_id"]):
        return None
    return item


# ---------------------------------------------------------------------
# patient.search / patient.get
# ---------------------------------------------------------------------

class PatientSearchIn(_In):
    query: str | None = Field(default=None, max_length=100, description="name, UHID or phone fragment")
    dob: Date | None = None

    @field_validator("query")
    @classmethod
    def _strip(cls, v):
        if v is None:
            return None
        v = v.strip()
        if len(v) < 2:
            raise ValueError("query must be at least 2 characters")
        return v

    @model_validator(mode="after")
    def _one_criterion(self):
        if not self.query and not self.dob:
            raise ValueError("provide query and/or dob")
        return self


class PatientSearchOut(_Out):
    patients: list[PatientBrief]
    count: int
    truncated: bool


def _patient_search(cur, ctx: AuthContext, args: PatientSearchIn) -> dict:
    rows = search_patients_service(cur, q=args.query, dob=args.dob, hospital_id=ctx.facility_id)
    truncated = len(rows) >= SEARCH_RESULT_LIMIT
    visible = [r for r in rows if ctx.patient_allowed(r["id"])]
    return {"patients": [_patient_brief(r) for r in visible], "count": len(visible), "truncated": truncated}


class PatientGetIn(_In):
    patient_id: int


def _patient_get(cur, ctx: AuthContext, args: PatientGetIn) -> dict:
    _require_patient_in_scope(ctx, args.patient_id)
    row = get_patient_service(cur, args.patient_id, hospital_id=ctx.facility_id)
    if row is None:
        raise _not_found("patient")
    return _patient_brief(row)


# ---------------------------------------------------------------------
# appointment.search / appointment.get
# ---------------------------------------------------------------------



class AppointmentSearchIn(_In):
    patient_id: int | None = None
    doctor_id: int | None = None
    date: Date | None = Field(default=None, description="doctor-local calendar date, YYYY-MM-DD")
    time: str | None = Field(default=None, description="doctor-local start time, HH:MM (24h)")
    status: str | None = Field(default=None, pattern=r"^[A-Z_]{3,20}$")

    @field_validator("time")
    @classmethod
    def _hhmm(cls, v):
        if v is None:
            return None
        if len(v) != 5 or v[2] != ":":
            raise ValueError("time must be HH:MM (24h)")
        Time.fromisoformat(v)
        return v

    @model_validator(mode="after")
    def _bounded(self):
        if self.patient_id is None and self.doctor_id is None and self.date is None:
            raise ValueError("provide at least one of patient_id, doctor_id, date")
        if self.time is not None and self.date is None:
            raise ValueError("time requires date")
        return self


class AppointmentSearchOut(_Out):
    appointments: list[AppointmentBrief]
    count: int
    truncated: bool


def _appointment_search(cur, ctx: AuthContext, args: AppointmentSearchIn) -> dict:
    if args.patient_id is not None:
        _require_patient_in_scope(ctx, args.patient_id)

    result = list_appointments_service(
        cur,
        patient_id=args.patient_id,
        doctor_id=args.doctor_id,
        status=args.status,
        date_from=args.date,
        date_to=args.date,
        limit=APPOINTMENT_SEARCH_LIMIT,
        hospital_id=ctx.facility_id,
    )
    items = [i for i in result["items"] if ctx.patient_allowed(i["patient_id"])]
    briefs = [_appointment_brief(i) for i in items]
    if args.time is not None:
        briefs = [b for b in briefs if b["time"] == args.time]
    return {
        "appointments": briefs,
        "count": len(briefs),
        "truncated": result["total"] > APPOINTMENT_SEARCH_LIMIT,
    }


class AppointmentGetIn(_In):
    appointment_id: int


def _appointment_get(cur, ctx: AuthContext, args: AppointmentGetIn) -> dict:
    item = _get_appointment(cur, ctx, args.appointment_id)
    if item is None:
        raise _not_found("appointment")
    return _appointment_brief(item)


# ---------------------------------------------------------------------
# doctor.get / department.get / doctor_schedule.get
# ---------------------------------------------------------------------

class DoctorGetIn(_In):
    doctor_id: int


class DoctorOut(_Out):
    id: int
    name: str
    specialization: str | None = None
    sub_specialization: str | None = None
    years_of_experience: int | None = None
    default_duration_minutes: int | None = None
    buffer_minutes: int | None = None


def _doctor_get(cur, ctx: AuthContext, args: DoctorGetIn) -> dict:
    # Imported here: the profile builder lives in the doctors router module
    # (it is shared with the WhatsApp side-channel); importing it lazily
    # keeps app.agent importable without pulling every router at load.
    from app.api.doctors import get_doctor_profile_and_education

    if doctor_hospital_id(cur, args.doctor_id) != ctx.facility_id:
        raise _not_found("doctor")
    profile = get_doctor_profile_and_education(cur, args.doctor_id)
    if profile is None:
        raise _not_found("doctor")
    return {k: profile.get(k) for k in DoctorOut.model_fields}


class DepartmentGetIn(_In):
    department_id: int


class DepartmentOut(_Out):
    id: int
    name: str
    active: bool


def _department_get(cur, ctx: AuthContext, args: DepartmentGetIn) -> dict:
    row = get_department_service(cur, args.department_id, hospital_id=ctx.facility_id)
    if row is None:
        raise _not_found("department")
    return row


class DoctorScheduleGetIn(_In):
    doctor_id: int


class ScheduleEntry(_Out):
    day_of_week: int
    start_time: str
    end_time: str
    start_date: str | None = None
    end_date: str | None = None
    department_id: int | None = None


class DoctorScheduleOut(_Out):
    doctor_id: int
    entries: list[ScheduleEntry]


def _doctor_schedule_get(cur, ctx: AuthContext, args: DoctorScheduleGetIn) -> dict:
    rows = get_doctor_schedule_service(cur, args.doctor_id, hospital_id=ctx.facility_id)
    if rows is None:
        raise _not_found("doctor")
    return {
        "doctor_id": args.doctor_id,
        "entries": [{k: r[k] for k in ScheduleEntry.model_fields} for r in rows],
    }


# ---------------------------------------------------------------------
# queue.get / encounter.get / invoice.get
# ---------------------------------------------------------------------

class QueueGetIn(_In):
    doctor_id: int


class QueueEntry(_Out):
    appointment_id: int
    token_number: int
    patient_id: int
    patient_name: str
    is_priority: bool


class QueueOut(_Out):
    doctor_id: int
    doctor_name: str
    date: str
    now_serving: QueueEntry | None = None
    waiting: list[QueueEntry]
    held: list[QueueEntry]
    completed_count: int


def _queue_get(cur, ctx: AuthContext, args: QueueGetIn) -> dict:
    if doctor_hospital_id(cur, args.doctor_id) != ctx.facility_id:
        raise _not_found("doctor")
    queue = get_doctor_queue_service(cur, args.doctor_id, hospital_id=ctx.facility_id)
    if queue is None:
        raise _not_found("doctor")

    def keep(entry):
        return entry is not None and ctx.patient_allowed(entry["patient_id"])

    def slim(entry):
        return {k: entry[k] for k in QueueEntry.model_fields}

    return {
        "doctor_id": queue["doctor_id"],
        "doctor_name": queue["doctor_name"],
        "date": queue["date"],
        "now_serving": slim(queue["now_serving"]) if keep(queue["now_serving"]) else None,
        "waiting": [slim(e) for e in queue["waiting"] if keep(e)],
        "held": [slim(e) for e in queue["held"] if keep(e)],
        "completed_count": len([e for e in queue["completed"] if keep(e)]),
    }


class EncounterGetIn(_In):
    appointment_id: int


class EncounterOut(_Out):
    encounter_id: int
    encounter_status: str
    opened_at: str
    closed_at: str | None = None
    patient_id: int
    patient_name: str
    patient_uhid: str | None = None
    doctor_id: int
    doctor_name: str
    token_number: int | None = None
    appointment_id: int
    appointment_status: str
    start_at: str


def _encounter_get(cur, ctx: AuthContext, args: EncounterGetIn) -> dict:
    _require_appointment_access(cur, ctx, args.appointment_id)
    try:
        summary = get_encounter_summary_service(cur, args.appointment_id)
    except svc_exc.ServiceError as exc:
        raise ToolError("not_found", f"no encounter for this appointment ({type(exc).__name__})") from exc
    return {k: summary[k] for k in EncounterOut.model_fields}


class InvoiceGetIn(_In):
    appointment_id: int


class InvoiceLine(_Out):
    id: int
    description: str
    amount: Decimal


class InvoiceOut(_Out):
    appointment_id: int
    invoice_number: str | None = None
    consultation_fee: Decimal
    extra_charges_total: Decimal
    total_due: Decimal
    line_items: list[InvoiceLine]


def _invoice_get(cur, ctx: AuthContext, args: InvoiceGetIn) -> dict:
    _require_appointment_access(cur, ctx, args.appointment_id)
    try:
        invoice = get_invoice_service(cur, args.appointment_id)
    except svc_exc.AppointmentNotFound as exc:
        raise _not_found("appointment") from exc
    except svc_exc.ServiceError as exc:
        raise ToolError("conflict", f"invoice unavailable ({type(exc).__name__})") from exc
    return {
        **{k: invoice[k] for k in ("appointment_id", "invoice_number", "consultation_fee",
                                   "extra_charges_total", "total_due")},
        "line_items": [{k: li[k] for k in InvoiceLine.model_fields} for li in invoice["line_items"]],
    }


# ---------------------------------------------------------------------
# appointment.check_in  (the one Phase 1 write)
# ---------------------------------------------------------------------

class CheckInIn(_In):
    appointment_id: int


class CheckInOut(_Out):
    appointment_id: int
    status: str
    visited_at: str
    patient_id: int
    doctor_id: int
    token_number: int | None = None


def _check_in(cur, ctx: AuthContext, args: CheckInIn) -> dict:
    _require_appointment_access(cur, ctx, args.appointment_id)
    try:
        result = front_desk_check_in_service(cur, args.appointment_id, hospital_id=ctx.facility_id)
    except svc_exc.AppointmentNotFound as exc:
        raise _not_found("appointment") from exc
    except svc_exc.InvalidStatusTransition as exc:
        raise ToolError("invalid_status", "only a Confirmed appointment can be checked in") from exc
    return {
        "appointment_id": result["id"],
        "status": result["status"],
        "visited_at": result["visited_at"],
        "patient_id": result["patient_id"],
        "doctor_id": result["doctor_id"],
        "token_number": result["token_number"],
    }


def _check_in_precheck(cur, ctx: AuthContext, args: CheckInIn) -> Precheck:
    item = _get_appointment(cur, ctx, args.appointment_id)
    if item is None:
        return Precheck("blocked", "appointment not found or not visible to this task")
    status = item["status"]
    detail = {"appointment_id": item["id"], "status": status}
    if status == "CHECKED_IN":
        return Precheck("already_done", "appointment is already CHECKED_IN", detail)
    if status == "CONFIRMED":
        return Precheck("ok", "", detail)
    if status == "PENDING":
        return Precheck(
            "blocked",
            "appointment is PENDING: it must be confirmed before check-in "
            "(confirm-and-check-in is not an enabled agent tool)",
            detail,
        )
    return Precheck("blocked", f"an appointment in status {status} cannot be checked in", detail)


def _check_in_postchecks(cur, ctx: AuthContext, args: CheckInIn, output: dict) -> list[CheckResult]:
    item = _get_appointment(cur, ctx, args.appointment_id)
    checks = [
        CheckResult(
            "appointment_status_is_CHECKED_IN",
            item is not None and item["status"] == "CHECKED_IN",
            f"status re-read from the appointment service: {item['status'] if item else 'not found'}",
        ),
        CheckResult(
            "tool_reported_the_same_appointment",
            output.get("appointment_id") == args.appointment_id,
            f"output appointment_id={output.get('appointment_id')}",
        ),
        CheckResult(
            "visited_at_recorded",
            # An already-CHECKED_IN appointment is verified from the
            # precheck's own read (no write happened, so no fresh RETURNING).
            bool(output.get("already_done")) or bool(output.get("visited_at")),
            f"visited_at={output.get('visited_at')}, already_done={bool(output.get('already_done'))}",
        ),
    ]
    # token_number is EXPECTED to be null here: a token is issued only on
    # payment/waiver. Recorded as evidence, never as a failure.
    return checks


def build_default_registry() -> ToolRegistry:
    r = ToolRegistry()

    def read(name, description, permissions, in_model, out_model, handler):
        r.register(Tool(
            name=name, description=description, operation="read", risk="low",
            requires_approval=False, irreversible=False, idempotency_required=False,
            required_permissions=permissions, input_model=in_model, output_model=out_model,
            handler=handler,
        ))

    read("patient.search",
         "Find patients of this hospital by name, UHID, phone fragment and/or date of birth. "
         "Returns up to 20 matches with id, name, uhid, gender and last four phone digits.",
         ("patient.read",), PatientSearchIn, PatientSearchOut, _patient_search)
    read("patient.get", "Get one patient by id (id, name, uhid, gender, phone last four).",
         ("patient.read",), PatientGetIn, PatientBrief, _patient_get)
    read("appointment.search",
         "Search appointments by patient_id, doctor_id and/or doctor-local date (YYYY-MM-DD), "
         "optionally narrowed to a doctor-local start time (HH:MM) or a status.",
         ("appointment.read",), AppointmentSearchIn, AppointmentSearchOut, _appointment_search)
    read("appointment.get", "Get one appointment by id.",
         ("appointment.read",), AppointmentGetIn, AppointmentBrief, _appointment_get)
    read("doctor.get", "Get a doctor's public profile summary by id.",
         ("directory.read",), DoctorGetIn, DoctorOut, _doctor_get)
    read("department.get", "Get a department by id.",
         ("directory.read",), DepartmentGetIn, DepartmentOut, _department_get)
    read("doctor_schedule.get", "Get a doctor's active recurring weekly schedule.",
         ("directory.read",), DoctorScheduleGetIn, DoctorScheduleOut, _doctor_schedule_get)
    read("queue.get", "Get today's queue for a doctor (now serving, waiting, held).",
         ("queue.read",), QueueGetIn, QueueOut, _queue_get)
    read("encounter.get", "Get the encounter summary for an appointment.",
         ("encounter.read",), EncounterGetIn, EncounterOut, _encounter_get)
    read("invoice.get", "Get the itemized invoice for an appointment.",
         ("invoice.read",), InvoiceGetIn, InvoiceOut, _invoice_get)

    r.register(Tool(
        name="appointment.check_in",
        description=(
            "Front-desk check-in of ONE Confirmed appointment (status CONFIRMED -> CHECKED_IN), "
            "exactly as the reception 'Check In' button does, including the patient's arrival "
            "notification. Does NOT issue a queue token: a token is issued only once the "
            "consultation fee is paid or waived."
        ),
        operation="write", risk="medium", requires_approval=False, irreversible=False,
        idempotency_required=True,
        required_permissions=("appointment.check_in", "appointment.read"),
        input_model=CheckInIn, output_model=CheckInOut, handler=_check_in,
        precheck=_check_in_precheck, postchecks=_check_in_postchecks,
        audit_resource=lambda args, out: ("appointment", out.get("appointment_id")),
    ))
    return r
