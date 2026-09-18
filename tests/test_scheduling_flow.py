"""
Integration tests for the WhatsApp conversation flow in app/api/scheduling.py,
driven exactly the way the real endpoint is: POST /api/scheduling with a
whatsapp_number and a message, state persisted server-side.

Reschedule/cancel tests use date_option="5" (the furthest offered date)
rather than "1" (today), so the scheduled appointment is unambiguously in
the future regardless of what time of day the suite happens to run --
get_upcoming_scheduled_appointments() filters on start_at > NOW().
"""

import psycopg

import app.api.scheduling as scheduling_module
from app.services.exceptions import SlotOverlap
from tests.helpers import seed_basic_doctor, register_patient, schedule_first_available_slot


def send(client, whatsapp_number, message):
    return client.post(
        "/api/scheduling", json={"whatsapp_number": whatsapp_number, "message": message}
    ).json()


def _drive_to_confirm(client, whatsapp_number, date_option="1", slot_option="1"):
    """Like schedule_first_available_slot, but stops at CONFIRM_SCHEDULING
    instead of sending the final "1" -- so a test can intervene (change
    the doctor's schedule, force an exception) between slot selection and
    confirmation."""
    msg = lambda m: client.post(
        "/api/scheduling", json={"whatsapp_number": whatsapp_number, "message": m}
    ).json()

    msg("1")  # Book Appointment -> SELECT_SCHEDULING_MODE
    msg("1")  # Choose a Doctor (Doctor-First)
    msg("1")  # department 1
    msg("1")  # doctor 1
    msg("1")  # appointment type 1
    msg(date_option)
    return msg(slot_option)  # now sitting at CONFIRM_SCHEDULING


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
    register_patient(client, number, "Scheduling Flow Patient")

    result = schedule_first_available_slot(client, number)

    assert result["next_step"] == "SCHEDULED"
    assert result["appointment"]["status"] == "PENDING"

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT status, encounter_id FROM appointments WHERE id = %s",
            (result["appointment"]["id"],),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "PENDING"
    # M3: the WhatsApp confirm path converged onto create_appointment_service
    # in R1, so it must get an encounter too. Not exposed in the WhatsApp
    # response itself (R1 kept that response to its original seven keys),
    # so checked directly against the database.
    encounter_id = row[1]
    assert encounter_id is not None

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM encounters WHERE id = %s", (encounter_id,))
        assert cur.fetchone()[0] == "OPEN"


