from datetime import date, datetime, timedelta, timezone
from functools import wraps
import logging
import re

import psycopg
from fastapi import APIRouter
from pydantic import BaseModel

from app.db.connection import get_connection
from app.api.patients import insert_patient
from app.utils.timezone import (
    ensure_aware_datetime,
    overlaps,
    get_doctor_timezone,
    convert_to_timezone,
    validate_timezone,
)
from app.services.availability_engine import (
    get_appointment_type_for_doctor,
    get_available_slots,
    get_appointment_types_for_department,
    get_doctors_offering_appointment_type,
    list_doctors_with_slots_for_date,
    build_doctor_summary,
    DOCTOR_SUMMARY_SELECT_SQL,
    DOCTOR_SUMMARY_JOIN_SQL,
)
from app.api.doctors import get_doctor_profile_and_education
from app.services.appointment_services import reschedule_appointment_service
from app.services.exceptions import (
    AppointmentNotFound,
    AlreadyCancelled,
    AppointmentTypeNotAssigned,
    DoctorBlockConflict,
    OutsideDoctorSchedule,
    SlotOverlap,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/scheduling",
    tags=["Scheduling"],
)


class SchedulingRequest(BaseModel):
    whatsapp_number: str
    message: str


# -------------------------------------------------------------------------
# Patient helpers
# -------------------------------------------------------------------------

def get_patient(cur, whatsapp_number: str):
    cur.execute(
        """
        SELECT id, name, whatsapp_number
        FROM patients
        WHERE whatsapp_number = %s
        """,
        (whatsapp_number,),
    )

    row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "whatsapp_number": row[2],
    }


# -------------------------------------------------------------------------
# Department helpers
# -------------------------------------------------------------------------

def get_departments(cur):
    cur.execute(
        """
        SELECT id, name
        FROM departments
        WHERE active = TRUE
        ORDER BY name
        """
    )

    rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
        }
        for row in rows
    ]


def get_doctors_for_department(cur, department_id: int):
    cur.execute(
        f"""
        SELECT
            d.id,
            d.name,
            {DOCTOR_SUMMARY_SELECT_SQL}
        FROM doctor_departments dd
        JOIN doctors d
            ON d.id = dd.doctor_id
        {DOCTOR_SUMMARY_JOIN_SQL}
        WHERE dd.department_id = %s
          AND d.active = TRUE
        ORDER BY d.name
        """,
        (department_id,),
    )

    return [build_doctor_summary(row[0], row[1], row[2:]) for row in cur.fetchall()]


# -------------------------------------------------------------------------
# Appointment type helpers
# -------------------------------------------------------------------------

def get_appointment_types_for_doctor(cur, doctor_id: int):
    cur.execute(
        """
        SELECT
            at.id,
            at.name,
            dat.duration_minutes
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
        }
        for row in rows
    ]


# get_appointment_type_for_doctor() now lives in
# app.services.availability_engine -- imported above (moved there in the
# WEB P1 phase so app/api/availability.py and future web endpoints share
# it instead of duplicating it).


# -------------------------------------------------------------------------
# Scheduling session helpers
# -------------------------------------------------------------------------

def get_scheduling_session(cur, whatsapp_number: str):
    cur.execute(
        """
        SELECT
            id,
            patient_id,
            whatsapp_number,
            step,
            department_id,
            doctor_id,
            appointment_type_id,
            selected_date,
            selected_start_at,
            selected_appointment_id,
            scheduling_mode
        FROM scheduling_sessions
        WHERE whatsapp_number = %s
        """,
        (whatsapp_number,),
    )

    row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "patient_id": row[1],
        "whatsapp_number": row[2],
        "step": row[3],
        "department_id": row[4],
        "doctor_id": row[5],
        "appointment_type_id": row[6],
        "selected_date": row[7],
        "selected_start_at": row[8],
        "selected_appointment_id": row[9],
        # NULL (default) = Doctor-First. 'DATE_FIRST' = inside the
        # Date-First state chain -- see migrations/0013's header comment.
        "scheduling_mode": row[10],
    }


def create_or_update_session(
    cur,
    patient_id: int | None,
    whatsapp_number: str,
    step: str,
    department_id=None,
    doctor_id=None,
    appointment_type_id=None,
    selected_date=None,
    selected_start_at=None,
):
    cur.execute(
        """
        INSERT INTO scheduling_sessions (
            patient_id,
            whatsapp_number,
            step,
            department_id,
            doctor_id,
            appointment_type_id,
            selected_date,
            selected_start_at,
            updated_at
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            NOW()
        )
        ON CONFLICT (whatsapp_number)
        DO UPDATE SET
            patient_id = EXCLUDED.patient_id,
            step = EXCLUDED.step,
            department_id = EXCLUDED.department_id,
            doctor_id = EXCLUDED.doctor_id,
            appointment_type_id = EXCLUDED.appointment_type_id,
            selected_date = EXCLUDED.selected_date,
            selected_start_at = EXCLUDED.selected_start_at,
            updated_at = NOW()
        RETURNING id
        """,
        (
            patient_id,
            whatsapp_number,
            step,
            department_id,
            doctor_id,
            appointment_type_id,
            selected_date,
            selected_start_at,
        ),
    )

    return cur.fetchone()[0]


def update_session(
    cur,
    session_id: int,
    step: str,
    department_id=None,
    doctor_id=None,
    appointment_type_id=None,
    selected_date=None,
    selected_start_at=None,
    selected_appointment_id=None,
    scheduling_mode=None,
):
    # scheduling_mode follows the exact same convention every other
    # parameter here already does: every call site sets the full row,
    # and any call that doesn't pass it explicitly resets it to NULL --
    # by design, not an oversight. Doctor-First transitions and every
    # MAIN_MENU/start-over/cancel/reschedule reset never pass it, so they
    # correctly clear it; only the Date-First state chain passes
    # scheduling_mode="DATE_FIRST" explicitly, at every one of its own
    # transitions, to keep carrying it forward. See migrations/0013.
    cur.execute(
        """
        UPDATE scheduling_sessions
        SET
            step = %s,
            department_id = %s,
            doctor_id = %s,
            appointment_type_id = %s,
            selected_date = %s,
            selected_start_at = %s,
            selected_appointment_id = %s,
            scheduling_mode = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (
            step,
            department_id,
            doctor_id,
            appointment_type_id,
            selected_date,
            selected_start_at,
            selected_appointment_id,
            scheduling_mode,
            session_id,
        ),
    )


