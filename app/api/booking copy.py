from datetime import date, datetime, time, timedelta, timezone
from functools import wraps

from fastapi import APIRouter
from pydantic import BaseModel

from app.db.connection import get_connection
from app.api.patients import insert_patient


router = APIRouter(
    prefix="/booking",
    tags=["Booking"],
)


class BookingRequest(BaseModel):
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
        """
        SELECT
            d.id,
            d.name
        FROM doctor_departments dd
        JOIN doctors d
            ON d.id = dd.doctor_id
        WHERE dd.department_id = %s
          AND d.active = TRUE
        ORDER BY d.name
        """,
        (department_id,),
    )

    rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
        }
        for row in rows
    ]


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


def get_appointment_type_for_doctor(
    cur,
    doctor_id: int,
    appointment_type_id: int,
):
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
          AND dat.appointment_type_id = %s
          AND dat.active = TRUE
          AND at.active = TRUE
        """,
        (
            doctor_id,
            appointment_type_id,
        ),
    )

    row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "duration_minutes": row[2],
    }


# -------------------------------------------------------------------------
# Booking session helpers
# -------------------------------------------------------------------------

def get_booking_session(cur, whatsapp_number: str):
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
            selected_appointment_id
        FROM booking_sessions
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
        INSERT INTO booking_sessions (
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
):
    
    cur.execute(
        """
        UPDATE booking_sessions
        SET
            step = %s,
            department_id = %s,
            doctor_id = %s,
            appointment_type_id = %s,
            selected_date = %s,
            selected_start_at = %s,
            selected_appointment_id = %s,
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
        UPDATE booking_sessions
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
        DELETE FROM booking_sessions
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


def make_aware_datetime(
    selected_date: date,
    selected_time: time,
    tzinfo,
):
    """
    Create a timezone-aware datetime.

    PostgreSQL TIMESTAMPTZ values returned by psycopg are timezone-aware.
    All datetimes used for availability comparisons must therefore also
    be timezone-aware.
    """

    return datetime.combine(
        selected_date,
        selected_time,
    ).replace(tzinfo=tzinfo)


def overlaps(
    start_at: datetime,
    end_at: datetime,
    existing_start: datetime,
    existing_end: datetime,
) -> bool:
    return (
        start_at < existing_end
        and end_at > existing_start
    )


def lock_doctor_for_booking(cur, doctor_id: int):
    """Serialize appointment writes for one doctor within the current transaction.

    Availability is intentionally checked before the INSERT, but that check alone
    is subject to a race condition when two requests run concurrently. PostgreSQL
    transaction-level advisory locking makes the final availability check and
    appointment write serialize for a given doctor without requiring a schema
    change. The lock is released automatically when the current transaction ends.
    """
    cur.execute(
        "SELECT pg_advisory_xact_lock(%s::bigint)",
        (doctor_id,),
    )


# -------------------------------------------------------------------------
# Availability
# -------------------------------------------------------------------------

