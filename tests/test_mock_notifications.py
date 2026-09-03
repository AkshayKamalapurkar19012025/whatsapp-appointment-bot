"""
Tests for WEB P8 -- generalizing mock_sms_outbox (previously OTP-only)
into a shared mock-notification outbox, and wiring web-originated
booking/cancel/reschedule to it via app/services/notifications.py.

Deliberately does NOT touch WhatsApp: booking.py's flow already
confirms actions live in the same chat and is not wired to this
module -- see app/services/notifications.py's module docstring for why.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tests.helpers import register_and_login_web_patient, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def test_booking_confirmation_notification_is_sent(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Notify Booking")
    number = "+919500000001"
    token = register_and_login_web_patient(client, number, "Notify Patient")

    booking_date = _next_weekday(date.today() + timedelta(days=10))
    response = client.post(
        "/api/web/appointments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{booking_date.isoformat()}T10:00:00+05:30",
        },
    )
    assert response.status_code == 200

    notification = client.get(
        "/api/web/notifications/_dev_lookup",
        params={"whatsapp_number": number, "kind": "BOOKING_CONFIRMATION"},
    )
    assert notification.status_code == 200
    body = notification.json()
    assert body["kind"] == "BOOKING_CONFIRMATION"
    assert "Dr. Notify Booking" in body["message"]
    assert "confirmed" in body["message"]
    assert "10:00 AM" in body["message"]


def test_cancellation_notification_shows_doctor_local_time(client, db_connection):
    # America/New_York doctor -- the same regression class WEB P3/P4
    # hit twice before (a value read back from Postgres is UTC-
    # normalized unless explicitly converted). If this notification
    # used the raw DB value without converting, it would show a time
    # several hours off from what was actually booked.
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Notify Cancel",
        timezone="America/New_York",
        start_time="08:00",
        end_time="18:00",
    )
    number = "+919500000002"
    token = register_and_login_web_patient(client, number, "Notify Cancel Patient")
    headers = {"Authorization": f"Bearer {token}"}

    booking_date = _next_weekday(date.today() + timedelta(days=10))
    # Compute America/New_York's actual UTC offset for this date rather
    # than assuming EDT (-04:00) -- the same DST-correctness fix this
    # project's own test suite already applies elsewhere (see
    # tests/test_booking_flow.py) after being bitten by hardcoding it.
    ny_start = datetime(
        booking_date.year, booking_date.month, booking_date.day, 9, 0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    created = client.post(
        "/api/web/appointments",
        headers=headers,
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": ny_start.isoformat(),
        },
    ).json()

    cancelled = client.delete(f"/api/web/appointments/{created['id']}", headers=headers)
    assert cancelled.status_code == 200

    notification = client.get(
        "/api/web/notifications/_dev_lookup",
        params={"whatsapp_number": number, "kind": "CANCELLATION"},
    ).json()
    assert "Dr. Notify Cancel" in notification["message"]
    assert "cancelled" in notification["message"]
    assert "9:00 AM" in notification["message"]


def test_reschedule_notification_shows_new_time(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Notify Reschedule")
    number = "+919500000003"
    token = register_and_login_web_patient(client, number, "Notify Reschedule Patient")
    headers = {"Authorization": f"Bearer {token}"}

    first_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/web/appointments",
        headers=headers,
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{first_date.isoformat()}T09:00:00+05:30",
        },
    ).json()

    second_date = _next_weekday(first_date + timedelta(days=1))
    rescheduled = client.post(
        f"/api/web/appointments/{created['id']}/reschedule",
        headers=headers,
        json={"new_start_at": f"{second_date.isoformat()}T11:00:00+05:30"},
    )
    assert rescheduled.status_code == 200

    notification = client.get(
        "/api/web/notifications/_dev_lookup",
        params={"whatsapp_number": number, "kind": "RESCHEDULE"},
    ).json()
    assert "Dr. Notify Reschedule" in notification["message"]
    assert "rescheduled" in notification["message"]
    assert "11:00 AM" in notification["message"]


def test_otp_dev_lookup_is_unaffected_by_a_later_booking_notification(client, db_connection):
    # The regression this phase had to guard against: before filtering
    # by kind, the OTP dev-lookup endpoint would silently start
    # returning a booking-confirmation row instead of the OTP once one
    # existed for the same number (both live in mock_sms_outbox, sorted
    # by recency).
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Notify Kind Guard")
    number = "+919500000004"
    token = register_and_login_web_patient(client, number, "Notify Kind Guard Patient")

    booking_date = _next_weekday(date.today() + timedelta(days=10))
    client.post(
        "/api/web/appointments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{booking_date.isoformat()}T10:00:00+05:30",
        },
    )

    # A booking confirmation now exists as the most recent
    # mock_sms_outbox row for this number -- request a fresh OTP and
    # confirm the OTP-specific dev-lookup still returns it, not the
    # booking confirmation.
    client.post("/api/auth/patient/otp/request", json={"whatsapp_number": number})
    otp_lookup = client.get(
        "/api/auth/patient/otp/_dev_lookup", params={"whatsapp_number": number}
    )
    assert otp_lookup.status_code == 200
    assert otp_lookup.json()["otp_code"]
    assert "otp_code" in otp_lookup.json()
    assert "confirmed" not in otp_lookup.json()["message"]


def test_notifications_dev_lookup_disabled_in_production(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    try:
        response = client.get(
            "/api/web/notifications/_dev_lookup",
            params={"whatsapp_number": "+919500000005"},
        )
    finally:
        monkeypatch.undo()

    assert response.status_code == 404


def test_notifications_dev_lookup_without_kind_returns_most_recent(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Notify Any Kind")
    number = "+919500000006"
    token = register_and_login_web_patient(client, number, "Notify Any Kind Patient")
    headers = {"Authorization": f"Bearer {token}"}

    booking_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/web/appointments",
        headers=headers,
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{booking_date.isoformat()}T10:00:00+05:30",
        },
    ).json()
    client.delete(f"/api/web/appointments/{created['id']}", headers=headers)

    latest = client.get(
        "/api/web/notifications/_dev_lookup", params={"whatsapp_number": number}
    ).json()
    assert latest["kind"] == "CANCELLATION"


def test_notifications_dev_lookup_404_for_unknown_number(client):
    response = client.get(
        "/api/web/notifications/_dev_lookup",
        params={"whatsapp_number": "+919500000099"},
    )
    assert response.status_code == 404