def update_session_patient(
    cur,
    session_id: int,
    patient_id: int,
):
    cur.execute(
        """
        UPDATE scheduling_sessions
        SET
            patient_id = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (
            patient_id,
            session_id,
        ),
    )


def clear_session(cur, whatsapp_number: str):
    cur.execute(
        """
        DELETE FROM scheduling_sessions
        WHERE whatsapp_number = %s
        """,
        (whatsapp_number,),
    )


# -------------------------------------------------------------------------
# Date/time helpers
# -------------------------------------------------------------------------

def parse_time(value: str):
    formats = [
        "%H:%M",
        "%H.%M",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue

    return None


# get_doctor_timezone() now lives in app.utils.timezone -- imported above.
# (previously duplicated here and in app/api/availability.py)


# -------------------------------------------------------------------------
# Availability
# -------------------------------------------------------------------------

# get_available_slots() now lives in app.services.availability_engine --
# imported above (moved there in the WEB P1 phase, byte-identical, so
# app/api/availability.py and future web endpoints share this exact
# implementation instead of diverging copies).


def get_available_dates(
    cur,
    doctor_id: int,
    appointment_type_id: int,
    max_dates: int = 5,
    max_days: int = 60,
):
    """Return the next dates that have at least one available slot."""

    available_dates = []
    start_date = date.today()

    for days_ahead in range(max_days):
        candidate_date = start_date + timedelta(days=days_ahead)

        slots = get_available_slots(
            cur,
            doctor_id,
            appointment_type_id,
            candidate_date,
        )

        if slots:
            available_dates.append(candidate_date)

        if len(available_dates) == max_dates:
            break

    return available_dates


def format_date_options(available_dates):
    options = []

    for number, available_date in enumerate(available_dates, start=1):
        options.append(
            {
                "number": number,
                "date": available_date.isoformat(),
                "label": (
                    f"{available_date.strftime('%a')}, "
                    f"{available_date.day} "
                    f"{available_date.strftime('%b')}"
                ),
            }
        )

    return options


def date_selection_message(date_options):
    if not date_options:
        return "No appointment dates are available in the next 60 days."

    lines = ["Please select an appointment date:", ""]

    for option in date_options:
        lines.append(f"{option['number']}. {option['label']}")

    lines.extend(["", "Reply with the number of your preferred date."])

    return "\n".join(lines)


def format_slot_options(slots):
    options = []

    for number, slot in enumerate(slots, start=1):
        start_at = datetime.fromisoformat(slot["start_at"])
        hour = start_at.strftime("%I").lstrip("0") or "0"

        options.append(
            {
                "number": number,
                "label": f"{hour}:{start_at.strftime('%M %p')}",
                "start_at": slot["start_at"],
                "end_at": slot["end_at"],
            }
        )

    return options


def slot_selection_message(slot_options):
    lines = ["Please select an available time:", ""]

    for option in slot_options:
        lines.append(f"{option['number']}. {option['label']}")

    return "\n".join(lines)


# -------------------------------------------------------------------------
# Date-First helpers
#
# Everything below builds on the shared availability_engine functions
# imported above (get_doctors_offering_appointment_type,
# get_appointment_types_for_department, list_doctors_with_slots_for_date)
# -- no new slot/duration/timezone logic, only new WhatsApp message
# formatting and a department-scoped sibling of get_available_dates().
# -------------------------------------------------------------------------

def scheduling_mode_selection_message():
    return (
        "How would you like to find your appointment?\n\n"
        "1. Choose a Doctor\n"
        "2. Find by Date\n\n"
        "Reply with 1 or 2."
    )


def get_available_dates_for_department(
    cur,
    department_id: int,
    appointment_type_id: int,
    max_dates: int = 5,
    max_days: int = 60,
):
    """Date-First's version of get_available_dates(): the next dates
    (within the same 60-day/5-date window) on which at least one doctor
    in the department offering this appointment type has a real,
    schedulable slot -- 'available' means exactly what it means everywhere
    else in this flow (get_available_slots found one), not merely a
    doctor being scheduled to work that day."""
    doctors = get_doctors_offering_appointment_type(
        cur, department_id, appointment_type_id
    )

    if not doctors:
        return []

    available_dates = []
    start_date = date.today()

    for days_ahead in range(max_days):
        candidate_date = start_date + timedelta(days=days_ahead)

        for doctor in doctors:
            slots = get_available_slots(
                cur,
                doctor["id"],
                appointment_type_id,
                candidate_date,
                department_id=department_id,
            )
            if slots:
                available_dates.append(candidate_date)
                break

        if len(available_dates) == max_dates:
            break

    return available_dates


def format_available_doctors(doctors_with_slots):
    """doctors_with_slots is list_doctors_with_slots_for_date()'s return
    value: [{"id", "name", ...compact profile fields, "slots": [...]}],
    already filtered to doctors with at least one real slot. Adds the
    numbered-selection "number" field every other *_options list here
    already has, and formats each doctor's own slots the same way
    format_slot_options() does for the Doctor-First flow, so
    SELECT_AVAILABLE_DOCTOR_DATE_FIRST's response shape matches
    SELECT_DATE's existing "next_step": "SELECT_SLOT" response shape
    once a doctor is picked. The compact profile fields are carried
    straight through unchanged -- see doctor_summary_line() for how the
    accompanying text message renders them."""
    options = []

    for number, doctor in enumerate(doctors_with_slots, start=1):
        options.append(
            {
                "number": number,
                "id": doctor["id"],
                "name": doctor["name"],
                "specialization": doctor.get("specialization"),
                "years_of_experience": doctor.get("years_of_experience"),
                "qualifications": doctor.get("qualifications"),
                "education_location": doctor.get("education_location"),
                "slot_count": len(doctor["slots"]),
                "slots": format_slot_options(doctor["slots"]),
            }
        )

    return options


def doctor_summary_line(doctor):
    """
    A short "Specialization, N yrs exp" fragment appended after a
    doctor's name in WhatsApp listings -- the channel's equivalent of
    the web compact card's specialization/experience fields. Omits
    whatever piece is missing rather than showing a blank, since
    specialization/years_of_experience are only guaranteed present for
    doctors created after this feature (existing doctors have neither
    until an admin edits their profile).
    """
    parts = []

    if doctor.get("specialization"):
        parts.append(doctor["specialization"])

    years = doctor.get("years_of_experience")
    if years is not None:
        parts.append(f"{years} yr{'s' if years != 1 else ''} exp")

    return ", ".join(parts)


def available_doctors_selection_message(doctor_options):
    if not doctor_options:
        return "No doctors have availability on this date. Please choose another date."

    lines = ["Doctors available on this date:", ""]

    for option in doctor_options:
        plural = "slot" if option["slot_count"] == 1 else "slots"
        summary = doctor_summary_line(option)
        suffix = f" -- {summary}" if summary else ""
        lines.append(f"{option['number']}. {option['name']}{suffix} ({option['slot_count']} {plural} available)")

    lines.extend(
        [
            "",
            "Reply with the doctor's number, or PROFILE <number> to view their full profile.",
        ]
    )

    return "\n".join(lines)


def format_doctor_profile_message(profile):
    """
    Renders the same full-profile data GET /doctors/{id} returns
    (app/api/doctors.py's get_doctor_profile_and_education) as a plain
    text block for the WhatsApp "PROFILE <number>" side-channel reply
    (see try_build_doctor_profile_reply below). Never sends a photo --
    WhatsApp deliberately stays text-only for doctor info, per product
    decision; photo_url is simply not read here.
    """
    lines = [profile["name"]]

    specialization_line = profile.get("specialization") or ""
    if profile.get("sub_specialization"):
        specialization_line = f"{specialization_line} ({profile['sub_specialization']})".strip()
    if specialization_line:
        lines.append(specialization_line)

    if profile.get("qualifications"):
        lines.append(profile["qualifications"])

    years = profile.get("years_of_experience")
    if years is not None:
        lines.append(f"{years} year{'s' if years != 1 else ''} of experience")

    education = profile.get("education") or []
    if education:
        lines.append("")
        lines.append("Education & Training:")
        for entry in education:
            lines.append(
                f"- {entry['qualification']}, {entry['institution']}, "
                f"{entry['city']}, {entry['country']} ({entry['completion_year']})"
            )

    lines.append("")
    lines.append("Reply with the doctor's number to continue booking.")

    return "\n".join(lines)


_PROFILE_COMMAND_PATTERN = re.compile(r"^profile\s+(\d+)$", re.IGNORECASE)


def try_build_doctor_profile_reply(cur, message, numbered_doctors):
    """
    numbered_doctors is whatever list this step just showed the patient
    (get_doctors_for_department()'s or list_doctors_with_slots_for_date()'s
    return value, in the same order the "number" the patient would reply
    with refers to) -- 1-based, matching every other numbered-selection
    list in this file.

    Returns the formatted profile text if `message` is a "PROFILE <n>"
    command for a valid n, else None. Deliberately a pure lookup with no
    update_session call anywhere near it: the two call sites below both
    return this text alongside the *unchanged* current step's own
    listing, so asking to view a profile never advances, resets, or
    otherwise perturbs the scheduling session -- satisfying "an optional
    way to view more profile information, without adding unnecessary
    scheduling states."
    """
    match = _PROFILE_COMMAND_PATTERN.match(message.strip())
    if not match:
        return None

    number = int(match.group(1))
    if number < 1 or number > len(numbered_doctors):
        return None

    doctor_id = numbered_doctors[number - 1]["id"]
    profile = get_doctor_profile_and_education(cur, doctor_id)
    if profile is None:
        return None

    return format_doctor_profile_message(profile)


def _select_date_or_available_doctors_response(cur, patient, session, error=None):
    """
    Shared fallback for both flows whenever a scheduling interaction needs
    to send the patient back to date-adjacent selection: Doctor-First
    returns to SELECT_DATE (pick another date for the doctor already
    chosen, its existing behavior, unchanged); Date-First returns to
    SELECT_AVAILABLE_DOCTOR_DATE_FIRST (pick a different doctor for the
    date already chosen). session["scheduling_mode"] is what tells these
    apart -- by this point (SELECT_SLOT/CONFIRM_SCHEDULING) both flows share
    an otherwise identical session shape (department_id/doctor_id/
    appointment_type_id/selected_date all set), so the mode flag is the
    only way left to know which flow got the patient here. Used by both
    the explicit "back" command handler and CONFIRM_SCHEDULING's own
    change/expired/blocked/already-scheduled fallbacks -- previously each of
    those had its own copy of the Doctor-First half of this logic.
    """
    if session.get("scheduling_mode") == "DATE_FIRST":
        selected_date = session["selected_date"]
        update_session(
            cur=cur,
            session_id=session["id"],
            step="SELECT_AVAILABLE_DOCTOR_DATE_FIRST",
            department_id=session["department_id"],
            doctor_id=None,
            appointment_type_id=session["appointment_type_id"],
            selected_date=selected_date,
            selected_start_at=None,
            selected_appointment_id=None,
            scheduling_mode="DATE_FIRST",
        )

        doctors_with_slots = list_doctors_with_slots_for_date(
            cur,
            session["department_id"],
            session["appointment_type_id"],
            selected_date,
        )
        doctor_options = format_available_doctors(doctors_with_slots)

        response = {
            "patient": patient,
            "date": selected_date.isoformat(),
            "next_step": "SELECT_AVAILABLE_DOCTOR_DATE_FIRST",
            "doctors": doctor_options,
            "message": available_doctors_selection_message(doctor_options),
            "navigation": {
                "back": {"id": "BACK", "label": "Back"},
                "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
            },
        }
    else:
        doctor_id = session["doctor_id"]
        appointment_type_id = session["appointment_type_id"]
        update_session(
            cur=cur,
            session_id=session["id"],
            step="SELECT_DATE",
            department_id=session["department_id"],
            doctor_id=doctor_id,
            appointment_type_id=appointment_type_id,
            selected_date=None,
            selected_start_at=None,
            selected_appointment_id=None,
        )

        date_options = format_date_options(
            get_available_dates(cur, doctor_id, appointment_type_id)
        )

        response = {
            "patient": patient,
            "next_step": "SELECT_DATE",
            "date_options": date_options,
            "message": date_selection_message(date_options),
            "navigation": {
                "back": {"id": "BACK", "label": "Back"},
                "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
            },
        }

    if error:
        response["error"] = error

    return response


def get_upcoming_scheduled_appointments(cur, patient_id: int):
    """
    Bug fix (WEB P4): this used to return a.start_at/a.end_at exactly as
    psycopg deserializes them from the TIMESTAMPTZ columns -- which is
    always normalized to the database session's own timezone (UTC in
    this app), never the offset the row was originally inserted with.
    Confirmed live: an appointment scheduled for 2:00 PM in a doctor's
    Asia/Kolkata (+05:30) timezone was coming back as 8:30 AM here,
    which cancellation_details_message()/reschedule_selection_message()
    then displayed verbatim to WhatsApp users trying to cancel or
    reschedule -- a real, pre-existing production bug, not something
    introduced by this phase. (Scheduling/slot-selection was never affected:
    those times are computed fresh via make_aware_datetime, never read
    back from a stored row.) Fixed by converting to the doctor's own
    timezone here, the single place both the WhatsApp flow and WEB P4's
    new appointment-listing endpoint get this data from.
    """
    cur.execute(
        """
        SELECT
            a.id,
            a.doctor_id,
            d.name,
            d.timezone,
            a.appointment_type_id,
            at.name,
            a.start_at,
            a.end_at
        FROM appointments a
        JOIN doctors d
            ON d.id = a.doctor_id
        JOIN appointment_types at
            ON at.id = a.appointment_type_id
        WHERE a.patient_id = %s
          -- a.status::text: see availability_engine.py's
          -- get_available_slots for why (enum-typed
          -- appointments.status on some databases).
          AND a.status::text = ANY(%s::text[])
          AND a.start_at > NOW()
        ORDER BY a.start_at
        """,
        (patient_id, ["PENDING", "CONFIRMED"]),
    )

    rows = cur.fetchall()

    results = []

    for row in rows:
        doctor_tz = row[3]

        if not validate_timezone(doctor_tz):
            doctor_tz = "Asia/Kolkata"

        results.append(
            {
                "id": row[0],
                "doctor_id": row[1],
                "doctor_name": row[2],
                "appointment_type_id": row[4],
                "appointment_type_name": row[5],
                "start_at": convert_to_timezone(row[6], doctor_tz),
                "end_at": convert_to_timezone(row[7], doctor_tz),
            }
        )

    return results


def cancellation_details_message(appointment):
    start_at = appointment["start_at"]
    date_label = start_at.strftime("%a, %d %b %Y")
    time_label = start_at.strftime("%I:%M %p").lstrip("0")

    return (
        "Please confirm cancellation:\n\n"
        f"Doctor: {appointment['doctor_name']}\n"
        f"Appointment: {appointment['appointment_type_name']}\n"
        f"Date: {date_label}\n"
        f"Time: {time_label}\n\n"
        "Reply 1 to cancel or 2 to keep it."
    )


def cancellation_selection_message(appointments):
    lines = ["Which appointment would you like to cancel?", ""]

    for number, appointment in enumerate(appointments, start=1):
        date_label = appointment["start_at"].strftime("%a, %d %b %Y")
        time_label = appointment["start_at"].strftime("%I:%M %p").lstrip("0")
        lines.append(
            f"{number}. {date_label} at {time_label} - "
            f"{appointment['doctor_name']}"
        )

    lines.extend(["", "Reply with the appointment number."])
    return "\n".join(lines)


def reschedule_selection_message(appointments):
    lines = ["Which appointment would you like to reschedule?", ""]

    for number, appointment in enumerate(appointments, start=1):
        start_at = appointment["start_at"]
        date_label = start_at.strftime("%a, %d %b %Y")
        time_label = start_at.strftime("%I:%M %p").lstrip("0")

        lines.append(
            f"{number}. {date_label} at {time_label} - "
            f"{appointment['doctor_name']}"
        )

    lines.append("")
    lines.append("Reply with the appointment number.")

    return "\n".join(lines)


def reschedule_details_message(appointment):
    start_at = appointment["start_at"]
    date_label = start_at.strftime("%a, %d %b %Y")
    time_label = start_at.strftime("%I:%M %p").lstrip("0")

    return (
        "Please confirm rescheduling:\n\n"
        f"Doctor: {appointment['doctor_name']}\n"
        f"Appointment: {appointment['appointment_type_name']}\n"
        f"Date: {date_label}\n"
        f"Time: {time_label}\n\n"
        "Reply 1 to reschedule or 2 to keep it."
    )

def add_navigation(response):
    """Add backend navigation actions to every chatbot response.

    The current /api/scheduling endpoint still accepts text input. The future
    WhatsApp webhook can render these action IDs as interactive buttons.
    """
    if not isinstance(response, dict):
        return response

    next_step = response.get("next_step")

    # Registration has no previous application menu yet. Main menu itself
    # has no meaningful Back action. All appointment-flow states expose both
    # Back and Main Menu. Terminal success states expose Main Menu because
    # the flow has already completed.
    if next_step == "MAIN_MENU":
        response.setdefault(
            "navigation",
            {
                "back": None,
                "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
            },
        )
    elif next_step in {"SCHEDULED", "CANCELLED", "RESCHEDULED"}:
        response.setdefault(
            "navigation",
            {
                "back": None,
                "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
            },
        )
    elif next_step not in {"REGISTER_PATIENT", "ERROR"}:
        response.setdefault(
            "navigation",
            {
                "back": {"id": "BACK", "label": "Back"},
                "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
            },
        )

    return response


def navigation_response(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return add_navigation(func(*args, **kwargs))

    return wrapper


def main_menu_message():
    return (
        "Hi! Welcome back. 👋\n\n"
        "What would you like to do?\n\n"
        "1. Book Appointment\n"
        "2. Reschedule Appointment\n"
        "3. Cancel Appointment\n"
        "4. Main Menu"
    )

# -------------------------------------------------------------------------
# Scheduling endpoint
# -------------------------------------------------------------------------

@router.post("")
@navigation_response
def scheduling(request: SchedulingRequest):

    whatsapp_number = request.whatsapp_number.strip()
    message = request.message.strip()

    if not whatsapp_number:
        return {
            "next_step": "ERROR",
            "message": "WhatsApp number is required.",
        }

    with get_connection() as conn:
        with conn.cursor() as cur:

            # =============================================================
            # 1. Find patient
            # =============================================================

            session = get_scheduling_session(
                cur,
                whatsapp_number,
            )

            patient = get_patient(cur, whatsapp_number)

            # Rescheduling and cancellation are available only to
            # registered numbers with existing scheduled appointments.
            if patient is None and message.lower() in {
                "reschedule",
                "reschedule appointment",
                "reschedule my appointment",
                "cancel",
                "cancel appointment",
                "cancel my appointment",
            }:
                return {
                    "next_step": "REGISTER_PATIENT",
                    "error": "Rescheduling and cancellation are available only for registered mobile numbers with a booked appointment.",
                    "message": "Please send Hi using your registered mobile number.",
                    "whatsapp_number": whatsapp_number,
                }

            if patient is None:
                if session is None:
                    create_or_update_session(
                        cur=cur,
                        patient_id=None,
                        whatsapp_number=whatsapp_number,
                        step="REGISTER_PATIENT",
                    )

                    return {
                        "next_step": "REGISTER_PATIENT",
                        "message": "Welcome! Please enter your name to continue.",
                        "whatsapp_number": whatsapp_number,
                    }

                if (
                    session["step"] == "REGISTER_PATIENT"
                    and session["patient_id"] is None
                ):
                    if message.lower() in {
                        "restart",
                        "start over",
                        "start",
                        "back",
                    }:
                        return {
                            "next_step": "REGISTER_PATIENT",
                            "message": "Welcome! Please enter your name to continue.",
                            "whatsapp_number": whatsapp_number,
                        }

                    if not message or len(message) > 150:
                        return {
                            "next_step": "REGISTER_PATIENT",
                            "error": "Please enter a valid name up to 150 characters.",
                            "message": "Welcome! Please enter your name to continue.",
                            "whatsapp_number": whatsapp_number,
                        }

                    patient = insert_patient(
                        cur,
                        message,
                        whatsapp_number,
                    )

                    if patient is None:
                        patient = get_patient(cur, whatsapp_number)

                    if patient is None:
                        return {
                            "next_step": "REGISTER_PATIENT",
                            "error": "Unable to register the patient. Please try again.",
                            "message": "Welcome! Please enter your name to continue.",
                            "whatsapp_number": whatsapp_number,
                        }

                    update_session_patient(
                        cur,
                        session["id"],
                        patient["id"],
                    )

                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": main_menu_message(),
                    }

                return {
                    "next_step": "REGISTER_PATIENT",
                    "message": "Welcome! Please enter your name to continue.",
                    "whatsapp_number": whatsapp_number,
                }

            # =============================================================
            # 2. Get current session
            # =============================================================

            if not message:
                return {
                    "next_step": "ERROR",
                    "message": "Message cannot be empty.",
                }

            # =============================================================
            # MAIN MENU / GREETINGS
            # =============================================================

            if message.lower() in {
                "hi",
                "hello",
                "hey",
                "main menu",
                "menu",
            }:
                if session is None:
                    session_id = create_or_update_session(
                        cur=cur,
                        patient_id=patient["id"],
                        whatsapp_number=whatsapp_number,
                        step="MAIN_MENU",
                    )
                    session = {"id": session_id}
                else:
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                return {
                    "patient": patient,
                    "next_step": "MAIN_MENU",
                    "message": main_menu_message(),
                }

            # =============================================================
            # MAIN MENU SELECTION
            # =============================================================

            if session["step"] == "MAIN_MENU":

                if message == "1" or message.lower() in {
                    "book",
                    "book appointment",
                }:
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_SCHEDULING_MODE",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "SELECT_SCHEDULING_MODE",
                        "message": scheduling_mode_selection_message(),
                    }

                if message == "2":
                    message = "reschedule"

                elif message == "3":
                    message = "cancel"

                elif message == "4":
                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": main_menu_message(),
                    }

                else:
                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "error": "Please select a valid option from the main menu.",
                        "message": main_menu_message(),
                    }

            # =============================================================
            # GLOBAL NAVIGATION
            # =============================================================
            # Navigation is handled centrally so every scheduling, reschedule,
            # and cancellation state supports Back and Main Menu.
            #
            # The current API still accepts text commands. The future
            # WhatsApp webhook can map interactive button IDs such as BACK
            # and MAIN_MENU to these same values without changing the
            # conversation state machine.

            if message.lower() in {
                "main menu",
                "mainmenu",
                "menu",
                "MAIN_MENU",
            }:
                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="MAIN_MENU",
                    department_id=None,
                    doctor_id=None,
                    appointment_type_id=None,
                    selected_date=None,
                    selected_start_at=None,
                    selected_appointment_id=None,
                )

                return {
                    "patient": patient,
                    "next_step": "MAIN_MENU",
                    "navigation": {
                        "back": None,
                        "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                    },
                    "message": main_menu_message(),
                }

            if message.lower() in {"back", "go back", "BACK"}:

                current_step = session["step"]

                # ---------------------------------------------------------
                # SCHEDULING
                # ---------------------------------------------------------

                if current_step == "SELECT_DEPARTMENT":
                    # Back target changed from MAIN_MENU to
                    # SELECT_SCHEDULING_MODE: the mode fork is now the step
                    # between them (see the "1"/"book" handler above).
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_SCHEDULING_MODE",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )
                    return {
                        "patient": patient,
                        "next_step": "SELECT_SCHEDULING_MODE",
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                        "message": scheduling_mode_selection_message(),
                    }

                if current_step == "SELECT_SCHEDULING_MODE":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )
                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "navigation": {
                            "back": None,
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                        "message": main_menu_message(),
                    }

                if current_step == "SELECT_DEPARTMENT_DATE_FIRST":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_SCHEDULING_MODE",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )
                    return {
                        "patient": patient,
                        "next_step": "SELECT_SCHEDULING_MODE",
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                        "message": scheduling_mode_selection_message(),
                    }

                if current_step == "SELECT_APPOINTMENT_TYPE_DATE_FIRST":
                    department_id = session["department_id"]
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_DEPARTMENT_DATE_FIRST",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                        scheduling_mode="DATE_FIRST",
                    )
                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT_DATE_FIRST",
                        "departments": get_departments(cur),
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                if current_step == "SELECT_DATE_DATE_FIRST":
                    department_id = session["department_id"]
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_APPOINTMENT_TYPE_DATE_FIRST",
                        department_id=department_id,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                        scheduling_mode="DATE_FIRST",
                    )
                    return {
                        "patient": patient,
                        "department_id": department_id,
                        "next_step": "SELECT_APPOINTMENT_TYPE_DATE_FIRST",
                        "appointment_types": get_appointment_types_for_department(cur, department_id),
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                if current_step == "SELECT_AVAILABLE_DOCTOR_DATE_FIRST":
                    department_id = session["department_id"]
                    appointment_type_id = session["appointment_type_id"]
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_DATE_DATE_FIRST",
                        department_id=department_id,
                        doctor_id=None,
                        appointment_type_id=appointment_type_id,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                        scheduling_mode="DATE_FIRST",
                    )
                    date_options = format_date_options(
                        get_available_dates_for_department(cur, department_id, appointment_type_id)
                    )
                    return {
                        "patient": patient,
                        "department_id": department_id,
                        "appointment_type_id": appointment_type_id,
                        "next_step": "SELECT_DATE_DATE_FIRST",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                if current_step == "SELECT_DOCTOR":
                    department_id = session["department_id"]
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_DEPARTMENT",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )
                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "departments": get_departments(cur),
                        "navigation": {
                            "back": None,
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                if current_step == "SELECT_APPOINTMENT_TYPE":
                    department_id = session["department_id"]
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_DOCTOR",
                        department_id=department_id,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )
                    return {
                        "patient": patient,
                        "department_id": department_id,
                        "next_step": "SELECT_DOCTOR",
                        "doctors": get_doctors_for_department(cur, department_id),
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                if current_step == "SELECT_DATE":
                    doctor_id = session["doctor_id"]
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_APPOINTMENT_TYPE",
                        department_id=session["department_id"],
                        doctor_id=doctor_id,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )
                    return {
                        "patient": patient,
                        "doctor_id": doctor_id,
                        "next_step": "SELECT_APPOINTMENT_TYPE",
                        "appointment_types": get_appointment_types_for_doctor(cur, doctor_id),
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                if current_step == "SELECT_SLOT":
                    return _select_date_or_available_doctors_response(cur, patient, session)

                if current_step == "CONFIRM_SCHEDULING":
                    doctor_id = session["doctor_id"]
                    appointment_type_id = session["appointment_type_id"]
                    selected_date = session["selected_date"]
                    slots = get_available_slots(
                        cur, doctor_id, appointment_type_id, selected_date
                    )
                    if not slots:
                        return _select_date_or_available_doctors_response(
                            cur,
                            patient,
                            session,
                            error="No appointments are available on this date. Please choose another date.",
                        )

                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_SLOT",
                        department_id=session["department_id"],
                        doctor_id=doctor_id,
                        appointment_type_id=appointment_type_id,
                        selected_date=selected_date,
                        selected_start_at=None,
                        selected_appointment_id=None,
                        scheduling_mode=session.get("scheduling_mode"),
                    )
                    slot_options = format_slot_options(slots)
                    return {
                        "patient": patient,
                        "date": selected_date.isoformat(),
                        "next_step": "SELECT_SLOT",
                        "slots": slot_options,
                        "message": slot_selection_message(slot_options),
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                # ---------------------------------------------------------
                # CANCELLATION
                # ---------------------------------------------------------

                if current_step == "CANCEL_SELECT":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )
                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "navigation": {
                            "back": None,
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                        "message": main_menu_message(),
                    }

                if current_step == "CANCEL_CONFIRM":
                    appointments = get_upcoming_scheduled_appointments(cur, patient["id"])
                    if not appointments:
                        update_session(
                            cur=cur,
                            session_id=session["id"],
                            step="MAIN_MENU",
                            department_id=None,
                            doctor_id=None,
                            appointment_type_id=None,
                            selected_date=None,
                            selected_start_at=None,
                            selected_appointment_id=None,
                        )
                        return {
                            "patient": patient,
                            "next_step": "MAIN_MENU",
                            "message": "You do not have any upcoming appointments to cancel.\n\n" + main_menu_message(),
                            "navigation": {
                                "back": None,
                                "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                            },
                        }
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="CANCEL_SELECT",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )
                    return {
                        "patient": patient,
                        "next_step": "CANCEL_SELECT",
                        "appointments": [
                            {
                                "number": number,
                                "doctor_name": appointment["doctor_name"],
                                "appointment_type_name": appointment["appointment_type_name"],
                                "start_at": appointment["start_at"].isoformat(),
                                "end_at": appointment["end_at"].isoformat(),
                            }
                            for number, appointment in enumerate(appointments, start=1)
                        ],
                        "message": cancellation_selection_message(appointments),
                        "navigation": {
                            "back": None,
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                # ---------------------------------------------------------
                # RESCHEDULING
                # ---------------------------------------------------------

                if current_step == "RESCHEDULE_SELECT":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )
                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "navigation": {
                            "back": None,
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                        "message": main_menu_message(),
                    }

                if current_step == "RESCHEDULE_CONFIRM":
                    appointment_id = session["selected_appointment_id"]
                    appointments = get_upcoming_scheduled_appointments(cur, patient["id"])
                    appointment = next(
                        (item for item in appointments if item["id"] == appointment_id),
                        None,
                    )
                    if appointment is None:
                        update_session(
                            cur=cur,
                            session_id=session["id"],
                            step="MAIN_MENU",
                            department_id=None,
                            doctor_id=None,
                            appointment_type_id=None,
                            selected_date=None,
                            selected_start_at=None,
                            selected_appointment_id=None,
                        )
                        return {
                            "patient": patient,
                            "next_step": "MAIN_MENU",
                            "message": "That appointment is no longer available to reschedule.\n\n" + main_menu_message(),
                            "navigation": {
                                "back": None,
                                "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                            },
                        }
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="RESCHEDULE_SELECT",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )
                    return {
                        "patient": patient,
                        "next_step": "RESCHEDULE_SELECT",
                        "appointments": [
                            {
                                "number": number,
                                "doctor_name": item["doctor_name"],
                                "appointment_type_name": item["appointment_type_name"],
                                "start_at": item["start_at"].isoformat(),
                                "end_at": item["end_at"].isoformat(),
                            }
                            for number, item in enumerate(appointments, start=1)
                        ],
                        "message": reschedule_selection_message(appointments),
                        "navigation": {
                            "back": None,
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                if current_step == "RESCHEDULE_DATE":
                    appointment = get_upcoming_scheduled_appointments(cur, patient["id"])
                    appointment = next(
                        (item for item in appointment if item["id"] == session["selected_appointment_id"]),
                        None,
                    )
                    if appointment is None:
                        update_session(
                            cur=cur,
                            session_id=session["id"],
                            step="MAIN_MENU",
                            department_id=None,
                            doctor_id=None,
                            appointment_type_id=None,
                            selected_date=None,
                            selected_start_at=None,
                            selected_appointment_id=None,
                        )
                        return {
                            "patient": patient,
                            "next_step": "MAIN_MENU",
                            "message": "That appointment is no longer available to reschedule.\n\n" + main_menu_message(),
                            "navigation": {
                                "back": None,
                                "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                            },
                        }
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="RESCHEDULE_CONFIRM",
                        department_id=None,
                        doctor_id=appointment["doctor_id"],
                        appointment_type_id=appointment["appointment_type_id"],
                        selected_date=appointment["start_at"].date(),
                        selected_start_at=appointment["start_at"],
                        selected_appointment_id=appointment["id"],
                    )
                    return {
                        "patient": patient,
                        "next_step": "RESCHEDULE_CONFIRM",
                        "appointment": {
                            "doctor_name": appointment["doctor_name"],
                            "appointment_type_name": appointment["appointment_type_name"],
                            "start_at": appointment["start_at"].isoformat(),
                            "end_at": appointment["end_at"].isoformat(),
                        },
                        "message": reschedule_details_message(appointment),
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                if current_step == "RESCHEDULE_SLOT":
                    doctor_id = session["doctor_id"]
                    appointment_type_id = session["appointment_type_id"]
                    selected_date = session["selected_date"]
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="RESCHEDULE_DATE",
                        department_id=None,
                        doctor_id=doctor_id,
                        appointment_type_id=appointment_type_id,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=session["selected_appointment_id"],
                    )
                    date_options = format_date_options(
                        get_available_dates(cur, doctor_id, appointment_type_id)
                    )
                    return {
                        "patient": patient,
                        "doctor_id": doctor_id,
                        "appointment_type_id": appointment_type_id,
                        "next_step": "RESCHEDULE_DATE",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                if current_step == "RESCHEDULE_FINAL_CONFIRM":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="RESCHEDULE_DATE",
                        department_id=None,
                        doctor_id=session["doctor_id"],
                        appointment_type_id=session["appointment_type_id"],
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=session["selected_appointment_id"],
                    )
                    date_options = format_date_options(
                        get_available_dates(
                            cur,
                            session["doctor_id"],
                            session["appointment_type_id"],
                        )
                    )
                    return {
                        "patient": patient,
                        "doctor_id": session["doctor_id"],
                        "appointment_type_id": session["appointment_type_id"],
                        "next_step": "RESCHEDULE_DATE",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                # Unknown/terminal states: safely return to the main menu.
                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="MAIN_MENU",
                    department_id=None,
                    doctor_id=None,
                    appointment_type_id=None,
                    selected_date=None,
                    selected_start_at=None,
                    selected_appointment_id=None,
                )
                return {
                    "patient": patient,
                    "next_step": "MAIN_MENU",
                    "message": main_menu_message(),
                    "navigation": {
                        "back": None,
                        "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                    },
                }

            # =============================================================
            # 4. CANCELLATION
            # =============================================================

            if message.lower() in {"cancel", "cancel appointment", "cancel my appointment"}:

                appointments = get_upcoming_scheduled_appointments(
                    cur,
                    patient["id"],
                )

                if not appointments:
                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": "You do not have any upcoming appointments to cancel.\n\n" + main_menu_message(),
                    }

                if len(appointments) == 1:
                    appointment = appointments[0]

                    update_session(
                        cur=cur,
                        session_id=session["id"] if session else create_or_update_session(
                            cur=cur,
                            patient_id=patient["id"],
                            whatsapp_number=whatsapp_number,
                            step="CANCEL_CONFIRM",
                        ),
                        step="CANCEL_CONFIRM",
                        department_id=None,
                        doctor_id=appointment["doctor_id"],
                        appointment_type_id=appointment["appointment_type_id"],
                        selected_date=appointment["start_at"].date(),
                        selected_start_at=appointment["start_at"],
                        selected_appointment_id=appointment["id"],
                    )

                    return {
                        "patient": patient,
                        "next_step": "CANCEL_CONFIRM",
                        "appointment": {
                            "doctor_name": appointment["doctor_name"],
                            "appointment_type_name": appointment["appointment_type_name"],
                            "start_at": appointment["start_at"].isoformat(),
                            "end_at": appointment["end_at"].isoformat(),
                        },
                        "message": cancellation_details_message(appointment),
                    }

                session_id = session["id"] if session else create_or_update_session(
                    cur=cur,
                    patient_id=patient["id"],
                    whatsapp_number=whatsapp_number,
                    step="CANCEL_SELECT",
                )

                update_session(
                    cur=cur,
                    session_id=session_id,
                    step="CANCEL_SELECT",
                    department_id=None,
                    doctor_id=None,
                    appointment_type_id=None,
                    selected_date=None,
                    selected_start_at=None,
                )

                return {
                    "patient": patient,
                    "next_step": "CANCEL_SELECT",
                    "appointments": [
                        {
                            "number": number,
                            "doctor_name": appointment["doctor_name"],
                            "appointment_type_name": appointment["appointment_type_name"],
                            "start_at": appointment["start_at"].isoformat(),
                            "end_at": appointment["end_at"].isoformat(),
                        }
                        for number, appointment in enumerate(appointments, start=1)
                    ],
                    "message": cancellation_selection_message(appointments),
                }

            # =============================================================
            # 3. New conversation
            # =============================================================

            if session is None:

                create_or_update_session(
                    cur=cur,
                    patient_id=patient["id"],
                    whatsapp_number=whatsapp_number,
                    step="SELECT_DEPARTMENT",
                )

                departments = get_departments(cur)

                return {
                    "patient": patient,
                    "next_step": "SELECT_DEPARTMENT",
                    "departments": departments,
                }
            # =============================================================
            # 4. RESCHEDULE
            # =============================================================

            if message.lower() in {
                "reschedule",
                "reschedule appointment",
                "reschedule my appointment",
            }:

                appointments = get_upcoming_scheduled_appointments(
                    cur,
                    patient["id"],
                )

                if not appointments:
                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": "You do not have any upcoming appointments to reschedule.\n\n" + main_menu_message(),
                    }

                session_id = session["id"] if session else create_or_update_session(
                    cur=cur,
                    patient_id=patient["id"],
                    whatsapp_number=whatsapp_number,
                    step="RESCHEDULE_SELECT",
                )

                update_session(
                    cur=cur,
                    session_id=session_id,
                    step="RESCHEDULE_SELECT",
                    department_id=None,
                    doctor_id=None,
                    appointment_type_id=None,
                    selected_date=None,
                    selected_start_at=None,
                    selected_appointment_id=None,
                )

                return {
                    "patient": patient,
                    "next_step": "RESCHEDULE_SELECT",
                    "appointments": [
                        {
                            "number": number,
                            "doctor_name": appointment["doctor_name"],
                            "appointment_type_name": appointment["appointment_type_name"],
                            "start_at": appointment["start_at"].isoformat(),
                            "end_at": appointment["end_at"].isoformat(),
                        }
                        for number, appointment in enumerate(appointments, start=1)
                    ],
                    "message": reschedule_selection_message(appointments),
                }

            # =============================================================
            # 5. RESCHEDULE SELECTION
            # =============================================================

            if session["step"] == "RESCHEDULE_SELECT":

                if message.lower() == "back":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": main_menu_message(),
                    }

                appointments = get_upcoming_scheduled_appointments(
                    cur,
                    patient["id"],
                )

                if not appointments:
                    update_session(
                        cur=cur,
                        session_id=session["id"] if session else create_or_update_session(
                            cur=cur,
                            patient_id=patient["id"],
                            whatsapp_number=whatsapp_number,
                            step="MAIN_MENU",
                        ),
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": "You do not have any upcoming appointments to reschedule.\n\n" + main_menu_message(),
                    }

                if (
                    not message.isdigit()
                    or not 1 <= int(message) <= len(appointments)
                ):
                    return {
                        "patient": patient,
                        "next_step": "RESCHEDULE_SELECT",
                        "error": "Please select a valid appointment number.",
                        "appointments": [
                            {
                                "number": number,
                                "doctor_name": appointment["doctor_name"],
                                "appointment_type_name": appointment[
                                    "appointment_type_name"
                                ],
                                "start_at": appointment["start_at"].isoformat(),
                                "end_at": appointment["end_at"].isoformat(),
                            }
                            for number, appointment in enumerate(
                                appointments,
                                start=1,
                            )
                        ],
                        "message": reschedule_selection_message(appointments),
                    }

                appointment = appointments[int(message) - 1]

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="RESCHEDULE_CONFIRM",
                    department_id=None,
                    doctor_id=appointment["doctor_id"],
                    appointment_type_id=appointment["appointment_type_id"],
                    selected_date=appointment["start_at"].date(),
                    selected_start_at=appointment["start_at"],
                    selected_appointment_id=appointment["id"],
                )

                return {
                    "patient": patient,
                    "next_step": "RESCHEDULE_CONFIRM",
                    "appointment": {
                        "doctor_name": appointment["doctor_name"],
                        "appointment_type_name": appointment[
                            "appointment_type_name"
                        ],
                        "start_at": appointment["start_at"].isoformat(),
                        "end_at": appointment["end_at"].isoformat(),
                    },
                    "message": reschedule_details_message(appointment),
                }

            # =============================================================
            # 6. RESCHEDULE CONFIRMATION
            # =============================================================

            if session["step"] == "RESCHEDULE_CONFIRM":

                if message.lower() == "back":
                    appointments = get_upcoming_scheduled_appointments(
                        cur,
                        patient["id"],
                    )

                    if not appointments:
                        clear_session(cur, whatsapp_number)

                        return {
                            "patient": patient,
                            "next_step": "SELECT_DEPARTMENT",
                            "message": "You do not have any upcoming appointments to reschedule.",
                            "departments": get_departments(cur),
                        }

                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="RESCHEDULE_SELECT",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "RESCHEDULE_SELECT",
                        "appointments": [
                            {
                                "number": number,
                                "doctor_name": appointment["doctor_name"],
                                "appointment_type_name": appointment[
                                    "appointment_type_name"
                                ],
                                "start_at": appointment["start_at"].isoformat(),
                                "end_at": appointment["end_at"].isoformat(),
                            }
                            for number, appointment in enumerate(
                                appointments,
                                start=1,
                            )
                        ],
                        "message": reschedule_selection_message(appointments),
                    }                

                if message == "2":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": "Your appointment was not rescheduled.\n\n" + main_menu_message(),
                    }

                if message != "1":
                    appointment = get_upcoming_scheduled_appointments(
                        cur,
                        patient["id"],
                    )

                    appointment = next(
                        (
                            item
                            for item in appointment
                            if item["id"] == session["selected_appointment_id"]
                        ),
                        None,
                    )

                    if appointment is None:
                        clear_session(cur, whatsapp_number)

                        return {
                            "patient": patient,
                            "next_step": "SELECT_DEPARTMENT",
                            "message": "That appointment is no longer available to reschedule.",
                            "departments": get_departments(cur),
                        }

                    return {
                        "patient": patient,
                        "next_step": "RESCHEDULE_CONFIRM",
                        "error": "Please reply 1 to reschedule or 2 to keep it.",
                        "message": reschedule_details_message(appointment),
                    }

                # Continue into the existing date/slot selection flow.
                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="RESCHEDULE_DATE",
                    department_id=None,
                    doctor_id=session["doctor_id"],
                    appointment_type_id=session["appointment_type_id"],
                    selected_date=None,
                    selected_start_at=None,
                    selected_appointment_id=session[
                        "selected_appointment_id"
                    ],
                )

                available_dates = get_available_dates(
                    cur,
                    session["doctor_id"],
                    session["appointment_type_id"],
                )

                date_options = format_date_options(available_dates)

                return {
                    "patient": patient,
                    "doctor_id": session["doctor_id"],
                    "appointment_type_id": session["appointment_type_id"],
                    "next_step": "RESCHEDULE_DATE",
                    "date_options": date_options,
                    "message": date_selection_message(date_options),
                }


            # =============================================================
            # 7. CANCELLATION SELECTION
            # =============================================================

            if session["step"] == "CANCEL_SELECT":
                if message.lower() == "back":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )
                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": main_menu_message(),
                    }

                appointments = get_upcoming_scheduled_appointments(cur, patient["id"])

                if not appointments:
                    update_session(
                        cur=cur,
                        session_id=session["id"] if session else create_or_update_session(
                            cur=cur,
                            patient_id=patient["id"],
                            whatsapp_number=whatsapp_number,
                            step="MAIN_MENU",
                        ),
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": "You do not have any upcoming appointments to cancel.\n\n" + main_menu_message(),
                    }

                if not message.isdigit() or not 1 <= int(message) <= len(appointments):
                    return {
                        "patient": patient,
                        "next_step": "CANCEL_SELECT",
                        "error": "Please select a valid appointment number.",
                        "appointments": [
                            {
                                "number": number,
                                "doctor_name": appointment["doctor_name"],
                                "appointment_type_name": appointment["appointment_type_name"],
                                "start_at": appointment["start_at"].isoformat(),
                                "end_at": appointment["end_at"].isoformat(),
                            }
                            for number, appointment in enumerate(appointments, start=1)
                        ],
                        "message": cancellation_selection_message(appointments),
                    }

                appointment = appointments[int(message) - 1]

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="CANCEL_CONFIRM",
                    department_id=None,
                    doctor_id=appointment["doctor_id"],
                    appointment_type_id=appointment["appointment_type_id"],
                    selected_date=appointment["start_at"].date(),
                    selected_start_at=appointment["start_at"],
                    selected_appointment_id=appointment["id"],
                )

                return {
                    "patient": patient,
                    "next_step": "CANCEL_CONFIRM",
                    "appointment": {
                        "doctor_name": appointment["doctor_name"],
                        "appointment_type_name": appointment["appointment_type_name"],
                        "start_at": appointment["start_at"].isoformat(),
                        "end_at": appointment["end_at"].isoformat(),
                    },
                    "message": cancellation_details_message(appointment),
                }

            # =============================================================
            # 6. CANCELLATION CONFIRMATION
            # =============================================================

            if session["step"] == "CANCEL_CONFIRM":

                if message.lower() == "back":
                    appointments = get_upcoming_scheduled_appointments(cur, patient["id"])

                    if not appointments:
                        update_session(
                            cur=cur,
                            session_id=session["id"],
                            step="MAIN_MENU",
                            department_id=None,
                            doctor_id=None,
                            appointment_type_id=None,
                            selected_date=None,
                            selected_start_at=None,
                            selected_appointment_id=None,
                        )
                        return {
                            "patient": patient,
                            "next_step": "MAIN_MENU",
                            "message": "You do not have any upcoming appointments to cancel.\n\n" + main_menu_message(),
                        }

                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="CANCEL_SELECT",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                    )
                    return {
                        "patient": patient,
                        "next_step": "CANCEL_SELECT",
                        "appointments": [
                            {
                                "number": number,
                                "doctor_name": appointment["doctor_name"],
                                "appointment_type_name": appointment["appointment_type_name"],
                                "start_at": appointment["start_at"].isoformat(),
                                "end_at": appointment["end_at"].isoformat(),
                            }
                            for number, appointment in enumerate(appointments, start=1)
                        ],
                        "message": cancellation_selection_message(appointments),
                    }

                if message == "2":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": "Your appointment was not cancelled.\n\n" + main_menu_message(),
                    }

                if message != "1":
                    appointment = get_upcoming_scheduled_appointments(cur, patient["id"])
                    appointment = next(
                        (
                            item
                            for item in appointment
                            if item["doctor_id"] == session["doctor_id"]
                            and item["start_at"] == session["selected_start_at"]
                        ),
                        None,
                    )

                    if appointment is None:
                        clear_session(cur, whatsapp_number)
                        return {
                            "patient": patient,
                            "next_step": "SELECT_DEPARTMENT",
                            "message": "That appointment is no longer available to cancel.",
                            "departments": get_departments(cur),
                        }

                    return {
                        "patient": patient,
                        "next_step": "CANCEL_CONFIRM",
                        "error": "Please reply 1 to cancel or 2 to keep it.",
                        "message": cancellation_details_message(appointment),
                    }

                cur.execute(
                    """
                    UPDATE appointments
                    SET status = 'CANCELLED', updated_at = NOW()
                    WHERE id = %s
                    AND patient_id = %s
                    -- status::text: see availability_engine.py's
                    -- get_available_slots for why (enum-typed
                    -- appointments.status on some databases).
                    AND status::text = ANY(%s::text[])
                    RETURNING id, start_at, end_at
                    """,
    (
        session["selected_appointment_id"],
        patient["id"],
        ["PENDING", "CONFIRMED"],
    ),
                )

                cancelled = cur.fetchone()

                if cancelled is None:
                    clear_session(cur, whatsapp_number)
                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "message": "That appointment was already cancelled or is no longer available.",
                        "departments": get_departments(cur),
                    }

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="MAIN_MENU",
                    department_id=None,
                    doctor_id=None,
                    appointment_type_id=None,
                    selected_date=None,
                    selected_start_at=None,
                    selected_appointment_id=None,
                )

                return {
                    "patient": patient,
                    "next_step": "CANCELLED",
                    "appointment": {
                        "start_at": cancelled[1].isoformat(),
                        "end_at": cancelled[2].isoformat(),
                    },
                    "message": "Your appointment has been cancelled successfully.\n\n" + main_menu_message(),
                }

            # =============================================================
            # 7. START OVER
            # =============================================================

            if message.lower() in {
                "restart",
                "start over",
                "start",
            }:

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="MAIN_MENU",
                    department_id=None,
                    doctor_id=None,
                    appointment_type_id=None,
                    selected_date=None,
                    selected_start_at=None,
                    selected_appointment_id=None,
                )

                return {
                    "patient": patient,
                    "next_step": "MAIN_MENU",
                    "message": "Let's start over.\n\n" + main_menu_message(),
                }

            # =============================================================
            # 5. BACK
            # =============================================================

            if message.lower() == "back":

                current_step = session["step"]

                if current_step == "SELECT_DEPARTMENT":

                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": main_menu_message(),
                    }

                if current_step == "SELECT_DOCTOR":

                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_DEPARTMENT",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "departments": get_departments(cur),
                    }

                if current_step == "SELECT_APPOINTMENT_TYPE":

                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_DOCTOR",
                        department_id=session["department_id"],
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                    )

                    doctors = get_doctors_for_department(
                        cur,
                        session["department_id"],
                    )

                    return {
                        "patient": patient,
                        "department_id": session["department_id"],
                        "next_step": "SELECT_DOCTOR",
                        "doctors": doctors,
                    }

                if current_step == "SELECT_DATE":

                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_APPOINTMENT_TYPE",
                        department_id=session["department_id"],
                        doctor_id=session["doctor_id"],
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                    )

                    appointment_types = get_appointment_types_for_doctor(
                        cur,
                        session["doctor_id"],
                    )

                    return {
                        "patient": patient,
                        "doctor_id": session["doctor_id"],
                        "next_step": "SELECT_APPOINTMENT_TYPE",
                        "appointment_types": appointment_types,
                    }

                if current_step == "SELECT_SLOT":

                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_DATE",
                        department_id=session["department_id"],
                        doctor_id=session["doctor_id"],
                        appointment_type_id=session["appointment_type_id"],
                        selected_date=None,
                        selected_start_at=None,
                    )

                    available_dates = get_available_dates(
                        cur,
                        session["doctor_id"],
                        session["appointment_type_id"],
                    )
                    date_options = format_date_options(available_dates)

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DATE",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                if current_step == "CANCEL_SELECT":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_DEPARTMENT",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "departments": get_departments(cur),
                    }

                if current_step == "CANCEL_CONFIRM":
                    appointments = get_upcoming_scheduled_appointments(
                        cur,
                        patient["id"],
                    )

                    if not appointments:
                        update_session(
                            cur=cur,
                            session_id=session["id"],
                            step="SELECT_DEPARTMENT",
                            department_id=None,
                            doctor_id=None,
                            appointment_type_id=None,
                            selected_date=None,
                            selected_start_at=None,
                        )

                        return {
                            "patient": patient,
                            "next_step": "SELECT_DEPARTMENT",
                            "message": "You do not have any upcoming appointments to cancel.",
                            "departments": get_departments(cur),
                        }

                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="CANCEL_SELECT",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "CANCEL_SELECT",
                        "appointments": [
                            {
                                "number": number,
                                "doctor_name": appointment["doctor_name"],
                                "appointment_type_name": appointment["appointment_type_name"],
                                "start_at": appointment["start_at"].isoformat(),
                                "end_at": appointment["end_at"].isoformat(),
                            }
                            for number, appointment in enumerate(appointments, start=1)
                        ],
                        "message": cancellation_selection_message(appointments),
                    }

                if current_step == "CONFIRM_SCHEDULING":

                    slots = get_available_slots(
                        cur,
                        session["doctor_id"],
                        session["appointment_type_id"],
                        session["selected_date"],
                    )

                    if not slots:
                        update_session(
                            cur=cur,
                            session_id=session["id"],
                            step="SELECT_DATE",
                            department_id=session["department_id"],
                            doctor_id=session["doctor_id"],
                            appointment_type_id=session[
                                "appointment_type_id"
                            ],
                            selected_date=None,
                            selected_start_at=None,
                        )

                        available_dates = get_available_dates(
                            cur,
                            session["doctor_id"],
                            session["appointment_type_id"],
                        )
                        date_options = format_date_options(available_dates)

                        return {
                            "patient": patient,
                            "next_step": "SELECT_DATE",
                            "error": "No appointments are available on this date. Please choose another date.",
                            "date_options": date_options,
                            "message": date_selection_message(date_options),
                        }

                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_SLOT",
                        department_id=session["department_id"],
                        doctor_id=session["doctor_id"],
                        appointment_type_id=session["appointment_type_id"],
                        selected_date=session["selected_date"],
                        selected_start_at=None,
                    )

                    slot_options = format_slot_options(slots)

                    return {
                        "patient": patient,
                        "date": session["selected_date"].isoformat(),
                        "next_step": "SELECT_SLOT",
                        "slots": slot_options,
                        "message": slot_selection_message(slot_options),
                    }

            # =============================================================
            # SELECT SCHEDULING MODE (Doctor-First vs Date-First)
            # =============================================================
            # The fork inserted between "Book Appointment" and Department
            # selection. Both options land on the SAME SELECT_DEPARTMENT-
            # family step and the SAME get_departments() listing --
            # Department itself is identical content in both flows; only
            # which step it's stored as (and therefore what comes next)
            # differs.

            if session["step"] == "SELECT_SCHEDULING_MODE":

                if message == "1":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_DEPARTMENT",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "departments": get_departments(cur),
                    }

                if message == "2":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="SELECT_DEPARTMENT_DATE_FIRST",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        scheduling_mode="DATE_FIRST",
                    )

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT_DATE_FIRST",
                        "departments": get_departments(cur),
                    }

                return {
                    "patient": patient,
                    "next_step": "SELECT_SCHEDULING_MODE",
                    "error": "Please reply 1 or 2.",
                    "message": scheduling_mode_selection_message(),
                }

            # =============================================================
            # SELECT DEPARTMENT (Date-First)
            # =============================================================
            # Same department listing/validation as Doctor-First's
            # SELECT_DEPARTMENT below -- the only difference is what
            # comes next: Appointment Type before any doctor is chosen,
            # not a doctor list.

            if session["step"] == "SELECT_DEPARTMENT_DATE_FIRST":

                if not message.isdigit():
                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT_DATE_FIRST",
                        "error": "Please select a valid department number.",
                        "departments": get_departments(cur),
                    }

                department_number = int(message)
                departments = get_departments(cur)

                if (
                    department_number < 1
                    or department_number > len(departments)
                ):
                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT_DATE_FIRST",
                        "error": "Please select a valid department number.",
                        "departments": departments,
                    }

                department = departments[department_number - 1]

                appointment_types = get_appointment_types_for_department(
                    cur, department["id"]
                )

                if not appointment_types:
                    return {
                        "patient": patient,
                        "department": department,
                        "next_step": "SELECT_DEPARTMENT_DATE_FIRST",
                        "error": "No appointment types are currently available in this department.",
                        "departments": departments,
                    }

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="SELECT_APPOINTMENT_TYPE_DATE_FIRST",
                    department_id=department["id"],
                    doctor_id=None,
                    appointment_type_id=None,
                    selected_date=None,
                    selected_start_at=None,
                    scheduling_mode="DATE_FIRST",
                )

                return {
                    "patient": patient,
                    "department": department,
                    "next_step": "SELECT_APPOINTMENT_TYPE_DATE_FIRST",
                    "appointment_types": appointment_types,
                }

            # =============================================================
            # SELECT APPOINTMENT TYPE (Date-First)
            # =============================================================
            # Appointment Type MUST stay before Date -- duration is
            # per doctor+type (doctor_appointment_types.duration_minutes),
            # and every slot calculation downstream needs it.

            if session["step"] == "SELECT_APPOINTMENT_TYPE_DATE_FIRST":

                department_id = session["department_id"]
                appointment_types = get_appointment_types_for_department(
                    cur, department_id
                )

                if not message.isdigit():
                    return {
                        "patient": patient,
                        "department_id": department_id,
                        "next_step": "SELECT_APPOINTMENT_TYPE_DATE_FIRST",
                        "error": "Please select a valid appointment type number.",
                        "appointment_types": appointment_types,
                    }

                appointment_type_number = int(message)

                if (
                    appointment_type_number < 1
                    or appointment_type_number > len(appointment_types)
                ):
                    return {
                        "patient": patient,
                        "department_id": department_id,
                        "next_step": "SELECT_APPOINTMENT_TYPE_DATE_FIRST",
                        "error": "Please select a valid appointment type number.",
                        "appointment_types": appointment_types,
                    }

                appointment_type = appointment_types[appointment_type_number - 1]

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="SELECT_DATE_DATE_FIRST",
                    department_id=department_id,
                    doctor_id=None,
                    appointment_type_id=appointment_type["id"],
                    selected_date=None,
                    selected_start_at=None,
                    scheduling_mode="DATE_FIRST",
                )

                date_options = format_date_options(
                    get_available_dates_for_department(
                        cur, department_id, appointment_type["id"]
                    )
                )

                return {
                    "patient": patient,
                    "department_id": department_id,
                    "appointment_type": appointment_type,
                    "next_step": "SELECT_DATE_DATE_FIRST",
                    "date_options": date_options,
                    "message": date_selection_message(date_options),
                }

            # =============================================================
            # SELECT DATE (Date-First)
            # =============================================================
            # Aggregate calendar: a date is offered only if at least one
            # doctor in the department offering this appointment type has
            # a real slot on it (list_available_dates_for_department /
            # get_available_dates_for_department, both built on the exact
            # same get_available_slots() the Doctor-First calendar uses).

            if session["step"] == "SELECT_DATE_DATE_FIRST":

                department_id = session["department_id"]
                appointment_type_id = session["appointment_type_id"]

                available_dates = get_available_dates_for_department(
                    cur, department_id, appointment_type_id
                )
                date_options = format_date_options(available_dates)

                if not message.isdigit():
                    return {
                        "patient": patient,
                        "department_id": department_id,
                        "appointment_type_id": appointment_type_id,
                        "next_step": "SELECT_DATE_DATE_FIRST",
                        "error": "Please select a valid date number.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                date_number = int(message)

                if (
                    date_number < 1
                    or date_number > len(available_dates)
                ):
                    return {
                        "patient": patient,
                        "department_id": department_id,
                        "appointment_type_id": appointment_type_id,
                        "next_step": "SELECT_DATE_DATE_FIRST",
                        "error": "Please select a valid date number.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                selected_date = available_dates[date_number - 1]

                doctors_with_slots = list_doctors_with_slots_for_date(
                    cur, department_id, appointment_type_id, selected_date
                )

                if not doctors_with_slots:
                    # Graceful no-availability handling (not a blank/dead
                    # end): re-show the date list rather than a page with
                    # nothing selectable, same as Doctor-First's
                    # equivalent "No appointments are available on this
                    # date" case just below.
                    available_dates = get_available_dates_for_department(
                        cur, department_id, appointment_type_id
                    )
                    date_options = format_date_options(available_dates)

                    return {
                        "patient": patient,
                        "department_id": department_id,
                        "appointment_type_id": appointment_type_id,
                        "date": selected_date.isoformat(),
                        "next_step": "SELECT_DATE_DATE_FIRST",
                        "error": "No doctors have availability on this date. Please choose another date.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="SELECT_AVAILABLE_DOCTOR_DATE_FIRST",
                    department_id=department_id,
                    doctor_id=None,
                    appointment_type_id=appointment_type_id,
                    selected_date=selected_date,
                    selected_start_at=None,
                    scheduling_mode="DATE_FIRST",
                )

                doctor_options = format_available_doctors(doctors_with_slots)

                return {
                    "patient": patient,
                    "department_id": department_id,
                    "appointment_type_id": appointment_type_id,
                    "date": selected_date.isoformat(),
                    "next_step": "SELECT_AVAILABLE_DOCTOR_DATE_FIRST",
                    "doctors": doctor_options,
                    "message": available_doctors_selection_message(doctor_options),
                }

            # =============================================================
            # SELECT AVAILABLE DOCTOR (Date-First)
            # =============================================================
            # Picking a doctor here converges onto the existing SELECT_SLOT
            # step, unchanged -- department_id/doctor_id/appointment_type_
            # id/selected_date are now set exactly as Doctor-First would
            # have set them, so SELECT_SLOT's own logic (re-fetch slots,
            # validate, move to CONFIRM_SCHEDULING) needs no Date-First-
            # specific branch at all. scheduling_mode="DATE_FIRST" is carried
            # forward explicitly so SELECT_SLOT's "back" and CONFIRM_
            # SCHEDULING's fallbacks know to return here rather than to
            # SELECT_DATE (see _select_date_or_available_doctors_response).

            if session["step"] == "SELECT_AVAILABLE_DOCTOR_DATE_FIRST":

                department_id = session["department_id"]
                appointment_type_id = session["appointment_type_id"]
                selected_date = session["selected_date"]

                doctors_with_slots = list_doctors_with_slots_for_date(
                    cur, department_id, appointment_type_id, selected_date
                )
                doctor_options = format_available_doctors(doctors_with_slots)

                if not message.isdigit():
                    profile_reply = try_build_doctor_profile_reply(cur, message, doctors_with_slots)
                    if profile_reply is not None:
                        return {
                            "patient": patient,
                            "date": selected_date.isoformat(),
                            "next_step": "SELECT_AVAILABLE_DOCTOR_DATE_FIRST",
                            "doctors": doctor_options,
                            "message": profile_reply,
                        }

                    return {
                        "patient": patient,
                        "date": selected_date.isoformat(),
                        "next_step": "SELECT_AVAILABLE_DOCTOR_DATE_FIRST",
                        "error": "Please select a valid doctor number.",
                        "doctors": doctor_options,
                        "message": available_doctors_selection_message(doctor_options),
                    }

                doctor_number = int(message)

                if (
                    doctor_number < 1
                    or doctor_number > len(doctors_with_slots)
                ):
                    return {
                        "patient": patient,
                        "date": selected_date.isoformat(),
                        "next_step": "SELECT_AVAILABLE_DOCTOR_DATE_FIRST",
                        "error": "Please select a valid doctor number.",
                        "doctors": doctor_options,
                        "message": available_doctors_selection_message(doctor_options),
                    }

                chosen_doctor = doctors_with_slots[doctor_number - 1]

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="SELECT_SLOT",
                    department_id=department_id,
                    doctor_id=chosen_doctor["id"],
                    appointment_type_id=appointment_type_id,
                    selected_date=selected_date,
                    selected_start_at=None,
                    scheduling_mode="DATE_FIRST",
                )

                slot_options = format_slot_options(chosen_doctor["slots"])

                return {
                    "patient": patient,
                    "doctor_id": chosen_doctor["id"],
                    "appointment_type_id": appointment_type_id,
                    "date": selected_date.isoformat(),
                    "next_step": "SELECT_SLOT",
                    "slots": slot_options,
                    "message": slot_selection_message(slot_options),
                }

            # =============================================================
            # 6. SELECT DEPARTMENT
            # =============================================================

            if session["step"] == "SELECT_DEPARTMENT":

                if not message.isdigit():
                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "Please select a valid department number.",
                        "departments": get_departments(cur),
                    }

                department_number = int(message)

                departments = get_departments(cur)

                if (
                    department_number < 1
                    or department_number > len(departments)
                ):
                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "Please select a valid department number.",
                        "departments": departments,
                    }

                department = departments[
                    department_number - 1
                ]

                doctors = get_doctors_for_department(
                    cur,
                    department["id"],
                )

                if not doctors:
                    return {
                        "patient": patient,
                        "department": department,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "No doctors are currently available in this department.",
                        "departments": departments,
                    }

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="SELECT_DOCTOR",
                    department_id=department["id"],
                    doctor_id=None,
                    appointment_type_id=None,
                    selected_date=None,
                    selected_start_at=None,
                )

                return {
                    "patient": patient,
                    "department": department,
                    "next_step": "SELECT_DOCTOR",
                    "doctors": doctors,
                }

            # =============================================================
            # 7. SELECT DOCTOR
            # =============================================================

            if session["step"] == "SELECT_DOCTOR":

                if not message.isdigit():
                    doctors = get_doctors_for_department(
                        cur,
                        session["department_id"],
                    )

                    profile_reply = try_build_doctor_profile_reply(cur, message, doctors)
                    if profile_reply is not None:
                        return {
                            "patient": patient,
                            "department_id": session["department_id"],
                            "next_step": "SELECT_DOCTOR",
                            "doctors": doctors,
                            "message": profile_reply,
                        }

                    return {
                        "patient": patient,
                        "department_id": session["department_id"],
                        "next_step": "SELECT_DOCTOR",
                        "error": "Please select a valid doctor number.",
                        "doctors": doctors,
                    }

                doctor_number = int(message)

                doctors = get_doctors_for_department(
                    cur,
                    session["department_id"],
                )

                if (
                    doctor_number < 1
                    or doctor_number > len(doctors)
                ):
                    return {
                        "patient": patient,
                        "department_id": session["department_id"],
                        "next_step": "SELECT_DOCTOR",
                        "error": "Please select a valid doctor number.",
                        "doctors": doctors,
                    }

                doctor = doctors[
                    doctor_number - 1
                ]

                appointment_types = get_appointment_types_for_doctor(
                    cur,
                    doctor["id"],
                )

                if not appointment_types:
                    return {
                        "patient": patient,
                        "doctor": doctor,
                        "next_step": "SELECT_DOCTOR",
                        "error": "No appointment types are currently available for this doctor.",
                        "doctors": doctors,
                    }

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="SELECT_APPOINTMENT_TYPE",
                    department_id=session["department_id"],
                    doctor_id=doctor["id"],
                    appointment_type_id=None,
                    selected_date=None,
                    selected_start_at=None,
                )

                return {
                    "patient": patient,
                    "department_id": session["department_id"],
                    "doctor": doctor,
                    "next_step": "SELECT_APPOINTMENT_TYPE",
                    "appointment_types": appointment_types,
                }

            # =============================================================
            # 8. SELECT APPOINTMENT TYPE
            # =============================================================

            if session["step"] == "SELECT_APPOINTMENT_TYPE":

                if not message.isdigit():
                    appointment_types = get_appointment_types_for_doctor(
                        cur,
                        session["doctor_id"],
                    )

                    return {
                        "patient": patient,
                        "doctor_id": session["doctor_id"],
                        "next_step": "SELECT_APPOINTMENT_TYPE",
                        "error": "Please select a valid appointment type number.",
                        "appointment_types": appointment_types,
                    }

                appointment_type_number = int(message)

                appointment_types = get_appointment_types_for_doctor(
                    cur,
                    session["doctor_id"],
                )

                if (
                    appointment_type_number < 1
                    or appointment_type_number > len(appointment_types)
                ):
                    return {
                        "patient": patient,
                        "doctor_id": session["doctor_id"],
                        "next_step": "SELECT_APPOINTMENT_TYPE",
                        "error": "Please select a valid appointment type number.",
                        "appointment_types": appointment_types,
                    }

                appointment_type = appointment_types[
                    appointment_type_number - 1
                ]

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="SELECT_DATE",
                    department_id=session["department_id"],
                    doctor_id=session["doctor_id"],
                    appointment_type_id=appointment_type["id"],
                    selected_date=None,
                    selected_start_at=None,
                )

                available_dates = get_available_dates(
                    cur,
                    session["doctor_id"],
                    appointment_type["id"],
                )
                date_options = format_date_options(available_dates)

                return {
                    "patient": patient,
                    "doctor_id": session["doctor_id"],
                    "appointment_type": appointment_type,
                    "next_step": "SELECT_DATE",
                    "date_options": date_options,
                    "message": date_selection_message(date_options),
                }


            # =============================================================
            # RESCHEDULE DATE
            # =============================================================

            if session["step"] == "RESCHEDULE_DATE":

                available_dates = get_available_dates(
                    cur,
                    session["doctor_id"],
                    session["appointment_type_id"],
                )

                date_options = format_date_options(available_dates)

                if not message.isdigit():
                    return {
                        "patient": patient,
                        "next_step": "RESCHEDULE_DATE",
                        "error": "Please select a valid date number.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                date_number = int(message)

                if date_number < 1 or date_number > len(available_dates):
                    return {
                        "patient": patient,
                        "next_step": "RESCHEDULE_DATE",
                        "error": "Please select a valid date number.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                selected_date = available_dates[date_number - 1]

                slots = get_available_slots(
                    cur,
                    session["doctor_id"],
                    session["appointment_type_id"],
                    selected_date,
                )

                if not slots:
                    return {
                        "patient": patient,
                        "next_step": "RESCHEDULE_DATE",
                        "error": "No appointments are available on this date. Please choose another date.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="RESCHEDULE_SLOT",
                    department_id=None,
                    doctor_id=session["doctor_id"],
                    appointment_type_id=session["appointment_type_id"],
                    selected_date=selected_date,
                    selected_start_at=None,
                    selected_appointment_id=session["selected_appointment_id"],
                )

                slot_options = format_slot_options(slots)

                return {
                    "patient": patient,
                    "doctor_id": session["doctor_id"],
                    "appointment_type_id": session["appointment_type_id"],
                    "date": selected_date.isoformat(),
                    "next_step": "RESCHEDULE_SLOT",
                    "slots": slot_options,
                    "message": slot_selection_message(slot_options),
                }

            # =============================================================
            # RESCHEDULE SLOT
            # =============================================================

            if session["step"] == "RESCHEDULE_SLOT":

                slots = get_available_slots(
                    cur,
                    session["doctor_id"],
                    session["appointment_type_id"],
                    session["selected_date"],
                )

                slot_options = format_slot_options(slots)

                if not message.isdigit():
                    return {
                        "patient": patient,
                        "date": session["selected_date"].isoformat(),
                        "next_step": "RESCHEDULE_SLOT",
                        "error": "Please select a valid slot number.",
                        "slots": slot_options,
                        "message": slot_selection_message(slot_options),
                    }

                slot_number = int(message)

                if slot_number < 1 or slot_number > len(slots):
                    return {
                        "patient": patient,
                        "date": session["selected_date"].isoformat(),
                        "next_step": "RESCHEDULE_SLOT",
                        "error": "Please select a valid slot number.",
                        "slots": slot_options,
                        "message": slot_selection_message(slot_options),
                    }

                selected_slot = slots[slot_number - 1]

                selected_start_at = datetime.fromisoformat(
                    selected_slot["start_at"]
                )

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="RESCHEDULE_FINAL_CONFIRM",
                    department_id=None,
                    doctor_id=session["doctor_id"],
                    appointment_type_id=session["appointment_type_id"],
                    selected_date=session["selected_date"],
                    selected_start_at=selected_start_at,
                    selected_appointment_id=session["selected_appointment_id"],
                )

                return {
                    "patient": patient,
                    "doctor_id": session["doctor_id"],
                    "appointment_type_id": session["appointment_type_id"],
                    "date": session["selected_date"].isoformat(),
                    "start_at": selected_slot["start_at"],
                    "end_at": selected_slot["end_at"],
                    "next_step": "RESCHEDULE_FINAL_CONFIRM",
                    "message": (
                        "Please confirm your new appointment.\n\n"
                        f"Date: {session['selected_date'].strftime('%a, %d %b %Y')}\n"
                        f"Time: {selected_start_at.strftime('%-I:%M %p')}\n\n"
                        "Reply 1 to confirm rescheduling or 2 to cancel."
                    ),
                }
            # =============================================================
            # RESCHEDULE FINAL CONFIRMATION
            # =============================================================

            if session["step"] == "RESCHEDULE_FINAL_CONFIRM":

                if message.lower() == "back":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="RESCHEDULE_DATE",
                        department_id=None,
                        doctor_id=session["doctor_id"],
                        appointment_type_id=session["appointment_type_id"],
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=session[
                            "selected_appointment_id"
                        ],
                    )

                    available_dates = get_available_dates(
                        cur,
                        session["doctor_id"],
                        session["appointment_type_id"],
                    )

                    date_options = format_date_options(available_dates)

                    return {
                        "patient": patient,
                        "doctor_id": session["doctor_id"],
                        "appointment_type_id": session[
                            "appointment_type_id"
                        ],
                        "next_step": "RESCHEDULE_DATE",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                if message == "2":
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="MAIN_MENU",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                    return {
                        "patient": patient,
                        "next_step": "MAIN_MENU",
                        "message": "Your appointment was not rescheduled.\n\n" + main_menu_message(),
                    }

                if message != "1":
                    return {
                        "patient": patient,
                        "next_step": "RESCHEDULE_FINAL_CONFIRM",
                        "error": "Please reply 1 to confirm rescheduling or 2 to cancel.",
                        "message": (
                            "Please confirm your new appointment.\n\n"
                            f"Date: {session['selected_date'].strftime('%a, %d %b %Y')}\n"
                            f"Time: {session['selected_start_at'].strftime('%-I:%M %p')}\n\n"
                            "Reply 1 to confirm rescheduling or 2 to cancel."
                        ),
                    }

                # ---------------------------------------------------------
                # Reschedule via app.services.appointment_services'
                # reschedule_appointment_service -- the same function the
                # web reschedule endpoint (WEB P4) calls, instead of a
                # second, hand-maintained copy of these rules that could
                # silently drift from this one. Each typed exception
                # below is translated back into the exact conversational
                # response this handler always returned (verified against
                # the pre-refactor code line by line), with one
                # deliberate, minor consolidation: the original code had
                # two slightly different "not reschedulable" messages
                # depending on exactly when in the flow that was detected
                # -- an initial status check, and a redundant re-check
                # right before the cancel UPDATE that the initial check's
                # FOR UPDATE row lock makes practically unreachable. Both
                # now map to AlreadyCancelled and the same message; the
                # unreachable second wording is not preserved.
                # ---------------------------------------------------------

                new_start_at = session["selected_start_at"]

                if new_start_at is None:
                    clear_session(cur, whatsapp_number)

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "Your rescheduling session expired. Please try again.",
                        "departments": get_departments(cur),
                    }

                if new_start_at.tzinfo is None:
                    new_start_at = new_start_at.replace(
                        tzinfo=timezone.utc
                    )

                def _back_to_reschedule_date(error_message):
                    update_session(
                        cur=cur,
                        session_id=session["id"],
                        step="RESCHEDULE_DATE",
                        department_id=None,
                        doctor_id=session["doctor_id"],
                        appointment_type_id=session["appointment_type_id"],
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=session["selected_appointment_id"],
                    )

                    available_dates = get_available_dates(
                        cur,
                        session["doctor_id"],
                        session["appointment_type_id"],
                    )
                    date_options = format_date_options(available_dates)

                    return {
                        "patient": patient,
                        "next_step": "RESCHEDULE_DATE",
                        "error": error_message,
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                try:
                    result = reschedule_appointment_service(
                        cur,
                        session["selected_appointment_id"],
                        patient_id=patient["id"],
                        new_start_at=new_start_at,
                    )
                except AppointmentNotFound:
                    clear_session(cur, whatsapp_number)

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "That appointment could not be found.",
                        "departments": get_departments(cur),
                    }
                except AlreadyCancelled:
                    clear_session(cur, whatsapp_number)

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "That appointment is no longer available to reschedule.",
                        "departments": get_departments(cur),
                    }
                except AppointmentTypeNotAssigned:
                    clear_session(cur, whatsapp_number)

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "The selected appointment type is no longer available.",
                        "departments": get_departments(cur),
                    }
                except OutsideDoctorSchedule:
                    return _back_to_reschedule_date(
                        "That time is outside the doctor's working hours. Please choose another date or slot."
                    )
                except DoctorBlockConflict:
                    return _back_to_reschedule_date(
                        "That slot is no longer available because the doctor is unavailable. Please choose another date."
                    )
                except SlotOverlap:
                    return _back_to_reschedule_date(
                        "That slot was just booked by someone else. Please choose another date or slot."
                    )

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="MAIN_MENU",
                    department_id=None,
                    doctor_id=None,
                    appointment_type_id=None,
                    selected_date=None,
                    selected_start_at=None,
                    selected_appointment_id=None,
                )

                return {
                    "patient": patient,
                    "appointment": {
                        "id": result["id"],
                        "doctor_id": result["doctor_id"],
                        "patient_id": result["patient_id"],
                        "appointment_type_id": result["appointment_type_id"],
                        "start_at": result["start_at"],
                        "end_at": result["end_at"],
                        "status": result["status"],
                    },
                    "next_step": "RESCHEDULED",
                    "message": "Your appointment has been rescheduled successfully.\n\n" + main_menu_message(),
                }
            

            # =============================================================
            # 9. SELECT DATE
            # =============================================================

            if session["step"] == "SELECT_DATE":

                available_dates = get_available_dates(
                    cur,
                    session["doctor_id"],
                    session["appointment_type_id"],
                )
                date_options = format_date_options(available_dates)

                if not message.isdigit():
                    return {
                        "patient": patient,
                        "doctor_id": session["doctor_id"],
                        "appointment_type_id": session[
                            "appointment_type_id"
                        ],
                        "next_step": "SELECT_DATE",
                        "error": "Please select a valid date number.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                date_number = int(message)

                if (
                    date_number < 1
                    or date_number > len(available_dates)
                ):
                    return {
                        "patient": patient,
                        "doctor_id": session["doctor_id"],
                        "appointment_type_id": session[
                            "appointment_type_id"
                        ],
                        "next_step": "SELECT_DATE",
                        "error": "Please select a valid date number.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                selected_date = available_dates[date_number - 1]

                slots = get_available_slots(
                    cur,
                    session["doctor_id"],
                    session["appointment_type_id"],
                    selected_date,
                )

                if not slots:
                    available_dates = get_available_dates(
                        cur,
                        session["doctor_id"],
                        session["appointment_type_id"],
                    )
                    date_options = format_date_options(available_dates)

                    return {
                        "patient": patient,
                        "doctor_id": session["doctor_id"],
                        "appointment_type_id": session[
                            "appointment_type_id"
                        ],
                        "date": selected_date.isoformat(),
                        "next_step": "SELECT_DATE",
                        "error": "No appointments are available on this date. Please choose another date.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="SELECT_SLOT",
                    department_id=session["department_id"],
                    doctor_id=session["doctor_id"],
                    appointment_type_id=session["appointment_type_id"],
                    selected_date=selected_date,
                    selected_start_at=None,
                )

                slot_options = format_slot_options(slots)

                return {
                    "patient": patient,
                    "doctor_id": session["doctor_id"],
                    "appointment_type_id": session[
                        "appointment_type_id"
                    ],
                    "date": selected_date.isoformat(),
                    "next_step": "SELECT_SLOT",
                    "slots": slot_options,
                    "message": slot_selection_message(slot_options),
                }

            # =============================================================
            # 10. SELECT SLOT
            # =============================================================

            if session["step"] == "SELECT_SLOT":

                if not message.isdigit():
                    slots = get_available_slots(
                        cur,
                        session["doctor_id"],
                        session["appointment_type_id"],
                        session["selected_date"],
                    )
                    slot_options = format_slot_options(slots)

                    return {
                        "patient": patient,
                        "date": session["selected_date"].isoformat(),
                        "next_step": "SELECT_SLOT",
                        "error": "Please select a valid slot number.",
                        "slots": slot_options,
                        "message": slot_selection_message(slot_options),
                    }

                slot_number = int(message)

                slots = get_available_slots(
                    cur,
                    session["doctor_id"],
                    session["appointment_type_id"],
                    session["selected_date"],
                )

                if (
                    slot_number < 1
                    or slot_number > len(slots)
                ):
                    slot_options = format_slot_options(slots)

                    return {
                        "patient": patient,
                        "date": session["selected_date"].isoformat(),
                        "next_step": "SELECT_SLOT",
                        "error": "Please select a valid slot number.",
                        "slots": slot_options,
                        "message": slot_selection_message(slot_options),
                    }

                selected_slot = slots[
                    slot_number - 1
                ]

                selected_start_at = datetime.fromisoformat(
                    selected_slot["start_at"]
                )

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="CONFIRM_SCHEDULING",
                    department_id=session["department_id"],
                    doctor_id=session["doctor_id"],
                    appointment_type_id=session["appointment_type_id"],
                    selected_date=session["selected_date"],
                    selected_start_at=selected_start_at,
                    # Carry scheduling_mode forward -- SELECT_SLOT is a
                    # shared step reached from both flows, so this must
                    # not silently reset it to NULL (which would make
                    # CONFIRM_SCHEDULING's change/expired/blocked/occupied
                    # fallbacks wrongly treat a Date-First scheduling as
                    # Doctor-First). See migrations/0013.
                    scheduling_mode=session.get("scheduling_mode"),
                )

                appointment_type = get_appointment_type_for_doctor(
                    cur,
                    session["doctor_id"],
                    session["appointment_type_id"],
                )

                cur.execute(
                    """
                    SELECT id, name
                    FROM doctors
                    WHERE id = %s
                    """,
                    (session["doctor_id"],),
                )

                doctor_row = cur.fetchone()

                return {
                    "patient": patient,
                    "doctor": {
                        "id": doctor_row[0],
                        "name": doctor_row[1],
                    },
                    "appointment_type": appointment_type,
                    "date": session["selected_date"].isoformat(),
                    "start_at": selected_slot["start_at"],
                    "end_at": selected_slot["end_at"],
                    "next_step": "CONFIRM_SCHEDULING",
                    "message": "Please confirm your appointment. Reply 1 to confirm or 2 to change.",
                }

            # =============================================================
            # 11. CONFIRM SCHEDULING
            # =============================================================

            if session["step"] == "CONFIRM_SCHEDULING":

                if message == "2":
                    return _select_date_or_available_doctors_response(cur, patient, session)

                if message != "1":
                    return {
                        "patient": patient,
                        "next_step": "CONFIRM_SCHEDULING",
                        "error": "Please reply 1 to confirm or 2 to change.",
                    }

                # ---------------------------------------------------------
                # Re-check availability immediately before scheduling.
                #
                # This is important because another customer may have
                # scheduled the slot after the slot was originally shown.
                # ---------------------------------------------------------

                if session["selected_start_at"] is None:
                    return _select_date_or_available_doctors_response(
                        cur,
                        patient,
                        session,
                        error="Your booking session expired. Please select a date again.",
                    )

                appointment_type = get_appointment_type_for_doctor(
                    cur,
                    session["doctor_id"],
                    session["appointment_type_id"],
                )

                if appointment_type is None:
                    return {
                        "patient": patient,
                        "next_step": "ERROR",
                        "error": "The selected appointment type is no longer available.",
                    }

                duration_minutes = appointment_type[
                    "duration_minutes"
                ]

                start_at = session[
                    "selected_start_at"
                ]

                if start_at.tzinfo is None:
                    # Defensive fallback.
                    start_at = start_at.replace(
                        tzinfo=timezone.utc
                    )

                end_at = start_at + timedelta(
                    minutes=duration_minutes
                )

                # ---------------------------------------------------------
                # Get doctor timezone for validation
                # ---------------------------------------------------------

                try:
                    doctor_tz = get_doctor_timezone(cur, session["doctor_id"])
                except Exception as e:
                    logger.error(f"Failed to get timezone for scheduling confirmation: {e}")
                    doctor_tz = "Asia/Kolkata"

                # ---------------------------------------------------------
                # Re-check doctor blocks
                # ---------------------------------------------------------

                cur.execute(
                    """
                    SELECT start_at, end_at
                    FROM doctor_blocks
                    WHERE doctor_id = %s
                      AND active = TRUE
                      AND start_at < %s
                      AND end_at > %s
                    """,
                    (
                        session["doctor_id"],
                        end_at,
                        start_at,
                    ),
                )

                blocks = cur.fetchall()

                blocked = False

                for block_start, block_end in blocks:

                    block_start = ensure_aware_datetime(block_start, doctor_tz)
                    block_end = ensure_aware_datetime(block_end, doctor_tz)

                    if overlaps(
                        start_at,
                        end_at,
                        block_start,
                        block_end,
                    ):
                        blocked = True
                        break

                if blocked:
                    return _select_date_or_available_doctors_response(
                        cur,
                        patient,
                        session,
                        error="That slot is no longer available because the doctor is unavailable. Please choose another date or slot.",
                    )

                # ---------------------------------------------------------
                # Serialize scheduling attempts for this doctor.
                #
                # The earlier availability check is only advisory. Two
                # concurrent requests can both observe the slot as free unless
                # the final check and INSERT are serialized at the database
                # transaction level.
                # ---------------------------------------------------------

                cur.execute(
                    "SELECT pg_advisory_xact_lock(%s::bigint)",
                    (session["doctor_id"],),
                )

                # ---------------------------------------------------------
                # Re-check existing appointments after acquiring the lock.
                # ---------------------------------------------------------

                cur.execute(
                    """
                    SELECT start_at, end_at
                    FROM appointments
                    WHERE doctor_id = %s
                      AND start_at < %s
                      AND end_at > %s
                      -- status::text: see availability_engine.py's
                      -- get_available_slots for why (enum-typed
                      -- appointments.status on some databases).
                      AND NOT (status::text = ANY(%s::text[]))
                    """,
                    (
                        session["doctor_id"],
                        end_at,
                        start_at,
                        ["CANCELLED", "REJECTED"],
                    ),
                )

                existing_appointments = cur.fetchall()

                occupied = False

                for existing_start, existing_end in existing_appointments:

                    existing_start = ensure_aware_datetime(existing_start, doctor_tz)
                    existing_end = ensure_aware_datetime(existing_end, doctor_tz)

                    if overlaps(
                        start_at,
                        end_at,
                        existing_start,
                        existing_end,
                    ):
                        occupied = True
                        break

                if occupied:
                    return _select_date_or_available_doctors_response(
                        cur,
                        patient,
                        session,
                        error="That slot was just booked by someone else. Please choose another date or slot.",
                    )

                # ---------------------------------------------------------
                # Create appointment.
                #
                # Second line of defense below this INSERT: a
                # database-level EXCLUDE constraint on (doctor_id, time
                # range) for non-cancelled appointments (see
                # migrations/0003_prevent_overlapping_bookings.sql). The
                # advisory lock above should make it impossible for two
                # concurrent requests to both reach this INSERT for an
                # overlapping slot; the constraint is what guarantees
                # that even if some future code path ever bypassed the
                # lock. If it fires, the transaction is already aborted
                # by Postgres -- roll back explicitly before issuing any
                # further statements on this connection (including the
                # session update below), matching the existing
                # slot-taken response used earlier in this same
                # function.
                # ---------------------------------------------------------

                try:
                    cur.execute(
                        """
                        INSERT INTO appointments (
                            doctor_id,
                            patient_id,
                            appointment_type_id,
                            start_at,
                            end_at,
                            status,
                            booking_source
                        )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            'PENDING',
                            -- Patient self-service through the WhatsApp
                            -- bot -- same bucket as the web app's own
                            -- self-service booking (app/api/
                            -- patient_scheduling.py passes 'ONLINE' for
                            -- the identical reason). Not refactored to
                            -- go through create_appointment_service in
                            -- this phase -- see migrations/0023's
                            -- report for why this INSERT stays inline.
                            'ONLINE'
                        )
                        RETURNING
                            id,
                            doctor_id,
                            patient_id,
                            appointment_type_id,
                            start_at,
                            end_at,
                            status
                        """,
                        (
                            session["doctor_id"],
                            patient["id"],
                            session["appointment_type_id"],
                            start_at,
                            end_at,
                        ),
                    )
                except psycopg.errors.ExclusionViolation:
                    conn.rollback()
                    logger.warning(
                        f"Exclusion constraint rejected overlapping scheduling for "
                        f"doctor_id={session['doctor_id']} (advisory lock should "
                        f"normally prevent reaching this point -- backstop triggered)"
                    )

                    return _select_date_or_available_doctors_response(
                        cur,
                        patient,
                        session,
                        error="That slot was just booked by someone else. Please choose another date or slot.",
                    )

                row = cur.fetchone()

                # ---------------------------------------------------------
                # Return to the main menu after successful scheduling.
                # ---------------------------------------------------------

                update_session(
                    cur=cur,
                    session_id=session["id"],
                    step="MAIN_MENU",
                    department_id=None,
                    doctor_id=None,
                    appointment_type_id=None,
                    selected_date=None,
                    selected_start_at=None,
                    selected_appointment_id=None,
                )

                return {
                    "patient": patient,
                    "appointment": {
                        "id": row[0],
                        "doctor_id": row[1],
                        "patient_id": row[2],
                        "appointment_type_id": row[3],
                        "start_at": row[4].isoformat(),
                        "end_at": row[5].isoformat(),
                        "status": row[6],
                    },
                    "next_step": "SCHEDULED",
                    "message": (
                        "Your appointment request has been received and is awaiting "
                        "confirmation from our staff.\n\n" + main_menu_message()
                    ),
                }

            # =============================================================
            # Unknown state
            # =============================================================

            update_session(
                cur=cur,
                session_id=session["id"],
                step="MAIN_MENU",
                department_id=None,
                doctor_id=None,
                appointment_type_id=None,
                selected_date=None,
                selected_start_at=None,
                selected_appointment_id=None,
            )

            return {
                "patient": patient,
                "next_step": "MAIN_MENU",
                "message": "Let's start again.\n\n" + main_menu_message(),
            }