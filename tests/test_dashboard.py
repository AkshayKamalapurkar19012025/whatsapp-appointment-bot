"""
Tests for GET /api/dashboard/stats and GET /api/dashboard/trends (the
admin Dashboard landing page's summary counts and trend charts).

Appointments move through a real lifecycle (migrations/0011_appointment_
lifecycle_statuses.sql): PENDING -> CONFIRMED -> VISITED -> COMPLETED,
with PENDING -> REJECTED or PENDING/CONFIRMED -> CANCELLED as the two
"never happened" exits. /stats reports a count for each of those six
statuses plus today's/upcoming (PENDING or CONFIRMED only).

Appointments are inserted directly via SQL (not through
create_appointment_service/the booking API) so each row's start_at and
status can be controlled precisely without running into the service
layer's own schedule/booking-window/overlap validation, which isn't
what's under test here.
"""

import datetime as dt
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from tests.helpers import create_staff_and_get_headers, seed_basic_doctor


def _insert_patient(db_connection, *, name: str, whatsapp_number: str) -> int:
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO patients (name, whatsapp_number) VALUES (%s, %s) RETURNING id",
            (name, whatsapp_number),
        )
        patient_id = cur.fetchone()[0]
    db_connection.commit()
    return patient_id


def _insert_appointment(
    db_connection,
    *,
    doctor_id: int,
    patient_id: int,
    appointment_type_id: int,
    start_at,
    status: str = "CONFIRMED",
) -> int:
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO appointments
                (doctor_id, patient_id, appointment_type_id, start_at, end_at, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                doctor_id,
                patient_id,
                appointment_type_id,
                start_at,
                start_at + timedelta(minutes=30),
                status,
            ),
        )
        appointment_id = cur.fetchone()[0]
    db_connection.commit()
    return appointment_id


def test_dashboard_stats_requires_staff_auth(client):
    response = client.get("/api/dashboard/stats")
    assert response.status_code == 401


def test_dashboard_stats_readable_by_plain_staff(client, db_connection):
    # Not require_role("ADMIN") -- any authenticated STAFF session can
    # view these read-only counts, matching GET /api/appointments.
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    response = client.get("/api/dashboard/stats", headers=staff_headers)
    assert response.status_code == 200


