"""
Integration tests for the WhatsApp conversation flow in app/api/booking.py,
driven exactly the way the real endpoint is: POST /api/booking with a
whatsapp_number and a message, state persisted server-side.

Reschedule/cancel tests use date_option="5" (the furthest offered date)
rather than "1" (today), so the booked appointment is unambiguously in
the future regardless of what time of day the suite happens to run --
get_upcoming_booked_appointments() filters on start_at > NOW().
"""

from tests.helpers import seed_basic_doctor, register_patient, book_first_available_slot


def send(client, whatsapp_number, message):
    return client.post(
        "/api/booking", json={"whatsapp_number": whatsapp_number, "message": message}
    ).json()


def test_registration_flow_for_new_patient(client):
    number = "+919000000101"

    first = send(client, number, "Hi")
    assert first["next_step"] == "REGISTER_PATIENT"

    second = send(client, number, "New Patient")
    assert second["next_step"] == "MAIN_MENU"
    assert second["patient"]["name"] == "New Patient"
    assert second["patient"]["whatsapp_number"] == number


def test_unregistered_number_cannot_reschedule_or_cancel(client):
    number = "+919000000102"

    response = send(client, number, "cancel")
    assert response["next_step"] == "REGISTER_PATIENT"
    assert "error" in response


def test_full_booking_flow_creates_booked_appointment(client, db_connection):
    seed_basic_doctor(client, db_connection)
    number = "+919000000103"
    register_patient(client, number, "Booking Flow Patient")

    result = book_first_available_slot(client, number)

    assert result["next_step"] == "BOOKED"
    assert result["appointment"]["status"] == "BOOKED"

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (result["appointment"]["id"],))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "BOOKED"


def test_booking_uses_doctor_specific_timezone(client, db_connection):
    seed_basic_doctor(client, db_connection, timezone="America/New_York")
    number = "+919000000104"
    register_patient(client, number, "Timezone Patient")

    result = book_first_available_slot(client, number)

    assert result["next_step"] == "BOOKED"

    # America/New_York in September is EDT (UTC-4). Convert the returned
    # instant to New York local time and confirm it's the doctor's 9am
    # schedule start, not 9am UTC or 9am IST.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    dt = datetime.fromisoformat(result["appointment"]["start_at"])
    ny_time = dt.astimezone(ZoneInfo("America/New_York"))
    assert ny_time.hour == 9


def test_cancel_and_reschedule_selection_show_doctor_local_time(client, db_connection):
    """
    Regression test for a real bug found while building WEB P4: an
    appointment's start_at, read back from the database (as opposed to
    freshly computed during slot selection), used to come back
    UTC-normalized regardless of the doctor's actual timezone --
    get_upcoming_booked_appointments() returned it as-is, and
    cancellation_details_message()/reschedule_selection_message() then
    displayed that wrong time verbatim. Confirmed live before the fix: a
    2:00 PM Asia/Kolkata (+05:30) booking displayed as "8:30 AM" in both
    messages. Uses America/New_York (a large, unambiguous offset from
    UTC, same choice as test_booking_uses_doctor_specific_timezone above)
    so a regression can't hide behind IST's own 5:30 offset coincidence.
    """
    seed_basic_doctor(client, db_connection, timezone="America/New_York")
    number = "+919000000199"
    register_patient(client, number, "TZ Display Patient")

    booked = book_first_available_slot(client, number, date_option="5")
    assert booked["next_step"] == "BOOKED"

    from datetime import datetime
    from zoneinfo import ZoneInfo

    booked_dt = datetime.fromisoformat(booked["appointment"]["start_at"])
    ny_time = booked_dt.astimezone(ZoneInfo("America/New_York"))
    expected_label = ny_time.strftime("%I:%M %p").lstrip("0")

    send(client, number, "main menu")
    # Single upcoming appointment -> both flows skip straight to their
    # confirm/selection screen (see the len(appointments) == 1 shortcuts).
    cancel_confirm = send(client, number, "3")
    assert cancel_confirm["next_step"] == "CANCEL_CONFIRM"
    assert expected_label in cancel_confirm["message"], (
        f"expected {expected_label!r} (doctor-local time) in cancellation "
        f"message, got: {cancel_confirm['message']!r}"
    )

    send(client, number, "main menu")
    reschedule_select = send(client, number, "2")
    assert reschedule_select["next_step"] in ("RESCHEDULE_SELECT", "RESCHEDULE_CONFIRM")
    assert expected_label in reschedule_select["message"], (
        f"expected {expected_label!r} (doctor-local time) in reschedule "
        f"selection message, got: {reschedule_select['message']!r}"
    )


