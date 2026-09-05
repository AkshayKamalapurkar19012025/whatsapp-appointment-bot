"""
Tests for the WhatsApp "PROFILE <number>" side-channel
(app/api/booking.py's try_build_doctor_profile_reply): an optional way
to view a doctor's full profile from either doctor-listing step
(SELECT_DOCTOR for Doctor-First, SELECT_AVAILABLE_DOCTOR_DATE_FIRST for
Date-First) without advancing, resetting, or otherwise changing the
booking session -- the reply always lands back on the exact same step,
still showing the same numbered list, ready for a normal numeric
selection to continue booking.
"""

from tests.helpers import (
    add_doctor_to_department,
    create_admin_and_get_headers,
    register_patient,
    seed_basic_doctor,
)


def send(client, whatsapp_number, message):
    return client.post(
        "/api/booking", json={"whatsapp_number": whatsapp_number, "message": message}
    ).json()


def _set_doctor_profile(db_connection, doctor_id, **fields):
    columns = ", ".join(f"{key} = %s" for key in fields)
    with db_connection.cursor() as cur:
        cur.execute(f"UPDATE doctors SET {columns} WHERE id = %s", (*fields.values(), doctor_id))
    db_connection.commit()


def _add_education(client, admin_headers, doctor_id, **entry):
    return client.post(
        f"/api/doctors/{doctor_id}/education", json=entry, headers=admin_headers
    ).json()


def test_profile_command_at_select_doctor_shows_profile_and_stays_on_step(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Profile WhatsApp")
    admin_headers = create_admin_and_get_headers(db_connection)
    _set_doctor_profile(
        db_connection,
        seeded["doctor_id"],
        specialization="Cardiology",
        years_of_experience=10,
        qualifications="MBBS, MD (Cardiology)",
    )
    entry = _add_education(
        client,
        admin_headers,
        seeded["doctor_id"],
        qualification="MBBS",
        institution="AIIMS",
        city="New Delhi",
        country="India",
        completion_year=2005,
    )
    client.post(f"/api/doctors/{seeded['doctor_id']}/education/{entry['id']}/feature", headers=admin_headers)

    number = "+919000000301"
    register_patient(client, number, "Profile Patient")

    send(client, number, "1")  # MAIN_MENU -> SELECT_BOOKING_MODE
    send(client, number, "1")  # Choose a Doctor -> SELECT_DEPARTMENT
    send(client, number, "1")  # department 1 -> SELECT_DOCTOR

    response = send(client, number, "PROFILE 1")

    assert response["next_step"] == "SELECT_DOCTOR"
    assert "error" not in response
    assert "Dr. Profile WhatsApp" in response["message"]
    assert "Cardiology" in response["message"]
    assert "10 years of experience" in response["message"]
    assert "MBBS, MD (Cardiology)" in response["message"]
    assert "AIIMS, New Delhi, India" in response["message"]

    # The session is unchanged -- a normal numeric reply still selects the
    # doctor and advances exactly as if PROFILE had never been sent.
    continued = send(client, number, "1")
    assert continued["next_step"] == "SELECT_APPOINTMENT_TYPE"


def test_profile_command_is_case_insensitive_and_accepts_lowercase(client, db_connection):
    seed_basic_doctor(client, db_connection, doctor_name="Dr. Case Insensitive")
    number = "+919000000302"
    register_patient(client, number, "Case Patient")

    send(client, number, "1")
    send(client, number, "1")
    send(client, number, "1")

    response = send(client, number, "profile 1")

    assert response["next_step"] == "SELECT_DOCTOR"
    assert "Dr. Case Insensitive" in response["message"]


def test_profile_command_out_of_range_falls_back_to_invalid_selection_error(client, db_connection):
    seed_basic_doctor(client, db_connection, doctor_name="Dr. Only One")
    number = "+919000000303"
    register_patient(client, number, "Range Patient")

    send(client, number, "1")
    send(client, number, "1")
    send(client, number, "1")

    response = send(client, number, "PROFILE 99")

    assert response["next_step"] == "SELECT_DOCTOR"
    assert response.get("error") == "Please select a valid doctor number."


def test_non_profile_non_numeric_message_still_shows_normal_invalid_selection_error(client, db_connection):
    seed_basic_doctor(client, db_connection, doctor_name="Dr. Gibberish")
    number = "+919000000304"
    register_patient(client, number, "Gibberish Patient")

    send(client, number, "1")
    send(client, number, "1")
    send(client, number, "1")

    response = send(client, number, "asdf")

    assert response["next_step"] == "SELECT_DOCTOR"
    assert response.get("error") == "Please select a valid doctor number."


def test_profile_command_at_select_available_doctor_date_first(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Date First Profile")
    admin_headers = create_admin_and_get_headers(db_connection)
    add_doctor_to_department(
        client,
        db_connection,
        department_id=seeded["department_id"],
        appointment_type_id=seeded["appointment_type_id"],
        doctor_name="Dr. Second Date First",
    )
    _set_doctor_profile(
        db_connection,
        seeded["doctor_id"],
        specialization="Pediatrics",
        years_of_experience=6,
    )

    number = "+919000000305"
    register_patient(client, number, "Date First Patient")

    send(client, number, "1")  # MAIN_MENU -> SELECT_BOOKING_MODE
    send(client, number, "2")  # Find by Date -> SELECT_DEPARTMENT_DATE_FIRST
    send(client, number, "1")  # department 1 -> SELECT_APPOINTMENT_TYPE_DATE_FIRST
    send(client, number, "1")  # appointment type 1 -> SELECT_DATE_DATE_FIRST
    date_response = send(client, number, "1")  # date option 1 -> SELECT_AVAILABLE_DOCTOR_DATE_FIRST
    assert date_response["next_step"] == "SELECT_AVAILABLE_DOCTOR_DATE_FIRST"

    response = send(client, number, "PROFILE 1")

    assert response["next_step"] == "SELECT_AVAILABLE_DOCTOR_DATE_FIRST"
    assert "error" not in response
    assert "Pediatrics" in response["message"]
    assert "6 years of experience" in response["message"]


def test_doctor_listing_message_includes_specialization_summary(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Summary Line")
    _set_doctor_profile(
        db_connection, seeded["doctor_id"], specialization="Orthopedics", years_of_experience=3
    )

    number = "+919000000306"
    register_patient(client, number, "Summary Patient")

    send(client, number, "1")
    send(client, number, "2")
    send(client, number, "1")
    send(client, number, "1")
    response = send(client, number, "1")

    assert response["next_step"] == "SELECT_AVAILABLE_DOCTOR_DATE_FIRST"
    assert "Orthopedics" in response["message"]
    assert "3 yrs exp" in response["message"]