def test_dashboard_stats_counts_by_status_and_time(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Dash",
        department_name="Dash Department",
        appointment_type_name="Dash Consultation",
    )
    doctor_id = seeded["doctor_id"]
    appointment_type_id = seeded["appointment_type_id"]
    patient_id = _insert_patient(db_connection, name="Dash Patient", whatsapp_number="+15550001111")

    now = dt.datetime.now(ZoneInfo("Asia/Kolkata"))

    # Today, still upcoming (Confirmed, later today).
    _insert_appointment(
        db_connection,
        doctor_id=doctor_id,
        patient_id=patient_id,
        appointment_type_id=appointment_type_id,
        start_at=now + timedelta(hours=1),
        status="CONFIRMED",
    )
    # Today, already in the past (Pending) -- counts as "today" but not
    # "upcoming"... wait, upcoming only requires start_at > now, so a
    # past appointment is never "upcoming" regardless of status.
    _insert_appointment(
        db_connection,
        doctor_id=doctor_id,
        patient_id=patient_id,
        appointment_type_id=appointment_type_id,
        start_at=now - timedelta(hours=1),
        status="PENDING",
    )
    # A future date (not today), Pending -- "upcoming" but not "today".
    _insert_appointment(
        db_connection,
        doctor_id=doctor_id,
        patient_id=patient_id,
        appointment_type_id=appointment_type_id,
        start_at=now + timedelta(days=5),
        status="PENDING",
    )
    # One of each remaining terminal/in-progress status -- none of these
    # count as "today" or "upcoming" regardless of date, but each must
    # land in its own bucket and in the lifetime total.
    _insert_appointment(
        db_connection,
        doctor_id=doctor_id,
        patient_id=patient_id,
        appointment_type_id=appointment_type_id,
        start_at=now + timedelta(days=3),
        status="CANCELLED",
    )
    _insert_appointment(
        db_connection,
        doctor_id=doctor_id,
        patient_id=patient_id,
        appointment_type_id=appointment_type_id,
        start_at=now + timedelta(days=2),
        status="REJECTED",
    )
    _insert_appointment(
        db_connection,
        doctor_id=doctor_id,
        patient_id=patient_id,
        appointment_type_id=appointment_type_id,
        start_at=now - timedelta(days=1),
        status="VISITED",
    )
    _insert_appointment(
        db_connection,
        doctor_id=doctor_id,
        patient_id=patient_id,
        appointment_type_id=appointment_type_id,
        start_at=now - timedelta(days=2),
        status="COMPLETED",
    )

    staff_headers = create_staff_and_get_headers(db_connection, role="ADMIN")
    response = client.get("/api/dashboard/stats", headers=staff_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["today_appointments"] == 2  # the two today, Confirmed + Pending
    assert data["upcoming_appointments"] == 2  # the two future, Confirmed + Pending
    assert data["total_appointments"] == 7
    assert data["pending_appointments"] == 2
    assert data["confirmed_appointments"] == 1
    assert data["cancelled_appointments"] == 1
    assert data["rejected_appointments"] == 1
    assert data["visited_appointments"] == 1
    assert data["completed_appointments"] == 1
    assert data["total_doctors"] == 1
    assert data["total_patients"] == 1


def test_dashboard_stats_today_is_per_doctor_local_timezone(client, db_connection):
    # Same UTC instant read as "today" for a doctor just past local
    # midnight, but as "yesterday" for a doctor several hours behind --
    # the per-doctor-local-day convention GET /api/appointments already
    # uses for date_from/date_to, applied here in SQL instead.
    tokyo = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Tokyo",
        department_name="Tokyo Department",
        appointment_type_name="Tokyo Consultation",
        timezone="Asia/Tokyo",
    )
    la = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. LA",
        department_name="LA Department",
        appointment_type_name="LA Consultation",
        timezone="America/Los_Angeles",
    )
    patient_id = _insert_patient(db_connection, name="TZ Patient", whatsapp_number="+15550002222")

    # Noon Tokyo time today is unambiguously "today" for the Tokyo
    # doctor. Tokyo (UTC+9) is always 16-17h ahead of Los Angeles
    # (UTC-7/-8), so the same instant lands the evening *before* in LA
    # local time -- whether that's also "today" for the LA doctor
    # depends on what LA's current wall-clock date already is relative
    # to Tokyo's, which is computed rather than assumed.
    tokyo_today = dt.datetime.now(ZoneInfo("Asia/Tokyo")).date()
    tokyo_noon = dt.datetime.combine(tokyo_today, dt.time(12, 0), tzinfo=ZoneInfo("Asia/Tokyo"))

    _insert_appointment(
        db_connection,
        doctor_id=tokyo["doctor_id"],
        patient_id=patient_id,
        appointment_type_id=tokyo["appointment_type_id"],
        start_at=tokyo_noon,
    )
    _insert_appointment(
        db_connection,
        doctor_id=la["doctor_id"],
        patient_id=patient_id,
        appointment_type_id=la["appointment_type_id"],
        start_at=tokyo_noon,
    )

    la_date_of_appointment = tokyo_noon.astimezone(ZoneInfo("America/Los_Angeles")).date()
    la_today = dt.datetime.now(ZoneInfo("America/Los_Angeles")).date()
    expected_today_count = 2 if la_date_of_appointment == la_today else 1

    staff_headers = create_staff_and_get_headers(db_connection, role="ADMIN")
    data = client.get("/api/dashboard/stats", headers=staff_headers).json()

    assert data["today_appointments"] == expected_today_count


def test_dashboard_trends_requires_staff_auth(client):
    response = client.get("/api/dashboard/trends")
    assert response.status_code == 401


def test_dashboard_trends_zero_fills_and_counts_by_created_at(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Trend",
        department_name="Trend Department",
        appointment_type_name="Trend Consultation",
    )
    patient_id = _insert_patient(db_connection, name="Trend Patient", whatsapp_number="+15550003333")

    _insert_appointment(
        db_connection,
        doctor_id=seeded["doctor_id"],
        patient_id=patient_id,
        appointment_type_id=seeded["appointment_type_id"],
        start_at=dt.datetime.now(dt.timezone.utc) + timedelta(days=10),
    )

    staff_headers = create_staff_and_get_headers(db_connection, role="ADMIN")
    response = client.get("/api/dashboard/trends", params={"days": 7}, headers=staff_headers)
    assert response.status_code == 200
    data = response.json()

    assert len(data["appointments"]) == 7
    assert len(data["patients"]) == 7

    today = date.today().isoformat()
    appointments_today = next(row for row in data["appointments"] if row["date"] == today)
    patients_today = next(row for row in data["patients"] if row["date"] == today)

    # Both rows were created (created_at defaults to NOW()) during this
    # test, so today's bucket must reflect them; every other day in the
    # 7-day window has no activity from this test and should read 0, not
    # be missing from the response.
    assert appointments_today["count"] == 1
    assert patients_today["count"] == 1
    assert all(row["count"] == 0 for row in data["appointments"] if row["date"] != today)
    assert all(row["count"] == 0 for row in data["patients"] if row["date"] != today)
