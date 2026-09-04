"""
Tests for department-scoped doctor schedules (migrations/0010 adds a
nullable department_id to doctor_schedule; NULL means "applies
regardless of department", which is why every pre-existing row and
every seed_basic_doctor()-created row -- none of which pass
department_id -- keeps its "applies everywhere" behavior unchanged,
covered by the full existing suite passing without modification).

Covers the same touch points test_doctor_schedule_date_range.py covers
for date ranges, since department scoping is the same shape of change:
  1. app/api/doctor_schedule.py -- create/update validates department_id
     against doctor_departments; overlap rejection stays department-
     agnostic (a doctor can't be in two places at once).
  2. app/services/availability_engine.py (via GET /api/availability and
     GET /api/web/calendar) -- department_id narrows which schedule
     rows are used to compute available slots/days.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers


def _next_weekday(target_weekday: int, from_date: date | None = None) -> date:
    """Next date (strictly after from_date, default today) whose
    isoweekday() == target_weekday (Monday=1 ... Sunday=7)."""
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() != target_weekday:
        d += timedelta(days=1)
    return d


def _seed_doctor_in_two_departments(client, db_connection, *, name_prefix: str) -> dict:
    """Create a doctor assigned to two departments, with an appointment
    type (30 min) and no schedule rows yet -- callers add their own
    department-scoped schedule rows. Returns ids involved."""
    admin_headers = create_admin_and_get_headers(db_connection)

    dept_a = client.post(
        "/api/departments", json={"name": f"{name_prefix} Dept A"}, headers=admin_headers
    ).json()
    dept_b = client.post(
        "/api/departments", json={"name": f"{name_prefix} Dept B"}, headers=admin_headers
    ).json()
    doctor = client.post(
        "/api/doctors", json={"name": f"Dr. {name_prefix}"}, headers=admin_headers
    ).json()
    client.post(f"/api/doctors/{doctor['id']}/departments/{dept_a['id']}", headers=admin_headers)
    client.post(f"/api/doctors/{doctor['id']}/departments/{dept_b['id']}", headers=admin_headers)

    appointment_type = client.post(
        "/api/appointment-types", json={"name": f"{name_prefix} Type"}, headers=admin_headers
    ).json()
    client.post(
        f"/api/doctors/{doctor['id']}/appointment-types/{appointment_type['id']}",
        json={"duration_minutes": 30},
        headers=admin_headers,
    )

    return {
        "admin_headers": admin_headers,
        "doctor_id": doctor["id"],
        "department_a_id": dept_a["id"],
        "department_b_id": dept_b["id"],
        "appointment_type_id": appointment_type["id"],
    }


def test_create_schedule_with_department_id_reflected_in_get(client, db_connection):
    seeded = _seed_doctor_in_two_departments(client, db_connection, name_prefix="P12 Reflect")
    admin_headers = seeded["admin_headers"]

    created = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 1,
            "start_time": "09:00",
            "end_time": "12:00",
            "department_id": seeded["department_a_id"],
        },
        headers=admin_headers,
    )
    assert created.status_code == 200
    assert created.json()["department_id"] == seeded["department_a_id"]

    listed = client.get(f"/api/doctors/{seeded['doctor_id']}/schedule")
    row = next(r for r in listed.json() if r["id"] == created.json()["id"])
    assert row["department_id"] == seeded["department_a_id"]


def test_schedule_with_no_department_defaults_to_null(client, db_connection):
    seeded = _seed_doctor_in_two_departments(client, db_connection, name_prefix="P12 Null")

    created = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={"day_of_week": 2, "start_time": "09:00", "end_time": "12:00"},
        headers=seeded["admin_headers"],
    )
    assert created.status_code == 200
    assert created.json()["department_id"] is None


def test_create_schedule_with_unassigned_department_is_rejected(client, db_connection):
    seeded = _seed_doctor_in_two_departments(client, db_connection, name_prefix="P12 Unassigned")
    admin_headers = seeded["admin_headers"]

    other_department = client.post(
        "/api/departments", json={"name": "P12 Not This Doctor's Dept"}, headers=admin_headers
    ).json()

    response = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 3,
            "start_time": "09:00",
            "end_time": "12:00",
            "department_id": other_department["id"],
        },
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_update_schedule_can_set_department_id(client, db_connection):
    seeded = _seed_doctor_in_two_departments(client, db_connection, name_prefix="P12 Update")
    admin_headers = seeded["admin_headers"]

    created = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={"day_of_week": 4, "start_time": "09:00", "end_time": "12:00"},
        headers=admin_headers,
    ).json()
    assert created["department_id"] is None

    updated = client.put(
        f"/api/doctors/{seeded['doctor_id']}/schedule/{created['id']}",
        json={
            "day_of_week": 4,
            "start_time": "09:00",
            "end_time": "12:00",
            "department_id": seeded["department_b_id"],
        },
        headers=admin_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["department_id"] == seeded["department_b_id"]


def test_overlap_check_ignores_department(client, db_connection):
    # A doctor can only be in one place at a time -- two rows for the
    # same doctor/day/time still conflict even when tagged to different
    # departments (migrations/0010's header).
    seeded = _seed_doctor_in_two_departments(client, db_connection, name_prefix="P12 Overlap")
    admin_headers = seeded["admin_headers"]

    first = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 5,
            "start_time": "09:00",
            "end_time": "12:00",
            "department_id": seeded["department_a_id"],
        },
        headers=admin_headers,
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={
            "day_of_week": 5,
            "start_time": "09:00",
            "end_time": "12:00",
            "department_id": seeded["department_b_id"],
        },
        headers=admin_headers,
    )
    assert second.status_code == 409


def test_availability_respects_department_scoping(client, db_connection):
    seeded = _seed_doctor_in_two_departments(client, db_connection, name_prefix="P12 Avail")
    admin_headers = seeded["admin_headers"]
    doctor_id = seeded["doctor_id"]
    appointment_type_id = seeded["appointment_type_id"]

    monday = _next_weekday(1, date(2028, 2, 1))

    client.post(
        f"/api/doctors/{doctor_id}/schedule",
        json={
            "day_of_week": 1,
            "start_time": "09:00",
            "end_time": "10:00",
            "department_id": seeded["department_a_id"],
        },
        headers=admin_headers,
    )
    client.post(
        f"/api/doctors/{doctor_id}/schedule",
        json={
            "day_of_week": 1,
            "start_time": "14:00",
            "end_time": "15:00",
            "department_id": seeded["department_b_id"],
        },
        headers=admin_headers,
    )

    def slot_starts(department_id=None):
        body = {
            "doctor_id": doctor_id,
            "appointment_type_id": appointment_type_id,
            "date": monday.isoformat(),
        }
        if department_id is not None:
            body["department_id"] = department_id
        response = client.post("/api/availability", json=body)
        return {slot["start_at"][11:16] for slot in response.json()["slots"]}

    assert slot_starts(seeded["department_a_id"]) == {"09:00", "09:30"}
    assert slot_starts(seeded["department_b_id"]) == {"14:00", "14:30"}
    # No department in scope (e.g. admin booking/reschedule) sees every
    # row regardless of department -- the pre-0010 behavior, unchanged.
    assert slot_starts(None) == {"09:00", "09:30", "14:00", "14:30"}


def test_web_calendar_respects_department_scoping(client, db_connection):
    seeded = _seed_doctor_in_two_departments(client, db_connection, name_prefix="P12 Calendar")
    admin_headers = seeded["admin_headers"]
    doctor_id = seeded["doctor_id"]
    appointment_type_id = seeded["appointment_type_id"]

    # Department A works Mondays, department B works Tuesdays -- so a
    # given day is available only through the department whose schedule
    # actually covers it. Both dates need to fall within GET /web/
    # calendar's rolling booking window (current month + 3), so they're
    # picked relative to today rather than fixed, unlike the date-range
    # tests elsewhere in this suite that deliberately use fixed
    # far-future dates for a window-unrestricted endpoint.
    monday = _next_weekday(1)
    tuesday = monday + timedelta(days=1)

    client.post(
        f"/api/doctors/{doctor_id}/schedule",
        json={
            "day_of_week": 1,
            "start_time": "09:00",
            "end_time": "10:00",
            "department_id": seeded["department_a_id"],
        },
        headers=admin_headers,
    )
    client.post(
        f"/api/doctors/{doctor_id}/schedule",
        json={
            "day_of_week": 2,
            "start_time": "09:00",
            "end_time": "10:00",
            "department_id": seeded["department_b_id"],
        },
        headers=admin_headers,
    )

    def dates_for(month_date: date, department_id: int) -> dict:
        response = client.get(
            "/api/web/calendar",
            params={
                "doctor_id": doctor_id,
                "appointment_type_id": appointment_type_id,
                "year": month_date.year,
                "month": month_date.month,
                "department_id": department_id,
            },
        )
        assert response.status_code == 200
        return response.json()["dates"]

    dept_a_view = dates_for(monday, seeded["department_a_id"])
    assert dept_a_view[monday.isoformat()] is True
    if tuesday.month == monday.month:
        assert dept_a_view[tuesday.isoformat()] is False

    dept_b_view = dates_for(tuesday, seeded["department_b_id"])
    assert dept_b_view[tuesday.isoformat()] is True
    if monday.month == tuesday.month:
        assert dept_b_view[monday.isoformat()] is False
