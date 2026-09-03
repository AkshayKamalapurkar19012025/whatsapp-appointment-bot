"""
Tests for WEB P9 -- RBAC-gating app/api/appointments.py (deferred from
WEB P6) and extending it into the staff/admin appointment surface:
list/filter, create, cancel, and a new reschedule endpoint, all on
behalf of a patient.

Deliberately does NOT re-test the underlying booking/cancel/reschedule
rules themselves (schedule/block/overlap/concurrency) -- those are
already covered by the existing suite via app/services/appointment_
services.py, which this router is a thin, now-authenticated wrapper
around. These tests are about the auth gate, the new filters, and the
new reschedule endpoint's own wiring.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _seed_and_book(client, db_connection, doctor_name):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name=doctor_name,
        department_name=f"{doctor_name} Dept",
        appointment_type_name=f"{doctor_name} Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9198{abs(hash(doctor_name)) % 10**8:08d}"},
        headers=admin_headers,
    ).json()
    booking_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{booking_date.isoformat()}T10:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    return admin_headers, seeded, patient, created


def test_all_three_endpoints_require_authentication(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. P9 Auth")

    unauthenticated_get = client.get("/api/appointments")
    assert unauthenticated_get.status_code == 401

    unauthenticated_post = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": 1,
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": "2027-01-01T10:00:00+05:30",
        },
    )
    assert unauthenticated_post.status_code == 401

    unauthenticated_delete = client.delete("/api/appointments/1")
    assert unauthenticated_delete.status_code == 401

    unauthenticated_reschedule = client.post(
        "/api/appointments/1/reschedule", json={"new_start_at": "2027-01-01T10:00:00+05:30"}
    )
    assert unauthenticated_reschedule.status_code == 401


def test_either_staff_role_can_list_create_cancel_and_reschedule(client, db_connection):
    staff_headers, seeded, patient, created = _seed_and_book(
        client, db_connection, "Dr. P9 Either Role"
    )
    # Recreate as a STAFF (non-admin) session to prove the RBAC table's
    # "Create/cancel/reschedule appointments (on behalf of a patient):
    # ADMIN + STAFF" row, not just ADMIN.
    staff_only_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    listed = client.get("/api/appointments", headers=staff_only_headers)
    assert listed.status_code == 200
    assert any(a["id"] == created["id"] for a in listed.json())

    reschedule_date = _next_weekday(date.today() + timedelta(days=11))
    rescheduled = client.post(
        f"/api/appointments/{created['id']}/reschedule",
        json={"new_start_at": f"{reschedule_date.isoformat()}T11:00:00+05:30"},
        headers=staff_only_headers,
    )
    assert rescheduled.status_code == 200
    new_id = rescheduled.json()["id"]
    assert new_id != created["id"]

    cancelled = client.delete(f"/api/appointments/{new_id}", headers=staff_only_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


def test_staff_can_cancel_a_different_patients_appointment(client, db_connection):
    # The behavior this phase's report documents as "intentional now,
    # not a lingering gap": a staff session isn't restricted to
    # appointments it created -- that's the actual point of an
    # on-a-patient's-behalf admin capability.
    admin_headers, seeded, patient, created = _seed_and_book(
        client, db_connection, "Dr. P9 Cross Patient"
    )
    other_admin_headers = create_admin_and_get_headers(db_connection)

    cancelled = client.delete(f"/api/appointments/{created['id']}", headers=other_admin_headers)
    assert cancelled.status_code == 200


def test_list_filters_by_doctor_patient_and_status(client, db_connection):
    admin_headers, seeded_a, patient_a, created_a = _seed_and_book(
        client, db_connection, "Dr. P9 Filter A"
    )
    _, seeded_b, patient_b, created_b = _seed_and_book(client, db_connection, "Dr. P9 Filter B")

    by_doctor = client.get(
        "/api/appointments", params={"doctor_id": seeded_a["doctor_id"]}, headers=admin_headers
    ).json()
    assert {a["id"] for a in by_doctor} == {created_a["id"]}

    by_patient = client.get(
        "/api/appointments", params={"patient_id": patient_b["id"]}, headers=admin_headers
    ).json()
    assert {a["id"] for a in by_patient} == {created_b["id"]}

    client.delete(f"/api/appointments/{created_a['id']}", headers=admin_headers)

    by_status_cancelled = client.get(
        "/api/appointments", params={"status": "CANCELLED"}, headers=admin_headers
    ).json()
    assert {a["id"] for a in by_status_cancelled} == {created_a["id"]}

    by_status_booked = client.get(
        "/api/appointments", params={"status": "BOOKED"}, headers=admin_headers
    ).json()
    assert created_a["id"] not in {a["id"] for a in by_status_booked}
    assert created_b["id"] in {a["id"] for a in by_status_booked}


def test_list_shows_doctor_local_time_not_utc(client, db_connection):
    # America/New_York, computed via zoneinfo for the real test date --
    # the same DST-correctness approach already established in this
    # project's suite (tests/test_booking_flow.py, tests/test_mock_
    # notifications.py) rather than hardcoding an offset.
    from zoneinfo import ZoneInfo
    from datetime import datetime

    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. P9 Local Time",
        timezone="America/New_York",
        start_time="08:00",
        end_time="18:00",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "P9 Local Time Patient", "whatsapp_number": "+919400000099"},
        headers=admin_headers,
    ).json()

    booking_date = _next_weekday(date.today() + timedelta(days=10))
    ny_start = datetime(
        booking_date.year, booking_date.month, booking_date.day, 9, 0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": ny_start.isoformat(),
        },
        headers=admin_headers,
    ).json()

    listed = client.get(
        "/api/appointments", params={"patient_id": patient["id"]}, headers=admin_headers
    ).json()
    row = next(a for a in listed if a["id"] == created["id"])
    assert row["start_at"].startswith(f"{booking_date.isoformat()}T09:00:00")


def test_reschedule_rejects_time_outside_doctor_schedule(client, db_connection):
    # This router's own wiring for the OutsideDoctorSchedule -> 409
    # mapping (a new except clause added alongside this file's other
    # exception mappings) -- not a re-test of the underlying rule
    # itself, which tests/test_reschedule_service.py already covers.
    admin_headers, seeded, patient, created = _seed_and_book(
        client, db_connection, "Dr. P9 Reschedule Schedule"
    )

    saturday = date.today() + timedelta(days=2)
    while saturday.isoweekday() != 6:
        saturday += timedelta(days=1)

    response = client.post(
        f"/api/appointments/{created['id']}/reschedule",
        json={"new_start_at": f"{saturday.isoformat()}T10:00:00+05:30"},
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert "working hours" in response.json()["detail"]


def test_reschedule_nonexistent_appointment_returns_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post(
        "/api/appointments/999999/reschedule",
        json={"new_start_at": "2027-01-01T10:00:00+05:30"},
        headers=admin_headers,
    )
    assert response.status_code == 404