def test_booking_uses_doctor_specific_timezone(client, db_connection):
    seed_basic_doctor(client, db_connection, timezone="America/New_York")
    number = "+919000000104"
    register_patient(client, number, "Timezone Patient")

    result = schedule_first_available_slot(client, number)

    assert result["next_step"] == "SCHEDULED"

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
    get_upcoming_scheduled_appointments() returned it as-is, and
    cancellation_details_message()/reschedule_selection_message() then
    displayed that wrong time verbatim. Confirmed live before the fix: a
    2:00 PM Asia/Kolkata (+05:30) scheduling displayed as "8:30 AM" in both
    messages. Uses America/New_York (a large, unambiguous offset from
    UTC, same choice as test_booking_uses_doctor_specific_timezone above)
    so a regression can't hide behind IST's own 5:30 offset coincidence.
    """
    seed_basic_doctor(client, db_connection, timezone="America/New_York")
    number = "+919000000199"
    register_patient(client, number, "TZ Display Patient")

    scheduled = schedule_first_available_slot(client, number, date_option="5")
    assert scheduled["next_step"] == "SCHEDULED"

    from datetime import datetime
    from zoneinfo import ZoneInfo

    scheduled_dt = datetime.fromisoformat(scheduled["appointment"]["start_at"])
    ny_time = scheduled_dt.astimezone(ZoneInfo("America/New_York"))
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

    scheduled = schedule_first_available_slot(client, number, date_option="5")
    appointment_id = scheduled["appointment"]["id"]

    send(client, number, "main menu")
    # With exactly one upcoming appointment, scheduling.py's cancel handler
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

    scheduled = schedule_first_available_slot(client, number, date_option="5")
    original_id = scheduled["appointment"]["id"]

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
    assert rows[new_id] == "PENDING"


def test_back_navigation_returns_to_previous_step(client, db_connection):
    seed_basic_doctor(client, db_connection)
    number = "+919000000107"
    register_patient(client, number, "Back Nav Patient")

    send(client, number, "1")  # book -> SELECT_SCHEDULING_MODE
    send(client, number, "1")  # Choose a Doctor
    send(client, number, "1")  # department
    at_doctor_type = send(client, number, "1")  # doctor -> SELECT_APPOINTMENT_TYPE
    assert at_doctor_type["next_step"] == "SELECT_APPOINTMENT_TYPE"

    back_once = send(client, number, "back")
    assert back_once["next_step"] == "SELECT_DOCTOR"

    back_twice = send(client, number, "back")
    assert back_twice["next_step"] == "SELECT_DEPARTMENT"

    back_thrice = send(client, number, "back")
    assert back_thrice["next_step"] == "SELECT_SCHEDULING_MODE"


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

    send(client, number, "1")  # book -> SELECT_SCHEDULING_MODE
    send(client, number, "1")  # Choose a Doctor -> SELECT_DEPARTMENT
    response = send(client, number, "not-a-number")
    assert response["next_step"] == "SELECT_DEPARTMENT"
    assert "error" in response


# ---------------------------------------------------------------------
# R1: WhatsApp confirm converged onto create_appointment_service() --
# exercising the two new failure modes that only exist because the
# service, unlike the inline code it replaced, re-checks the doctor's
# schedule and can raise SlotOverlap from its EXCLUDE-constraint
# backstop (not just its plain overlap re-check).
# ---------------------------------------------------------------------

def test_booking_rejects_when_doctor_schedule_removed_after_slot_selection(client, db_connection):
    """The service's OutsideDoctorSchedule check is a genuine behavior
    addition from R1: the inline code it replaced never re-checked the
    schedule at confirm time, only at slot-selection time. Removing the
    doctor's schedule row between selection and confirmation must now
    reject with the same conversational wording used for a block/overlap
    conflict, not crash or silently book anyway."""
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Schedule Removed")
    number = "+919000000211"
    register_patient(client, number, "Schedule Removed Patient")

    at_confirm = _drive_to_confirm(client, number, date_option="5")
    assert at_confirm["next_step"] == "CONFIRM_SCHEDULING"

    with db_connection.cursor() as cur:
        cur.execute("DELETE FROM doctor_schedule WHERE doctor_id = %s", (seeded["doctor_id"],))
    db_connection.commit()

    result = send(client, number, "1")

    assert result["next_step"] != "SCHEDULED"
    assert result["error"] == (
        "That slot is no longer available because the doctor's schedule has "
        "changed. Please choose another date or slot."
    )

    # The session must still be usable afterward -- this path returns via
    # _select_date_or_available_doctors_response, not an unhandled error.
    follow_up = send(client, number, "main menu")
    assert follow_up["next_step"] == "MAIN_MENU"


def test_slot_overlap_from_aborted_transaction_leaves_session_usable(client, db_connection, monkeypatch):
    """
    create_appointment_service raises SlotOverlap from two different
    internal causes: its plain post-lock overlap re-check (transaction
    still healthy -- already covered by
    test_concurrency.py::test_simultaneous_whatsapp_bookings_same_slot,
    which reliably hits this one via the advisory lock serializing two
    real racing confirms), and its EXCLUDE-constraint backstop underneath
    the INSERT (transaction left aborted by Postgres). Only the second
    needs the caller's explicit conn.rollback() before it can safely
    touch the session again -- see the R1 report's timezone/rollback
    findings and test_reschedule_service.py's
    test_rollback_after_exclusion_violation_restores_cursor_usability for
    the sibling case.

    Reaching the real EXCLUDE-constraint backstop through the actual
    advisory lock requires a second transaction to slip in during the
    narrow window between the service's post-lock re-check and its
    INSERT while bypassing the lock entirely -- exactly the kind of
    timing-dependent race tests/test_exclusion_constraint.py's Test D
    resorts to raw, lock-free connections for, and not worth chasing here
    with a flaky thread race. Instead, this monkeypatches
    create_appointment_service to reproduce the one property that
    actually matters to this test: a Postgres-level error occurred inside
    it (any error aborts a transaction the same way, not just
    ExclusionViolation specifically) and it raised SlotOverlap anyway --
    then proves the real scheduling.py code recovers correctly. Without
    its conn.rollback(), the assertions below fail with
    psycopg.errors.InFailedSqlTransaction instead of the assertions they
    write.
    """
    seed_basic_doctor(client, db_connection, doctor_name="Dr. Rollback WhatsApp")
    number = "+919000000212"
    register_patient(client, number, "Rollback WhatsApp Patient")

    at_confirm = _drive_to_confirm(client, number, date_option="5")
    assert at_confirm["next_step"] == "CONFIRM_SCHEDULING"

    def _fake_create_appointment_service(cur, **kwargs):
        try:
            cur.execute("SELECT 1/0")
        except psycopg.errors.DivisionByZero:
            pass
        raise SlotOverlap()

    monkeypatch.setattr(
        scheduling_module, "create_appointment_service", _fake_create_appointment_service
    )

    result = send(client, number, "1")

    assert result["next_step"] != "SCHEDULED"
    assert result["error"] == "That slot was just booked by someone else. Please choose another date or slot."

    follow_up = send(client, number, "main menu")
    assert follow_up["next_step"] == "MAIN_MENU"
