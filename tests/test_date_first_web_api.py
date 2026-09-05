"""
Tests for the Date-First web REST endpoints:

    GET /api/departments/{id}/appointment-types  (department_appointment_types.py)
    GET /api/web/calendar/department              (aggregate month calendar)
    GET /api/web/availability/by-date              (doctors + slots for one date)

All three are thin wrappers around the already-tested
app/services/availability_engine.py aggregation functions (see
tests/test_date_first_availability.py) -- these tests focus on the HTTP
layer itself: status codes, response shape, and window enforcement,
mirroring the existing GET /api/web/calendar tests' style.
"""

from datetime import date, timedelta

from tests.helpers import add_doctor_to_department, seed_basic_doctor


def test_department_appointment_types_endpoint_lists_types(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Endpoint Types",
        department_name="Endpoint Types Dept",
        appointment_type_name="Endpoint Type",
    )

    response = client.get(f"/api/departments/{seeded['department_id']}/appointment-types")
    assert response.status_code == 200
    body = response.json()
    assert any(t["id"] == seeded["appointment_type_id"] for t in body)


def test_department_appointment_types_endpoint_404_for_missing_department(client, db_connection):
    response = client.get("/api/departments/999999/appointment-types")
    assert response.status_code == 404


def test_web_department_calendar_matches_single_doctor_availability(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Web Calendar",
        department_name="Web Calendar Dept",
        appointment_type_name="Web Calendar Type",
        schedule_days=(1, 2, 3, 4, 5),
    )

    today = date.today()
    response = client.get(
        "/api/web/calendar/department",
        params={
            "department_id": seeded["department_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "year": today.year,
            "month": today.month,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["department_id"] == seeded["department_id"]

    for iso_date, is_available in body["dates"].items():
        row_date = date.fromisoformat(iso_date)
        weekday = row_date.weekday() + 1
        if row_date < today:
            assert is_available is False
        elif row_date == today:
            # Deliberately not asserted: get_available_slots also
            # filters out a day's slots once the doctor's local clock
            # has passed schedule end_time (see the "past dates/times"
            # fix), so whether today itself still has open slots now
            # depends on what time of day the suite happens to run --
            # weekday alone no longer determines it. Every other date
            # in this loop is unaffected (always in the future).
            continue
        else:
            assert is_available is (weekday in (1, 2, 3, 4, 5))


def test_web_department_calendar_rejects_out_of_window_month(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Window",
        department_name="Window Dept",
        appointment_type_name="Window Type",
    )

    far_future = date.today().replace(day=1) + timedelta(days=400)
    response = client.get(
        "/api/web/calendar/department",
        params={
            "department_id": seeded["department_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "year": far_future.year,
            "month": far_future.month,
        },
    )
    assert response.status_code == 409


def test_web_availability_by_date_excludes_doctor_with_no_slots(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Weekday Web",
        department_name="Weekday Web Dept",
        appointment_type_name="Weekday Web Type",
        schedule_days=(1, 2, 3, 4, 5),
    )
    doctor_b_id = add_doctor_to_department(
        client,
        db_connection,
        department_id=seeded["department_id"],
        appointment_type_id=seeded["appointment_type_id"],
        doctor_name="Dr. Saturday Web",
        schedule_days=(6,),
    )

    today = date.today()
    days_ahead = (0 - today.weekday()) % 7 or 7  # next Monday
    monday = today + timedelta(days=days_ahead)

    response = client.get(
        "/api/web/availability/by-date",
        params={
            "department_id": seeded["department_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "selected_date": monday.isoformat(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    doctor_ids = [d["id"] for d in body["doctors"]]
    assert seeded["doctor_id"] in doctor_ids
    assert doctor_b_id not in doctor_ids
    for doctor in body["doctors"]:
        assert doctor["slots"]


def test_web_availability_by_date_rejects_out_of_window_date(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Date Window",
        department_name="Date Window Dept",
        appointment_type_name="Date Window Type",
    )

    far_future = date.today() + timedelta(days=400)
    response = client.get(
        "/api/web/availability/by-date",
        params={
            "department_id": seeded["department_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "selected_date": far_future.isoformat(),
        },
    )
    assert response.status_code == 409