def get_available_slots(
    cur,
    doctor_id: int,
    appointment_type_id: int,
    selected_date: date,
):
    """
    Calculate available appointment slots.

    Important:
    - Schedule times come from TIME columns and are naive.
    - Blocks and appointments come from TIMESTAMPTZ and are aware.
    - We convert schedule times into timezone-aware datetimes before
      comparing them with blocks/appointments.
    """

    # ---------------------------------------------------------
    # Get appointment duration
    # ---------------------------------------------------------

    appointment_type = get_appointment_type_for_doctor(
        cur,
        doctor_id,
        appointment_type_id,
    )

    if appointment_type is None:
        return []

    duration_minutes = appointment_type["duration_minutes"]

    # ---------------------------------------------------------
    # Get doctor timezone
    #
    # The current database schema does not have a doctor timezone
    # column, so we use the PostgreSQL session timezone.
    #
    # This is enough for the current MVP and, importantly, makes
    # all datetime comparisons timezone-aware.
    # ---------------------------------------------------------

    cur.execute(
        "SELECT current_setting('TIMEZONE')"
    )

    timezone_name = cur.fetchone()[0]

    # PostgreSQL normally returns something like:
    # Asia/Kolkata
    #
    # We cannot safely construct every IANA timezone ourselves
    # without zoneinfo, so use the connection's current timezone
    # offset for the requested date.

    cur.execute(
        """
        SELECT
            (%s::date::timestamp AT TIME ZONE current_setting('TIMEZONE'))
        """,
        (selected_date,),
    )

    timezone_midnight = cur.fetchone()[0]

    if timezone_midnight.tzinfo is None:
        # Defensive fallback.
        tzinfo = timezone.utc
    else:
        tzinfo = timezone_midnight.tzinfo

    # ---------------------------------------------------------
    # Weekday
    # Monday = 1 ... Sunday = 7
    # ---------------------------------------------------------

    day_of_week = selected_date.weekday() + 1

    # ---------------------------------------------------------
    # Doctor schedule
    # ---------------------------------------------------------

    cur.execute(
        """
        SELECT
            start_time,
            end_time
        FROM doctor_schedule
        WHERE doctor_id = %s
          AND day_of_week = %s
          AND active = TRUE
        ORDER BY start_time
        """,
        (
            doctor_id,
            day_of_week,
        ),
    )

    schedules = cur.fetchall()

    if not schedules:
        return []

    # ---------------------------------------------------------
    # Full requested day
    # ---------------------------------------------------------

    day_start = make_aware_datetime(
        selected_date,
        time.min,
        tzinfo,
    )

    day_end = day_start + timedelta(days=1)

    # ---------------------------------------------------------
    # Doctor blocks
    # ---------------------------------------------------------

    cur.execute(
        """
        SELECT
            start_at,
            end_at
        FROM doctor_blocks
        WHERE doctor_id = %s
          AND active = TRUE
          AND start_at < %s
          AND end_at > %s
        ORDER BY start_at
        """,
        (
            doctor_id,
            day_end,
            day_start,
        ),
    )

    blocks = cur.fetchall()

    # ---------------------------------------------------------
    # Existing appointments
    # ---------------------------------------------------------

    cur.execute(
        """
        SELECT
            start_at,
            end_at
        FROM appointments
        WHERE doctor_id = %s
          AND start_at < %s
          AND end_at > %s
          AND status <> 'CANCELLED'
        ORDER BY start_at
        """,
        (
            doctor_id,
            day_end,
            day_start,
        ),
    )

    appointments = cur.fetchall()

    # ---------------------------------------------------------
    # Calculate slots
    # ---------------------------------------------------------

    slots = []

    for schedule_start, schedule_end in schedules:

        # TIME values from PostgreSQL are naive.
        # Convert them to aware datetimes using the same timezone
        # as the database values.

        current_start = make_aware_datetime(
            selected_date,
            schedule_start,
            tzinfo,
        )

        schedule_end_at = make_aware_datetime(
            selected_date,
            schedule_end,
            tzinfo,
        )

        # Handle overnight schedules.
        if schedule_end_at <= current_start:
            schedule_end_at += timedelta(days=1)

        while (
            current_start
            + timedelta(minutes=duration_minutes)
            <= schedule_end_at
        ):
            current_end = (
                current_start
                + timedelta(minutes=duration_minutes)
            )

            slot_available = True

            # -------------------------------------------------
            # Check blocks
            # -------------------------------------------------

            for block_start, block_end in blocks:

                # Defensive normalization in case the DB driver
                # ever returns a naive value.

                if block_start.tzinfo is None:
                    block_start = block_start.replace(
                        tzinfo=tzinfo
                    )

                if block_end.tzinfo is None:
                    block_end = block_end.replace(
                        tzinfo=tzinfo
                    )

                if overlaps(
                    current_start,
                    current_end,
                    block_start,
                    block_end,
                ):
                    slot_available = False
                    break

            # -------------------------------------------------
            # Check appointments
            # -------------------------------------------------

            if slot_available:
                for appointment_start, appointment_end in appointments:

                    if appointment_start.tzinfo is None:
                        appointment_start = appointment_start.replace(
                            tzinfo=tzinfo
                        )

                    if appointment_end.tzinfo is None:
                        appointment_end = appointment_end.replace(
                            tzinfo=tzinfo
                        )

                    if overlaps(
                        current_start,
                        current_end,
                        appointment_start,
                        appointment_end,
                    ):
                        slot_available = False
                        break

            if slot_available:
                slots.append(
                    {
                        "start_at": current_start.isoformat(),
                        "end_at": current_end.isoformat(),
                    }
                )

            current_start = current_end

    return slots


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


