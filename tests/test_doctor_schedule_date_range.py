"""
Tests for WEB P7 -- date-ranged doctor schedules (migrations/0006 adds
nullable start_date/end_date to doctor_schedule; NULL on either side
means unbounded, so an existing/omitted-range row keeps its pre-P7
"applies forever" behavior unchanged -- covered by the full existing
suite passing without modification).

Covers three touch points that all needed to learn about date ranges
consistently, not just the CRUD router itself:
  1. app/api/doctor_schedule.py -- create/update/overlap-rejection.
  2. app/services/availability_engine.py -- slot generation (what's
     shown as bookable).
  3. app/services/appointment_services.py's create_appointment_service
     -- the actual booking-creation schedule check (what's allowed to
     be booked), so a booking can't succeed for a date the calendar
     wouldn't have offered as available.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(target_weekday: int, from_date: date | None = None) -> date:
    """Next date (strictly after from_date, default today) whose
    isoweekday() == target_weekday (Monday=1 ... Sunday=7)."""
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() != target_weekday:
        d += timedelta(days=1)
    return d


def test_create_schedule_with_date_range_reflected_in_get(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Ranged Schedule")

    created = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 6,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": "2027-03-01",
            "end_date": "2027-03-31",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200
    body = created.json()
    assert body["start_date"] == "2027-03-01"
    assert body["end_date"] == "2027-03-31"

    listed = client.get(f"/api/doctors/{seeded['doctor_id']}/schedule")
    ranged = next(r for r in listed.json() if r["id"] == body["id"])
    assert ranged["start_date"] == "2027-03-01"
    assert ranged["end_date"] == "2027-03-31"


def test_schedule_with_no_date_range_still_returns_null(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Permanent Schedule")

    listed = client.get(f"/api/doctors/{seeded['doctor_id']}/schedule")
    for row in listed.json():
        assert row["start_date"] is None
        assert row["end_date"] is None


def test_slot_generation_respects_date_range(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    department = client.post(
        "/api/departments", json={"name": "P7 Ranged Dept"}, headers=admin_headers
    ).json()
    doctor = client.post(
        "/api/doctors",
        json={"name": "Dr. P7 Ranged", "specialization": "General Medicine"},
        headers=admin_headers,
    ).json()
    client.post(
        f"/api/doctors/{doctor['id']}/departments/{department['id']}", headers=admin_headers
    )
    appointment_type = client.post(
        "/api/appointment-types", json={"name": "P7 Ranged Type"}, headers=admin_headers
    ).json()
    client.post(
        f"/api/doctors/{doctor['id']}/appointment-types/{appointment_type['id']}",
        json={"duration_minutes": 30},
        headers=admin_headers,
    )

    # A Saturday-only schedule, active only in a specific 2-week window
    # well in the future.
    in_range_date = _next_weekday(6, date(2027, 6, 1))
    range_start = in_range_date - timedelta(days=7)
    range_end = in_range_date + timedelta(days=7)
    out_of_range_date = range_end + timedelta(days=7)
    while out_of_range_date.isoweekday() != 6:
        out_of_range_date += timedelta(days=1)

    client.post(
        f"/api/doctors/{doctor['id']}/schedule",
        json={
            "day_of_week": 6,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": range_start.isoformat(),
            "end_date": range_end.isoformat(),
        },
        headers=admin_headers,
    )

    in_range_slots = client.post(
        "/api/availability",
        json={
            "doctor_id": doctor["id"],
            "appointment_type_id": appointment_type["id"],
            "date": in_range_date.isoformat(),
        },
    ).json()
    assert len(in_range_slots["slots"]) > 0

    out_of_range_slots = client.post(
        "/api/availability",
        json={
            "doctor_id": doctor["id"],
            "appointment_type_id": appointment_type["id"],
            "date": out_of_range_date.isoformat(),
        },
    ).json()
    assert out_of_range_slots["slots"] == []


def test_booking_creation_respects_date_range(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded_department = client.post(
        "/api/departments", json={"name": "P7 Booking Dept"}, headers=admin_headers
    ).json()
    doctor = client.post(
        "/api/doctors",
        json={"name": "Dr. P7 Booking", "specialization": "General Medicine"},
        headers=admin_headers,
    ).json()
    client.post(
        f"/api/doctors/{doctor['id']}/departments/{seeded_department['id']}",
        headers=admin_headers,
    )
    appointment_type = client.post(
        "/api/appointment-types", json={"name": "P7 Booking Type"}, headers=admin_headers
    ).json()
    client.post(
        f"/api/doctors/{doctor['id']}/appointment-types/{appointment_type['id']}",
        json={"duration_minutes": 30},
        headers=admin_headers,
    )
    patient = client.post(
        "/api/patients",
        json={"name": "P7 Booking Patient", "whatsapp_number": "+919400000001"},
        headers=admin_headers,
    ).json()

    in_range_date = _next_weekday(6, date(2027, 7, 1))
    range_start = in_range_date - timedelta(days=1)
    range_end = in_range_date + timedelta(days=1)
    out_of_range_date = in_range_date + timedelta(days=7)  # next Saturday, past end_date

    client.post(
        f"/api/doctors/{doctor['id']}/schedule",
        json={
            "day_of_week": 6,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": range_start.isoformat(),
            "end_date": range_end.isoformat(),
        },
        headers=admin_headers,
    )

    in_range_booking = client.post(
        "/api/appointments",
        json={
            "doctor_id": doctor["id"],
            "patient_id": patient["id"],
            "appointment_type_id": appointment_type["id"],
            "start_at": f"{in_range_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    )
    assert in_range_booking.status_code == 200

    out_of_range_booking = client.post(
        "/api/appointments",
        json={
            "doctor_id": doctor["id"],
            "patient_id": patient["id"],
            "appointment_type_id": appointment_type["id"],
            "start_at": f"{out_of_range_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    )
    assert out_of_range_booking.status_code == 409
    assert (
        out_of_range_booking.json()["detail"]
        == "Appointment is outside doctor's working schedule"
    )


def test_non_overlapping_date_ranges_can_coexist_same_day_and_time(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Coexist", schedule_days=()
    )

    first = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 3,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": "2027-04-01",
            "end_date": "2027-04-30",
        },
        headers=admin_headers,
    )
    assert first.status_code == 200

    # A later window, same day-of-week and same hours -- allowed because
    # the date ranges don't overlap.
    second = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 3,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": "2027-05-01",
            "end_date": "2027-05-31",
        },
        headers=admin_headers,
    )
    assert second.status_code == 200


def test_overlapping_date_ranges_same_day_and_time_are_rejected(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Overlap Ranged", schedule_days=()
    )

    first = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 4,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": "2027-06-01",
            "end_date": "2027-06-30",
        },
        headers=admin_headers,
    )
    assert first.status_code == 200

    # Overlaps June 15-30 with the row above, same day/time.
    second = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 4,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": "2027-06-15",
            "end_date": "2027-07-15",
        },
        headers=admin_headers,
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "Schedule overlaps with an existing schedule"


def test_date_ranges_touching_on_the_same_day_are_rejected(client, db_connection):
    # Both start_date/end_date are inclusive, so a row ending on day N
    # and one starting on day N both claim day N -- a genuine conflict,
    # not a "back-to-back" case (unlike time ranges, which use a
    # half-open [start, end) convention elsewhere in this codebase).
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Touching Boundary", schedule_days=()
    )

    first = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 7,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": "2027-10-01",
            "end_date": "2027-10-15",
        },
        headers=admin_headers,
    )
    assert first.status_code == 200

    touching = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 7,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": "2027-10-15",
            "end_date": "2027-10-31",
        },
        headers=admin_headers,
    )
    assert touching.status_code == 409


def test_date_ranges_on_adjacent_days_do_not_conflict(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Adjacent Boundary", schedule_days=()
    )

    first = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 1,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": "2027-11-01",
            "end_date": "2027-11-15",
        },
        headers=admin_headers,
    )
    assert first.status_code == 200

    next_day = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 1,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": "2027-11-16",
            "end_date": "2027-11-30",
        },
        headers=admin_headers,
    )
    assert next_day.status_code == 200


def test_a_permanent_schedule_conflicts_with_any_dated_row_at_the_same_time(client, db_connection):
    # A NULL/NULL (permanent) row is unbounded on both sides, so it
    # conflicts with a dated row at the same day/time regardless of what
    # date range the dated row specifies -- this is the direct
    # consequence of NULL meaning -infinity/+infinity in the interval-
    # overlap check, not a special case.
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Permanent Conflict", schedule_days=(5,)
    )

    dated = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 5,
            "start_time": "09:00",
            "end_time": "17:00",
            "start_date": "2028-01-01",
            "end_date": "2028-01-31",
        },
        headers=admin_headers,
    )
    assert dated.status_code == 409


def test_end_date_before_start_date_is_rejected(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Bad Range")

    response = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 2,
            "start_time": "09:00",
            "end_time": "10:00",
            "start_date": "2027-08-10",
            "end_date": "2027-08-01",
        },
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_update_schedule_can_add_a_date_range(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Update Range", schedule_days=()
    )

    created = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={"day_of_week": 1, "start_time": "09:00", "end_time": "12:00"},
        headers=admin_headers,
    ).json()
    assert created["start_date"] is None

    updated = client.put(
        f"/api/doctors/{seeded['doctor_id']}/schedule/{created['id']}",
        json={
            "day_of_week": 1,
            "start_time": "09:00",
            "end_time": "12:00",
            "start_date": "2027-09-01",
            "end_date": "2027-09-30",
        },
        headers=admin_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["start_date"] == "2027-09-01"
    assert updated.json()["end_date"] == "2027-09-30"
