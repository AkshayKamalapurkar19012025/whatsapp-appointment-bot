"""
Tests for WEB P3's two new backend endpoints (app/api/patient_booking.py):
GET /api/web/calendar and POST /api/web/appointments.

Both are thin wrappers around already-tested WEB P1/P2 pieces
(availability_engine, create_appointment_service, get_current_patient) --
these tests are about the wiring (auth required, patient_id always from
the session, booking-window enforcement actually reachable now), not
about re-proving booking rules P1's own suite already covers.
"""

from datetime import date, timedelta

from app.services.availability_engine import booking_window

from tests.helpers import (
    create_admin_and_get_headers,
    seed_basic_doctor,
    register_and_login_web_patient as _register_and_login,
)


def test_calendar_returns_per_day_availability_matching_schedule(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Calendar",
        appointment_type_name="Calendar Consultation",
        schedule_days=(1, 2, 3, 4, 5),
    )

    today = date.today()
    response = client.get(
        "/api/web/calendar",
        params={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "year": today.year,
            "month": today.month,
        },
    )

    assert response.status_code == 200
    body = response.json()

    for iso_date, is_available in body["dates"].items():
        d = date.fromisoformat(iso_date)
        weekday = d.weekday() + 1
        if d < today:
            assert is_available is False, f"{iso_date} is in the past, must be unavailable"
        elif d == today:
            # Deliberately not asserted -- get_available_slots also
            # filters out a day's slots once the doctor's local clock
            # has passed schedule end_time (see the "past dates/times"
            # fix), so whether today itself still has open slots
            # depends on what time of day the suite runs, not just its
            # weekday. Every other date here is unaffected (always in
            # the future).
            continue
        else:
            expected = weekday in (1, 2, 3, 4, 5)
            assert is_available is expected, f"{iso_date}: expected {expected}, got {is_available}"


def test_calendar_rejects_month_entirely_outside_booking_window(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Far Future")

    _, window_end = booking_window()
    far_month = window_end + timedelta(days=32)  # safely into the month after the window

    response = client.get(
        "/api/web/calendar",
        params={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "year": far_month.year,
            "month": far_month.month,
        },
    )

    assert response.status_code == 409


def test_booking_requires_authentication(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Auth Required")

    response = client.post(
        "/api/web/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": "2026-09-07T09:00:00+05:30",
        },
    )

    assert response.status_code == 401


def test_booking_creates_appointment_for_authenticated_patient(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Web Booking",
        appointment_type_name="Web Booking Consultation",
        schedule_days=(1, 2, 3, 4, 5),
    )
    token = _register_and_login(client, "+919830000001", "Web Booking Patient")

    booking_date = date.today() + timedelta(days=1)
    while (booking_date.weekday() + 1) not in (1, 2, 3, 4, 5):
        booking_date += timedelta(days=1)

    response = client.post(
        "/api/web/appointments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{booking_date.isoformat()}T09:00:00+05:30",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["doctor_id"] == seeded["doctor_id"]

    with db_connection.cursor() as cur:
        cur.execute("SELECT patient_id FROM appointments WHERE id = %s", (body["id"],))
        patient_id = cur.fetchone()[0]

    me = client.get("/api/auth/patient/me", headers={"Authorization": f"Bearer {token}"})
    assert patient_id == me.json()["id"]


def test_booking_ignores_client_supplied_patient_id(client, db_connection):
    """The request body has no patient_id field -- even if a caller
    stuffs one in anyway, the appointment must belong to the
    session's own patient, never the supplied value."""
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. No Spoofing",
        appointment_type_name="No Spoofing Consultation",
        schedule_days=(1, 2, 3, 4, 5),
    )
    token = _register_and_login(client, "+919830000002", "Legit Patient")

    other = client.post(
        "/api/patients",
        json={"name": "Someone Else", "whatsapp_number": "+919830000099"},
        headers=create_admin_and_get_headers(db_connection),
    )
    other_patient_id = other.json()["id"]

    booking_date = date.today() + timedelta(days=1)
    while (booking_date.weekday() + 1) not in (1, 2, 3, 4, 5):
        booking_date += timedelta(days=1)

    response = client.post(
        "/api/web/appointments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{booking_date.isoformat()}T10:00:00+05:30",
            "patient_id": other_patient_id,  # not a real field -- must be ignored
        },
    )

    assert response.status_code == 200
    with db_connection.cursor() as cur:
        cur.execute("SELECT patient_id FROM appointments WHERE id = %s", (response.json()["id"],))
        booked_patient_id = cur.fetchone()[0]

    assert booked_patient_id != other_patient_id


def test_booking_enforces_calendar_window(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Window Enforced Web",
        appointment_type_name="Window Enforced Consultation",
        schedule_days=(1, 2, 3, 4, 5),
    )
    token = _register_and_login(client, "+919830000003", "Window Test Patient")

    _, window_end = booking_window()
    outside_date = window_end + timedelta(days=1)
    while (outside_date.weekday() + 1) not in (1, 2, 3, 4, 5):
        outside_date += timedelta(days=1)

    response = client.post(
        "/api/web/appointments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{outside_date.isoformat()}T09:00:00+05:30",
        },
    )

    assert response.status_code == 409
    assert "scheduling window" in response.json()["detail"]
