"""
Integration tests for the Date-First WhatsApp scheduling flow added to
app/api/scheduling.py:

    Department -> Appointment Type -> Date -> Available Doctors
        -> Select Doctor -> Slot -> Review -> Confirm

driven exactly the way the real endpoint is, same as
tests/test_booking_flow.py's Doctor-First tests. These specifically
cover what's NEW about this flow (the mode fork, aggregation across
doctors, and the mode-aware "back"/"change" targets at the SELECT_SLOT/
CONFIRM_SCHEDULING convergence point) -- slot computation itself,
timezones, and scheduling safety are already covered by
test_booking_flow.py, test_availability_engine.py, and
test_concurrency.py, and are exercised here only insofar as this flow
reuses them unchanged.
"""

import threading

from tests.helpers import (
    add_doctor_to_department,
    create_admin_and_get_headers,
    register_patient,
    seed_basic_doctor,
)


def send(client, whatsapp_number, message):
    return client.post(
        "/api/scheduling", json={"whatsapp_number": whatsapp_number, "message": message}
    ).json()


def test_select_booking_mode_offered_after_book_appointment(client, db_connection):
    seed_basic_doctor(client, db_connection)
    number = "+919700000001"
    register_patient(client, number, "Mode Choice Patient")

    response = send(client, number, "1")  # Book Appointment
    assert response["next_step"] == "SELECT_SCHEDULING_MODE"


def test_select_booking_mode_invalid_option_reprompts(client, db_connection):
    seed_basic_doctor(client, db_connection)
    number = "+919700000002"
    register_patient(client, number, "Mode Invalid Patient")

    send(client, number, "1")  # book -> SELECT_SCHEDULING_MODE
    response = send(client, number, "9")
    assert response["next_step"] == "SELECT_SCHEDULING_MODE"
    assert "error" in response


def test_date_first_full_flow_books_appointment(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Date First",
        department_name="Date First Dept",
        appointment_type_name="Date First Type",
    )
    number = "+919700000003"
    register_patient(client, number, "Date First Patient")

    send(client, number, "1")  # book -> SELECT_SCHEDULING_MODE
    send(client, number, "2")  # Find by Date -> SELECT_DEPARTMENT_DATE_FIRST
    at_type = send(client, number, "1")  # department -> SELECT_APPOINTMENT_TYPE_DATE_FIRST
    assert at_type["next_step"] == "SELECT_APPOINTMENT_TYPE_DATE_FIRST"
    assert any(t["id"] == seeded["appointment_type_id"] for t in at_type["appointment_types"])

    at_date = send(client, number, "1")  # appointment type -> SELECT_DATE_DATE_FIRST
    assert at_date["next_step"] == "SELECT_DATE_DATE_FIRST"
    assert at_date["date_options"]

    at_doctors = send(client, number, "1")  # date -> SELECT_AVAILABLE_DOCTOR_DATE_FIRST
    assert at_doctors["next_step"] == "SELECT_AVAILABLE_DOCTOR_DATE_FIRST"
    assert len(at_doctors["doctors"]) == 1
    assert at_doctors["doctors"][0]["id"] == seeded["doctor_id"]
    assert at_doctors["doctors"][0]["slot_count"] > 0

    at_slot = send(client, number, "1")  # doctor -> SELECT_SLOT (shared step)
    assert at_slot["next_step"] == "SELECT_SLOT"
    assert at_slot["slots"]

    at_confirm = send(client, number, "1")  # slot -> CONFIRM_SCHEDULING (shared step)
    assert at_confirm["next_step"] == "CONFIRM_SCHEDULING"

    scheduled = send(client, number, "1")  # confirm
    assert scheduled["next_step"] == "SCHEDULED"
    assert scheduled["appointment"]["status"] == "PENDING"
    assert scheduled["appointment"]["doctor_id"] == seeded["doctor_id"]

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT status, doctor_id FROM appointments WHERE id = %s",
            (scheduled["appointment"]["id"],),
        )
        row = cur.fetchone()
    assert row == ("PENDING", seeded["doctor_id"])


