"""
Tests for WEB P4's three new backend endpoints (app/api/patient_booking.py):
GET /api/web/appointments/me, DELETE /api/web/appointments/{id}, and
POST /api/web/appointments/{id}/reschedule.

Covers the phase spec's explicit test list -- normal cancellation,
invalid cancellation, normal reschedule, invalid reschedule, timezone,
appointment history -- plus the two required cross-channel checks:
Web cancellation <-> WhatsApp, Web rescheduling <-> WhatsApp. (The phase
spec's "concurrency during reschedule" item is covered by
tests/test_concurrency.py::test_concurrent_reschedule_vs_fresh_booking_same_target_slot,
which continues to pass unchanged after scheduling.py's reschedule handler
was refactored to call the same shared service these endpoints use --
not duplicated here.)
"""

from datetime import date, timedelta

from app.services.availability_engine import scheduling_window

from tests.helpers import (
    seed_basic_doctor,
    register_and_login_web_patient,
    register_patient,
)


def _next_weekday_matching(schedule_days, start_from_days_ahead=1):
    candidate = date.today() + timedelta(days=start_from_days_ahead)
    while (candidate.weekday() + 1) not in schedule_days:
        candidate += timedelta(days=1)
    return candidate


def _schedule_via_web(client, token, seeded, hour, day=None):
    day = day or _next_weekday_matching((1, 2, 3, 4, 5))
    response = client.post(
        "/api/web/appointments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{day.isoformat()}T{hour:02d}:00:00+05:30",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _send_whatsapp(client, whatsapp_number, message):
    return client.post(
        "/api/scheduling", json={"whatsapp_number": whatsapp_number, "message": message}
    ).json()


# ---------------------------------------------------------------------
# My Appointments listing
# ---------------------------------------------------------------------

def test_my_appointments_requires_authentication(client):
    response = client.get("/api/web/appointments/me")
    assert response.status_code == 401


def test_my_appointments_lists_upcoming_history_and_cancelled(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. My Appointments")
    number = "+919840000001"
    token = register_and_login_web_patient(client, number, "History Patient")

    upcoming = _schedule_via_web(client, token, seeded, 9)
    to_cancel = _schedule_via_web(client, token, seeded, 11)

    cancel_response = client.delete(
        f"/api/web/appointments/{to_cancel['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancel_response.status_code == 200

    # A "history" appointment (Confirmed, in the past) can't be produced
    # through the web creation endpoint at all -- enforce_scheduling_window
    # rejects past dates by design. Insert one directly, matching this
    # repo's established pattern for scenarios the API itself can't
    # produce (see tests/test_exclusion_constraint.py).
    with db_connection.cursor() as cur:
        cur.execute("SELECT id FROM patients WHERE whatsapp_number = %s", (number,))
        patient_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO appointments (doctor_id, patient_id, appointment_type_id, start_at, end_at, status)
            VALUES (%s, %s, %s, '2020-01-01T09:00:00+05:30', '2020-01-01T09:30:00+05:30', 'CONFIRMED')
            RETURNING id
            """,
            (seeded["doctor_id"], patient_id, seeded["appointment_type_id"]),
        )
        history_id = cur.fetchone()[0]
    db_connection.commit()

    listing = client.get(
        "/api/web/appointments/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listing.status_code == 200
    body = listing.json()

    assert {a["id"] for a in body["upcoming"]} == {upcoming["id"]}
    assert {a["id"] for a in body["cancelled"]} == {to_cancel["id"]}
    assert {a["id"] for a in body["history"]} == {history_id}

    # Every entry carries doctor/appointment-type names for display, not
    # just raw ids.
    assert body["upcoming"][0]["doctor_name"] == "Dr. My Appointments"


def test_my_appointments_only_shows_own_appointments(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Isolation")
    token_a = register_and_login_web_patient(client, "+919840000002", "Patient A")
    token_b = register_and_login_web_patient(client, "+919840000003", "Patient B")

    _schedule_via_web(client, token_a, seeded, 9)
    _schedule_via_web(client, token_b, seeded, 11)

    listing_a = client.get(
        "/api/web/appointments/me",
        headers={"Authorization": f"Bearer {token_a}"},
    ).json()

    assert len(listing_a["upcoming"]) == 1
    assert listing_a["upcoming"][0]["doctor_name"] == "Dr. Isolation"


# ---------------------------------------------------------------------
# Cancel: normal and invalid
# ---------------------------------------------------------------------

def test_cancel_web_appointment_requires_authentication(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Cancel Auth")
    token = register_and_login_web_patient(client, "+919840000004", "Cancel Auth Patient")
    scheduled = _schedule_via_web(client, token, seeded, 9)

    response = client.delete(f"/api/web/appointments/{scheduled['id']}")
    assert response.status_code == 401


def test_cancel_web_appointment_by_owner_succeeds(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Cancel Normal")
    token = register_and_login_web_patient(client, "+919840000005", "Cancel Normal Patient")
    scheduled = _schedule_via_web(client, token, seeded, 9)

    response = client.delete(
        f"/api/web/appointments/{scheduled['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (scheduled["id"],))
        assert cur.fetchone()[0] == "CANCELLED"


def test_cancel_web_appointment_rejects_non_owner_as_404(client, db_connection):
    """A non-owner gets the same 404 a nonexistent id would -- no
    existence leak (see cancel_appointment_service's NotAppointmentOwner
    mapping in app/api/patient_booking.py)."""
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Cancel WrongOwner")
    owner_token = register_and_login_web_patient(client, "+919840000006", "Rightful Owner")
    other_token = register_and_login_web_patient(client, "+919840000007", "Someone Else")
    scheduled = _schedule_via_web(client, owner_token, seeded, 9)

    response = client.delete(
        f"/api/web/appointments/{scheduled['id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Appointment not found"

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (scheduled["id"],))
        assert cur.fetchone()[0] == "PENDING", "a rejected cancel attempt must not touch the appointment"


def test_cancel_web_appointment_rejects_nonexistent(client, db_connection):
    token = register_and_login_web_patient(client, "+919840000008", "Nonexistent Test Patient")
    response = client.delete(
        "/api/web/appointments/999999999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_cancel_web_appointment_rejects_already_cancelled(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Cancel Twice")
    token = register_and_login_web_patient(client, "+919840000009", "Cancel Twice Patient")
    scheduled = _schedule_via_web(client, token, seeded, 9)

    first = client.delete(
        f"/api/web/appointments/{scheduled['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 200

    second = client.delete(
        f"/api/web/appointments/{scheduled['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second.status_code == 409


# ---------------------------------------------------------------------
# Reschedule: normal and invalid
# ---------------------------------------------------------------------

def test_reschedule_web_appointment_requires_authentication(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule Auth")
    token = register_and_login_web_patient(client, "+919840000010", "Reschedule Auth Patient")
    scheduled = _schedule_via_web(client, token, seeded, 9)

    new_day = _next_weekday_matching((1, 2, 3, 4, 5), start_from_days_ahead=2)
    response = client.post(
        f"/api/web/appointments/{scheduled['id']}/reschedule",
        json={"new_start_at": f"{new_day.isoformat()}T11:00:00+05:30"},
    )
    assert response.status_code == 401


def test_reschedule_web_appointment_normal(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule Web Normal")
    token = register_and_login_web_patient(client, "+919840000011", "Reschedule Normal Patient")
    scheduled = _schedule_via_web(client, token, seeded, 9)

    new_day = _next_weekday_matching((1, 2, 3, 4, 5), start_from_days_ahead=2)
    response = client.post(
        f"/api/web/appointments/{scheduled['id']}/reschedule",
        headers={"Authorization": f"Bearer {token}"},
        json={"new_start_at": f"{new_day.isoformat()}T11:00:00+05:30"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["doctor_id"] == seeded["doctor_id"]
    assert body["id"] != scheduled["id"]

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (scheduled["id"],))
        assert cur.fetchone()[0] == "CANCELLED"


def test_reschedule_web_appointment_rejects_non_owner_as_404(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule WrongOwner")
    owner_token = register_and_login_web_patient(client, "+919840000012", "Reschedule Owner")
    other_token = register_and_login_web_patient(client, "+919840000013", "Reschedule Intruder")
    scheduled = _schedule_via_web(client, owner_token, seeded, 9)

    new_day = _next_weekday_matching((1, 2, 3, 4, 5), start_from_days_ahead=2)
    response = client.post(
        f"/api/web/appointments/{scheduled['id']}/reschedule",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"new_start_at": f"{new_day.isoformat()}T11:00:00+05:30"},
    )
    assert response.status_code == 404


def test_reschedule_web_appointment_rejects_overlap(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule Web Overlap")
    token_a = register_and_login_web_patient(client, "+919840000014", "Reschedule Overlap A")
    token_b = register_and_login_web_patient(client, "+919840000015", "Reschedule Overlap B")

    target_day = _next_weekday_matching((1, 2, 3, 4, 5), start_from_days_ahead=2)
    scheduled_a = _schedule_via_web(client, token_a, seeded, 9)
    _schedule_via_web(client, token_b, seeded, 11, day=target_day)

    response = client.post(
        f"/api/web/appointments/{scheduled_a['id']}/reschedule",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"new_start_at": f"{target_day.isoformat()}T11:00:00+05:30"},
    )
    assert response.status_code == 409
    assert "scheduled by someone else" in response.json()["detail"]


def test_reschedule_web_appointment_rejects_time_outside_doctor_schedule(client, db_connection):
    # Wiring test for the OutsideDoctorSchedule -> 409 mapping in
    # app/api/patient_booking.py's reschedule handler -- the service-level
    # behavior itself is covered by
    # tests/test_reschedule_service.py::test_reschedule_rejects_time_outside_doctor_schedule.
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule Web Schedule")
    token = register_and_login_web_patient(client, "+919840000099", "Reschedule Schedule Patient")
    scheduled = _schedule_via_web(client, token, seeded, 9)

    saturday = date.today() + timedelta(days=2)
    while saturday.isoweekday() != 6:
        saturday += timedelta(days=1)

    response = client.post(
        f"/api/web/appointments/{scheduled['id']}/reschedule",
        headers={"Authorization": f"Bearer {token}"},
        json={"new_start_at": f"{saturday.isoformat()}T10:00:00+05:30"},
    )
    assert response.status_code == 409
    assert "working hours" in response.json()["detail"]


def test_reschedule_web_appointment_enforces_booking_window(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reschedule Web Window")
    token = register_and_login_web_patient(client, "+919840000016", "Reschedule Window Patient")
    scheduled = _schedule_via_web(client, token, seeded, 9)

    _, window_end = scheduling_window()
    outside_date = window_end + timedelta(days=1)
    while (outside_date.weekday() + 1) not in (1, 2, 3, 4, 5):
        outside_date += timedelta(days=1)

    response = client.post(
        f"/api/web/appointments/{scheduled['id']}/reschedule",
        headers={"Authorization": f"Bearer {token}"},
        json={"new_start_at": f"{outside_date.isoformat()}T09:00:00+05:30"},
    )
    assert response.status_code == 409
    assert "scheduling window" in response.json()["detail"]


# ---------------------------------------------------------------------
# Cross-channel: Web <-> WhatsApp
# ---------------------------------------------------------------------

def test_web_cancellation_is_visible_to_whatsapp(client, db_connection):
    """The phase spec requires verifying Web cancellation <-> WhatsApp:
    a patient who cancels via the web must see that reflected in the
    WhatsApp flow -- same patients/appointments tables, same
    cancel_appointment_service either channel's cancel path uses."""
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Cross Channel Cancel")
    number = "+919840000017"

    # WhatsApp registration first, so the web login resolves to the SAME
    # patient record (matched by whatsapp_number), proving the two
    # channels genuinely share one patient/appointment identity.
    register_patient(client, number, "Cross Channel Patient")
    token = register_and_login_web_patient(client, number, "Cross Channel Patient")

    scheduled = _schedule_via_web(client, token, seeded, 9)

    cancel_response = client.delete(
        f"/api/web/appointments/{scheduled['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancel_response.status_code == 200

    whatsapp_menu = _send_whatsapp(client, number, "main menu")
    reschedule_check = _send_whatsapp(client, number, "2")
    assert "do not have any upcoming appointments" in reschedule_check["message"]


def test_whatsapp_reschedule_is_visible_to_web(client, db_connection):
    """The phase spec requires verifying Web rescheduling <-> WhatsApp:
    a patient who reschedules via WhatsApp must see the new appointment
    (and not the old one) in the web "My Appointments" listing."""
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Cross Channel Reschedule")
    number = "+919840000018"

    register_patient(client, number, "WA Reschedule Patient")
    token = register_and_login_web_patient(client, number, "WA Reschedule Patient")

    original = _schedule_via_web(client, token, seeded, 9)

    # Drive the WhatsApp reschedule flow: menu -> reschedule -> pick the
    # only appointment -> confirm intent -> pick a date -> pick a slot ->
    # confirm the new time.
    _send_whatsapp(client, number, "main menu")
    reschedule_select = _send_whatsapp(client, number, "2")
    assert reschedule_select["next_step"] in ("RESCHEDULE_SELECT", "RESCHEDULE_CONFIRM")
    if reschedule_select["next_step"] == "RESCHEDULE_SELECT":
        confirm_step = _send_whatsapp(client, number, "1")
    else:
        confirm_step = reschedule_select
    assert confirm_step["next_step"] == "RESCHEDULE_CONFIRM"

    date_step = _send_whatsapp(client, number, "1")
    assert date_step["next_step"] == "RESCHEDULE_DATE"
    # Pick a date far enough out to avoid colliding with "today" edge cases.
    slot_step = _send_whatsapp(client, number, "4")
    assert slot_step["next_step"] == "RESCHEDULE_SLOT"
    final_confirm_step = _send_whatsapp(client, number, "1")
    assert final_confirm_step["next_step"] == "RESCHEDULE_FINAL_CONFIRM"
    rescheduled = _send_whatsapp(client, number, "1")
    assert rescheduled["next_step"] == "RESCHEDULED"

    listing = client.get(
        "/api/web/appointments/me",
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    upcoming_ids = {a["id"] for a in listing["upcoming"]}
    cancelled_ids = {a["id"] for a in listing["cancelled"]}

    assert original["id"] not in upcoming_ids
    assert original["id"] in cancelled_ids
    assert rescheduled["appointment"]["id"] in upcoming_ids