def get_upcoming_booked_appointments(cur, patient_id: int):
    cur.execute(
        """
        SELECT
            a.id,
            a.doctor_id,
            d.name,
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
          AND a.status = 'BOOKED'
          AND a.start_at > NOW()
        ORDER BY a.start_at
        """,
        (patient_id,),
    )

    rows = cur.fetchall()

    return [
    {
        "id": row[0],
        "doctor_id": row[1],
        "doctor_name": row[2],
        "appointment_type_id": row[3],
        "appointment_type_name": row[4],
        "start_at": row[5],
        "end_at": row[6],
    }
    for row in rows
]


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

    The current /api/booking endpoint still accepts text input. The future
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
    elif next_step in {"BOOKED", "CANCELLED", "RESCHEDULED"}:
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
# Booking endpoint
# -------------------------------------------------------------------------

@router.post("")
@navigation_response
def booking(request: BookingRequest):

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

            session = get_booking_session(
                cur,
                whatsapp_number,
            )

            patient = get_patient(cur, whatsapp_number)

            # Rescheduling and cancellation are available only to
            # registered numbers with existing booked appointments.
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
                        step="SELECT_DEPARTMENT",
                        department_id=None,
                        doctor_id=None,
                        appointment_type_id=None,
                        selected_date=None,
                        selected_start_at=None,
                        selected_appointment_id=None,
                    )

                    departments = get_departments(cur)

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "departments": departments,
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
            # Navigation is handled centrally so every booking, reschedule,
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
                # BOOKING
                # ---------------------------------------------------------

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
                        "navigation": {
                            "back": None,
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                        "message": main_menu_message(),
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
                    return {
                        "patient": patient,
                        "doctor_id": doctor_id,
                        "appointment_type_id": appointment_type_id,
                        "next_step": "SELECT_DATE",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                        "navigation": {
                            "back": {"id": "BACK", "label": "Back"},
                            "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                        },
                    }

                if current_step == "CONFIRM_BOOKING":
                    doctor_id = session["doctor_id"]
                    appointment_type_id = session["appointment_type_id"]
                    selected_date = session["selected_date"]
                    slots = get_available_slots(
                        cur, doctor_id, appointment_type_id, selected_date
                    )
                    if not slots:
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
                        return {
                            "patient": patient,
                            "next_step": "SELECT_DATE",
                            "error": "No appointments are available on this date. Please choose another date.",
                            "date_options": date_options,
                            "message": date_selection_message(date_options),
                            "navigation": {
                                "back": {"id": "BACK", "label": "Back"},
                                "main_menu": {"id": "MAIN_MENU", "label": "Main Menu"},
                            },
                        }

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
                    appointments = get_upcoming_booked_appointments(cur, patient["id"])
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
                    appointments = get_upcoming_booked_appointments(cur, patient["id"])
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
                    appointment = get_upcoming_booked_appointments(cur, patient["id"])
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

                appointments = get_upcoming_booked_appointments(
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

                appointments = get_upcoming_booked_appointments(
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

                appointments = get_upcoming_booked_appointments(
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
                    appointments = get_upcoming_booked_appointments(
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
                    appointment = get_upcoming_booked_appointments(
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

                appointments = get_upcoming_booked_appointments(cur, patient["id"])

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
                    appointments = get_upcoming_booked_appointments(cur, patient["id"])

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
                    appointment = get_upcoming_booked_appointments(cur, patient["id"])
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
                    AND status = 'BOOKED'
                    RETURNING id, start_at, end_at
                    """,
    (
        session["selected_appointment_id"],
        patient["id"],
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
                    appointments = get_upcoming_booked_appointments(
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

                if current_step == "CONFIRM_BOOKING":

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
                # Validate the original appointment.
                # ---------------------------------------------------------

                cur.execute(
                    """
                    SELECT
                        id,
                        doctor_id,
                        patient_id,
                        appointment_type_id,
                        start_at,
                        end_at,
                        status
                    FROM appointments
                    WHERE id = %s
                      AND patient_id = %s
                    FOR UPDATE
                    """,
                    (
                        session["selected_appointment_id"],
                        patient["id"],
                    ),
                )

                original_appointment = cur.fetchone()

                if original_appointment is None:
                    clear_session(cur, whatsapp_number)

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "That appointment could not be found.",
                        "departments": get_departments(cur),
                    }

                if original_appointment[6] != "BOOKED":
                    clear_session(cur, whatsapp_number)

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "That appointment is no longer available to reschedule.",
                        "departments": get_departments(cur),
                    }

                # ---------------------------------------------------------
                # Validate the new appointment type.
                # ---------------------------------------------------------

                appointment_type = get_appointment_type_for_doctor(
                    cur,
                    session["doctor_id"],
                    session["appointment_type_id"],
                )

                if appointment_type is None:
                    clear_session(cur, whatsapp_number)

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "The selected appointment type is no longer available.",
                        "departments": get_departments(cur),
                    }

                duration_minutes = appointment_type[
                    "duration_minutes"
                ]

                start_at = session["selected_start_at"]

                if start_at is None:
                    clear_session(cur, whatsapp_number)

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "Your rescheduling session expired. Please try again.",
                        "departments": get_departments(cur),
                    }

                if start_at.tzinfo is None:
                    start_at = start_at.replace(
                        tzinfo=timezone.utc
                    )

                end_at = start_at + timedelta(
                    minutes=duration_minutes
                )

                # ---------------------------------------------------------
                # Re-check doctor blocks.
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

                    if block_start.tzinfo is None:
                        block_start = block_start.replace(
                            tzinfo=start_at.tzinfo
                        )

                    if block_end.tzinfo is None:
                        block_end = block_end.replace(
                            tzinfo=start_at.tzinfo
                        )

                    if overlaps(
                        start_at,
                        end_at,
                        block_start,
                        block_end,
                    ):
                        blocked = True
                        break

                if blocked:
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
                        "next_step": "RESCHEDULE_DATE",
                        "error": "That slot is no longer available because the doctor is unavailable. Please choose another date.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                # ---------------------------------------------------------
                # Serialize appointment changes for this doctor.
                # This keeps rescheduling from racing with another booking or
                # rescheduling request for the same doctor.
                # ---------------------------------------------------------

                lock_doctor_for_booking(cur, session["doctor_id"])

                # ---------------------------------------------------------
                # Re-check existing appointments.
                #
                # Exclude the original appointment because it is the
                # appointment being rescheduled.
                # ---------------------------------------------------------

                cur.execute(
                    """
                    SELECT start_at, end_at
                    FROM appointments
                    WHERE doctor_id = %s
                      AND start_at < %s
                      AND end_at > %s
                      AND status <> 'CANCELLED'
                      AND id <> %s
                    """,
                    (
                        session["doctor_id"],
                        end_at,
                        start_at,
                        session["selected_appointment_id"],
                    ),
                )

                existing_appointments = cur.fetchall()

                occupied = False

                for existing_start, existing_end in existing_appointments:

                    if existing_start.tzinfo is None:
                        existing_start = existing_start.replace(
                            tzinfo=start_at.tzinfo
                        )

                    if existing_end.tzinfo is None:
                        existing_end = existing_end.replace(
                            tzinfo=start_at.tzinfo
                        )

                    if overlaps(
                        start_at,
                        end_at,
                        existing_start,
                        existing_end,
                    ):
                        occupied = True
                        break

                if occupied:
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
                        "next_step": "RESCHEDULE_DATE",
                        "error": "That slot was just booked by someone else. Please choose another date or slot.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                # ---------------------------------------------------------
                # Cancel the old appointment.
                # ---------------------------------------------------------

                cur.execute(
                    """
                    UPDATE appointments
                    SET status = 'CANCELLED',
                        updated_at = NOW()
                    WHERE id = %s
                      AND patient_id = %s
                      AND status = 'BOOKED'
                    RETURNING id
                    """,
                    (
                        session["selected_appointment_id"],
                        patient["id"],
                    ),
                )

                cancelled = cur.fetchone()

                if cancelled is None:
                    clear_session(cur, whatsapp_number)

                    return {
                        "patient": patient,
                        "next_step": "SELECT_DEPARTMENT",
                        "error": "That appointment was already cancelled or is no longer available.",
                        "departments": get_departments(cur),
                    }

                # ---------------------------------------------------------
                # Create the new appointment.
                # ---------------------------------------------------------

                cur.execute(
                    """
                    INSERT INTO appointments (
                        doctor_id,
                        patient_id,
                        appointment_type_id,
                        start_at,
                        end_at,
                        status
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        'BOOKED'
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

                row = cur.fetchone()

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
                    step="CONFIRM_BOOKING",
                    department_id=session["department_id"],
                    doctor_id=session["doctor_id"],
                    appointment_type_id=session["appointment_type_id"],
                    selected_date=session["selected_date"],
                    selected_start_at=selected_start_at,
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
                    "next_step": "CONFIRM_BOOKING",
                    "message": "Please confirm your appointment. Reply 1 to confirm or 2 to change.",
                }

            # =============================================================
            # 11. CONFIRM BOOKING
            # =============================================================

            if session["step"] == "CONFIRM_BOOKING":

                if message == "2":
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

                if message != "1":
                    return {
                        "patient": patient,
                        "next_step": "CONFIRM_BOOKING",
                        "error": "Please reply 1 to confirm or 2 to change.",
                    }

                # ---------------------------------------------------------
                # Re-check availability immediately before booking.
                #
                # This is important because another customer may have
                # booked the slot after the slot was originally shown.
                # ---------------------------------------------------------

                if session["selected_start_at"] is None:
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
                        "error": "Your booking session expired. Please select a date again.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

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

                    if block_start.tzinfo is None:
                        block_start = block_start.replace(
                            tzinfo=start_at.tzinfo
                        )

                    if block_end.tzinfo is None:
                        block_end = block_end.replace(
                            tzinfo=start_at.tzinfo
                        )

                    if overlaps(
                        start_at,
                        end_at,
                        block_start,
                        block_end,
                    ):
                        blocked = True
                        break

                if blocked:
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
                        "error": "That slot is no longer available because the doctor is unavailable. Please choose another date or slot.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                # ---------------------------------------------------------
                # Serialize booking attempts for this doctor.
                #
                # The earlier availability check is only advisory. Two
                # concurrent requests can both observe the slot as free unless
                # the final check and INSERT are serialized at the database
                # transaction level.
                # ---------------------------------------------------------

                lock_doctor_for_booking(cur, session["doctor_id"])

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
                      AND status <> 'CANCELLED'
                    """,
                    (
                        session["doctor_id"],
                        end_at,
                        start_at,
                    ),
                )

                existing_appointments = cur.fetchall()

                occupied = False

                for existing_start, existing_end in existing_appointments:

                    if existing_start.tzinfo is None:
                        existing_start = existing_start.replace(
                            tzinfo=start_at.tzinfo
                        )

                    if existing_end.tzinfo is None:
                        existing_end = existing_end.replace(
                            tzinfo=start_at.tzinfo
                        )

                    if overlaps(
                        start_at,
                        end_at,
                        existing_start,
                        existing_end,
                    ):
                        occupied = True
                        break

                if occupied:
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
                        "error": "That slot was just booked by someone else. Please choose another date or slot.",
                        "date_options": date_options,
                        "message": date_selection_message(date_options),
                    }

                # ---------------------------------------------------------
                # Create appointment
                # ---------------------------------------------------------

                cur.execute(
                    """
                    INSERT INTO appointments (
                        doctor_id,
                        patient_id,
                        appointment_type_id,
                        start_at,
                        end_at,
                        status
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        'BOOKED'
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

                row = cur.fetchone()

                # ---------------------------------------------------------
                # Return to the main menu after successful booking.
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
                    "next_step": "BOOKED",
                    "message": "Your appointment has been booked successfully.\n\n" + main_menu_message(),
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