def test_date_first_lists_multiple_doctors_with_their_own_slot_counts(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Thirty",
        department_name="Multi Doctor WA Dept",
        appointment_type_name="Multi Doctor WA Type",
        duration_minutes=30,
        schedule_days=(1, 2, 3, 4, 5),
    )
    doctor_b_id = add_doctor_to_department(
        client,
        db_connection,
        department_id=seeded["department_id"],
        appointment_type_id=seeded["appointment_type_id"],
        doctor_name="Dr. Sixty",
        duration_minutes=60,
        schedule_days=(1, 2, 3, 4, 5),
    )
    number = "+919700000004"
    register_patient(client, number, "Multi Doctor Patient")

    send(client, number, "1")  # book -> SELECT_SCHEDULING_MODE
    send(client, number, "2")  # find by date -> SELECT_DEPARTMENT_DATE_FIRST
    send(client, number, "1")  # department -> SELECT_APPOINTMENT_TYPE_DATE_FIRST
    send(client, number, "1")  # appointment type -> SELECT_DATE_DATE_FIRST
    # pick the first offered date (a weekday, both doctors work Mon-Fri)
    at_doctors = send(client, number, "1")  # date -> SELECT_AVAILABLE_DOCTOR_DATE_FIRST

    assert at_doctors["next_step"] == "SELECT_AVAILABLE_DOCTOR_DATE_FIRST"
    doctors_by_id = {d["id"]: d for d in at_doctors["doctors"]}
    assert set(doctors_by_id.keys()) == {seeded["doctor_id"], doctor_b_id}
    # 8-hour schedule: 30-min doctor has 16 slots, 60-min doctor has 8.
    assert doctors_by_id[seeded["doctor_id"]]["slot_count"] == 16
    assert doctors_by_id[doctor_b_id]["slot_count"] == 8

    # Picking the second doctor must reach THAT doctor's own slots.
    doctor_b_number = next(
        d["number"] for d in at_doctors["doctors"] if d["id"] == doctor_b_id
    )
    at_slot = send(client, number, str(doctor_b_number))
    assert at_slot["next_step"] == "SELECT_SLOT"
    assert at_slot["doctor_id"] == doctor_b_id
    assert len(at_slot["slots"]) == 8


def test_date_first_back_navigation_through_full_chain(client, db_connection):
    seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Back Nav",
        department_name="Back Nav Dept",
        appointment_type_name="Back Nav Type",
    )
    number = "+919700000005"
    register_patient(client, number, "Back Nav Patient")

    send(client, number, "1")  # book
    send(client, number, "2")  # find by date
    send(client, number, "1")  # department
    send(client, number, "1")  # appointment type
    send(client, number, "1")  # date
    at_slot = send(client, number, "1")  # doctor -> SELECT_SLOT
    assert at_slot["next_step"] == "SELECT_SLOT"

    # SELECT_SLOT's "back" must return to SELECT_AVAILABLE_DOCTOR_DATE_FIRST
    # (pick a different doctor for the same date), not SELECT_DATE (the
    # Doctor-First per-doctor calendar) -- the one place a shared step
    # name needs booking_mode to know where "back" goes.
    back_from_slot = send(client, number, "back")
    assert back_from_slot["next_step"] == "SELECT_AVAILABLE_DOCTOR_DATE_FIRST"

    back_to_date = send(client, number, "back")
    assert back_to_date["next_step"] == "SELECT_DATE_DATE_FIRST"

    back_to_type = send(client, number, "back")
    assert back_to_type["next_step"] == "SELECT_APPOINTMENT_TYPE_DATE_FIRST"

    back_to_department = send(client, number, "back")
    assert back_to_department["next_step"] == "SELECT_DEPARTMENT_DATE_FIRST"

    back_to_mode = send(client, number, "back")
    assert back_to_mode["next_step"] == "SELECT_SCHEDULING_MODE"

    back_to_main_menu = send(client, number, "back")
    assert back_to_main_menu["next_step"] == "MAIN_MENU"


def test_date_first_confirm_change_returns_to_available_doctors(client, db_connection):
    seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Confirm Change",
        department_name="Confirm Change Dept",
        appointment_type_name="Confirm Change Type",
    )
    number = "+919700000006"
    register_patient(client, number, "Confirm Change Patient")

    send(client, number, "1")
    send(client, number, "2")
    send(client, number, "1")
    send(client, number, "1")
    send(client, number, "1")
    send(client, number, "1")  # doctor -> SELECT_SLOT
    send(client, number, "1")  # slot -> CONFIRM_SCHEDULING

    changed = send(client, number, "2")  # "change" while at CONFIRM_SCHEDULING
    assert changed["next_step"] == "SELECT_AVAILABLE_DOCTOR_DATE_FIRST"


