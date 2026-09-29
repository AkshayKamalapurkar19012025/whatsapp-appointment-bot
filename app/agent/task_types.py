"""
Task types in scope for Intake, with their required fields and the
deterministic rules the orchestrator applies regardless of what the
Intake model says: the model proposes a TaskSpec, this table decides
whether it is acceptable (all required fields present and well-formed,
risk never below the type's floor, mandatory acceptance criteria always
appended).
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def _iso_date(value) -> bool:
    try:
        date.fromisoformat(value)
        return isinstance(value, str) and len(value) == 10
    except (TypeError, ValueError):
        return False


def _hhmm(value) -> bool:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    hh, mm = value[:2], value[3:]
    return hh.isdigit() and mm.isdigit() and 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59


def _non_blank(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _payment_method(value) -> bool:
    return value in ("CASH", "UPI", "CARD", "OTHER")


def _reason(value) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= 500


@dataclass(frozen=True)
class TaskType:
    name: str
    description: str
    required_fields: dict[str, str]            # field -> human description (shown to Intake)
    validators: dict[str, Callable[[object], bool]]
    risk_floor: str
    required_permissions: tuple[str, ...]
    mandatory_criteria: tuple[str, ...] = field(default=())


TASK_TYPES: dict[str, TaskType] = {
    "appointment_check_in": TaskType(
        name="appointment_check_in",
        description="Check in ONE existing appointment for a patient at the front desk.",
        required_fields={
            "patient_reference": "who the patient is, as written in the request (name, UHID or phone fragment)",
            "appointment_date": "the appointment's calendar date, ISO YYYY-MM-DD (resolve 'today' from context.today)",
            "appointment_time": "the appointment's scheduled start time, 24h HH:MM",
        },
        validators={
            "patient_reference": _non_blank,
            "appointment_date": _iso_date,
            "appointment_time": _hhmm,
        },
        risk_floor="medium",
        required_permissions=("appointment.check_in",),
        mandatory_criteria=(
            "Exactly one appointment was checked in, and it is the appointment for the identified patient on the requested date and time.",
            "That appointment's status is CHECKED_IN after execution, confirmed by re-reading it from the appointment service.",
        ),
    ),
}


_APPOINTMENT_FIELDS = {
    "patient_reference": "who the patient is, as written in the request (name, UHID or phone fragment)",
    "appointment_date": "the appointment's calendar date, ISO YYYY-MM-DD (resolve 'today' from context.today)",
    "appointment_time": "the appointment's scheduled start time, 24h HH:MM",
}
_APPOINTMENT_VALIDATORS = {
    "patient_reference": _non_blank,
    "appointment_date": _iso_date,
    "appointment_time": _hhmm,
}
_IDENTIFIED = ("Exactly one appointment was acted on, and it is the appointment for the identified patient on the "
               "requested date and time.")
_TOKEN = "The patient now has a queue token, confirmed by re-reading the appointment from the appointment service."

TASK_TYPES["appointment_record_payment"] = TaskType(
    name="appointment_record_payment",
    description="Record that ONE checked-in patient paid the consultation fee at the front desk (issues their queue token).",
    required_fields={**_APPOINTMENT_FIELDS,
                     "payment_method": "how the patient paid: exactly one of CASH, UPI, CARD, OTHER"},
    validators={**_APPOINTMENT_VALIDATORS, "payment_method": _payment_method},
    risk_floor="high",
    required_permissions=("bill.record_payment",),
    mandatory_criteria=(
        _IDENTIFIED,
        "The appointment's consultation-fee payment status is PAID, for exactly the amount that was due and approved.",
        _TOKEN,
    ),
)

TASK_TYPES["appointment_waive_fee"] = TaskType(
    name="appointment_waive_fee",
    description="Waive the consultation fee of ONE checked-in appointment under the 3-day revisit policy (issues the queue token).",
    required_fields={**_APPOINTMENT_FIELDS,
                     "waiver_reason": "the reason for the waiver, in the requester's words (1-500 characters)"},
    validators={**_APPOINTMENT_VALIDATORS, "waiver_reason": _reason},
    risk_floor="high",
    required_permissions=("appointment.waive_payment",),
    mandatory_criteria=(
        _IDENTIFIED,
        "The appointment's consultation-fee payment status is WAIVED.",
        _TOKEN,
    ),
)

TASK_TYPES["appointment_settle_free_visit"] = TaskType(
    name="appointment_settle_free_visit",
    description="Settle ONE checked-in visit that has no consultation fee and add the patient to the queue.",
    required_fields=dict(_APPOINTMENT_FIELDS),
    validators=dict(_APPOINTMENT_VALIDATORS),
    risk_floor="high",
    required_permissions=("bill.record_payment",),
    mandatory_criteria=(
        _IDENTIFIED,
        "No money was recorded (payment amount 0) and the visit's payment status is WAIVED.",
        _TOKEN,
    ),
)


def describe_for_intake() -> list[dict]:
    return [
        {"task_type": t.name, "description": t.description, "required_fields": t.required_fields}
        for t in TASK_TYPES.values()
    ]


def risk_at_least(a: str, floor: str) -> str:
    return a if _RISK_ORDER[a] >= _RISK_ORDER[floor] else floor