def test_cancel_flow_marks_appointment_cancelled(client, db_connection):
    seed_basic_doctor(client, db_connection)
    number = "+919000000105"
    register_patient(client, number, "Cancel Flow Patient")

    booked = book_first_available_slot(client, number, date_option="5")
    appointment_id = booked["appointment"]["id"]

    send(client, number, "main menu")
    # With exactly one upcoming appointment, booking.py's cancel handler
    # skips the numbered CANCEL_SELECT list and goes straight to
    # CANCEL_CONFIRM (see the `if len(appointments) == 1:` shortcut) --
    # verified against the running app, not assumed.
    cancel_confirm = send(client, number, "3")
    assert cancel_confirm["next_step"] == "CANCEL_CONFIRM"

    cancelled = send(client, number, "1")
    assert cancelled["next_step"] == "CANCELLED"

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (appointment_id,))
        row = cur.fetchone()
    assert row[0] == "CANCELLED"


def test_reschedule_flow_cancels_old_and_books_new(client, db_connection):
    seed_basic_doctor(client, db_connection)
    number = "+919000000106"
    register_patient(client, number, "Reschedule Flow Patient")

    booked = book_first_available_slot(client, number, date_option="5")
    original_id = booked["appointment"]["id"]

    send(client, number, "main menu")
    # Unlike cancel, reschedule has no single-appointment shortcut -- it
    # always shows RESCHEDULE_SELECT and requires picking a number, even
    # with only one upcoming appointment. Verified against the running
    # app, not assumed (this asymmetry is real, existing behavior).
    select = send(client, number, "2")
    assert select["next_step"] == "RESCHEDULE_SELECT"

    pick_appointment = send(client, number, "1")
    assert pick_appointment["next_step"] == "RESCHEDULE_CONFIRM"

    confirm_intent = send(client, number, "1")
    assert confirm_intent["next_step"] == "RESCHEDULE_DATE"

    pick_date = send(client, number, "1")
    assert pick_date["next_step"] == "RESCHEDULE_SLOT"

    pick_slot = send(client, number, "3")
    assert pick_slot["next_step"] == "RESCHEDULE_FINAL_CONFIRM"

    result = send(client, number, "1")
    assert result["next_step"] == "RESCHEDULED"
    new_id = result["appointment"]["id"]
    assert new_id != original_id

    with db_connection.cursor() as cur:
        cur.execute("SELECT id, status FROM appointments ORDER BY id")
        rows = {row[0]: row[1] for row in cur.fetchall()}

    assert rows[original_id] == "CANCELLED"
    assert rows[new_id] == "BOOKED"


def test_back_navigation_returns_to_previous_step(client, db_connection):
    seed_basic_doctor(client, db_connection)
    number = "+919000000107"
    register_patient(client, number, "Back Nav Patient")

    send(client, number, "1")  # book
    send(client, number, "1")  # department
    at_doctor_type = send(client, number, "1")  # doctor -> SELECT_APPOINTMENT_TYPE
    assert at_doctor_type["next_step"] == "SELECT_APPOINTMENT_TYPE"

    back_once = send(client, number, "back")
    assert back_once["next_step"] == "SELECT_DOCTOR"

    back_twice = send(client, number, "back")
    assert back_twice["next_step"] == "SELECT_DEPARTMENT"


def test_restart_returns_to_main_menu(client, db_connection):
    seed_basic_doctor(client, db_connection)
    number = "+919000000108"
    register_patient(client, number, "Restart Patient")

    send(client, number, "1")
    send(client, number, "1")

    restarted = send(client, number, "restart")
    assert restarted["next_step"] == "MAIN_MENU"


def test_invalid_main_menu_option_reprompts_without_crashing(client, db_connection):
    seed_basic_doctor(client, db_connection)
    number = "+919000000109"
    register_patient(client, number, "Invalid Input Patient")

    response = send(client, number, "banana")
    assert response["next_step"] == "MAIN_MENU"
    assert "error" in response


def test_invalid_department_selection_reprompts(client, db_connection):
    seed_basic_doctor(client, db_connection)
    number = "+919000000110"
    register_patient(client, number, "Invalid Dept Patient")

    send(client, number, "1")  # book -> SELECT_DEPARTMENT
    response = send(client, number, "not-a-number")
    assert response["next_step"] == "SELECT_DEPARTMENT"
    assert "error" in response