def test_date_first_no_appointment_types_in_department_reprompts_gracefully(client, db_connection):
    """A department with a doctor assigned but that doctor offering no
    appointment type at all: SELECT_DEPARTMENT_DATE_FIRST must reprompt
    with a clear error, not proceed into a broken/empty Appointment Type
    step."""
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. No Types",
        department_name="No Types Dept",
        appointment_type_name="Unused Type",
    )
    # Remove the one appointment type assignment this doctor has, so the
    # department has a doctor but that doctor offers nothing.
    admin_headers = create_admin_and_get_headers(db_connection)
    client.delete(
        f"/api/doctors/{seeded['doctor_id']}"
        f"/appointment-types/{seeded['appointment_type_id']}",
        headers=admin_headers,
    )

    number = "+919700000007"
    register_patient(client, number, "No Types Patient")

    send(client, number, "1")
    send(client, number, "2")
    response = send(client, number, "1")  # department -> should reprompt
    assert response["next_step"] == "SELECT_DEPARTMENT_DATE_FIRST"
    assert "error" in response


def test_date_first_no_slots_ever_shows_empty_date_list_gracefully(client, db_connection):
    """A doctor whose schedule window is zero-length (start_time ==
    end_time) never has a real slot on any day -- SELECT_DATE_DATE_FIRST
    must show an empty, clearly-labeled date list (the same "No
    appointment dates are available" message Doctor-First already uses),
    never crash or show a blank/dead end, and any reply while there must
    reprompt safely rather than raising."""
    seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. No Slots Ever",
        department_name="No Slots Dept",
        appointment_type_name="No Slots Type",
        start_time="09:00",
        end_time="09:00",
        schedule_days=(1, 2, 3, 4, 5),
    )
    number = "+919700000009"
    register_patient(client, number, "No Slots Patient")

    send(client, number, "1")
    send(client, number, "2")
    send(client, number, "1")  # department
    at_date = send(client, number, "1")  # appointment type -> SELECT_DATE_DATE_FIRST

    assert at_date["next_step"] == "SELECT_DATE_DATE_FIRST"
    assert at_date["date_options"] == []
    assert "No appointment dates are available" in at_date["message"]

    # Nothing to select -- must reprompt with an error, not crash.
    response = send(client, number, "1")
    assert response["next_step"] == "SELECT_DATE_DATE_FIRST"
    assert "error" in response


def test_concurrent_date_first_bookings_same_slot(client, db_connection):
    """Two patients race, via the Date-First flow, to book the identical
    doctor/slot -- exactly test_concurrency.py's Test A, but reached via
    Date-First's extra steps, proving it converges onto the same
    advisory-lock-protected CONFIRM_SCHEDULING logic Doctor-First uses, not
    a separate, unprotected copy."""
    seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Race Date First",
        department_name="Race Date First Dept",
        appointment_type_name="Race Date First Type",
    )
    number_a = "+919700000010"
    number_b = "+919700000011"
    register_patient(client, number_a, "Racer A")
    register_patient(client, number_b, "Racer B")

    for number in (number_a, number_b):
        send(client, number, "1")  # book -> SELECT_SCHEDULING_MODE
        send(client, number, "2")  # find by date
        send(client, number, "1")  # department
        send(client, number, "1")  # appointment type
        send(client, number, "1")  # date
        send(client, number, "1")  # doctor -> SELECT_SLOT
        send(client, number, "1")  # slot -> CONFIRM_SCHEDULING

    results = {}
    barrier = threading.Barrier(2)

    def confirm(number, key):
        barrier.wait()
        results[key] = send(client, number, "1")

    threads = [
        threading.Thread(target=confirm, args=(number_a, "a")),
        threading.Thread(target=confirm, args=(number_b, "b")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    outcomes = [results["a"]["next_step"], results["b"]["next_step"]]
    assert outcomes.count("SCHEDULED") == 1
    # The loser is routed back to SELECT_AVAILABLE_DOCTOR_DATE_FIRST (pick
    # a different doctor/slot), not SELECT_DATE -- the Date-First-aware
    # fallback this whole feature added; see
    # _select_date_or_available_doctors_response in app/api/scheduling.py.
    assert outcomes.count("SELECT_AVAILABLE_DOCTOR_DATE_FIRST") == 1

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM appointments WHERE status NOT IN ('CANCELLED', 'REJECTED')"
        )
        assert cur.fetchone()[0] == 1
