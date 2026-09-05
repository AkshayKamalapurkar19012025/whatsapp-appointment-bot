"""
Tests for the Date-First aggregation functions added to
app/services/availability_engine.py: get_doctors_offering_appointment_type,
get_appointment_types_for_department, list_available_dates_for_department,
and list_doctors_with_slots_for_date.

Every one of these is a thin loop over the pre-existing, already-tested
get_available_slots()/list_available_dates_in_range() -- these tests
focus on the aggregation behavior itself (which doctors/dates get
included or excluded, and why), not on slot-computation correctness
already covered by test_availability_engine.py.
"""

from datetime import date, timedelta

from app.services.availability_engine import (
    get_appointment_types_for_department,
    get_doctors_offering_appointment_type,
    list_available_dates_for_department,
    list_doctors_with_slots_for_date,
)

from tests.helpers import add_doctor_to_department, create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(start: date, weekday: int) -> date:
    """weekday: Monday=0 ... Sunday=6 (Python's date.weekday())."""
    days_ahead = (weekday - start.weekday()) % 7
    days_ahead = days_ahead or 7
    return start + timedelta(days=days_ahead)


def test_get_doctors_offering_appointment_type_excludes_doctor_without_it(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Offers It",
        department_name="Dept Offers",
        appointment_type_name="Offered Type",
    )

    # A second doctor in the same department, but never assigned the
    # seeded appointment type -- must not appear as a candidate for it.
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor_b = client.post(
        "/api/doctors", json={"name": "Dr. Does Not Offer It"}, headers=admin_headers
    ).json()
    client.post(
        f"/api/doctors/{doctor_b['id']}/departments/{seeded['department_id']}",
        headers=admin_headers,
    )

    with db_connection.cursor() as cur:
        doctors = get_doctors_offering_appointment_type(
            cur, seeded["department_id"], seeded["appointment_type_id"]
        )

    assert [d["id"] for d in doctors] == [seeded["doctor_id"]]


def test_get_doctors_offering_appointment_type_multiple_doctors(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. A",
        department_name="Multi Doctor Dept",
        appointment_type_name="Multi Doctor Type",
    )
    doctor_b_id = add_doctor_to_department(
        client,
        db_connection,
        department_id=seeded["department_id"],
        appointment_type_id=seeded["appointment_type_id"],
        doctor_name="Dr. B",
    )

    with db_connection.cursor() as cur:
        doctors = get_doctors_offering_appointment_type(
            cur, seeded["department_id"], seeded["appointment_type_id"]
        )

    assert {d["id"] for d in doctors} == {seeded["doctor_id"], doctor_b_id}
    # Ordered by name -- "Dr. A" before "Dr. B".
    assert [d["name"] for d in doctors] == ["Dr. A", "Dr. B"]


def test_get_appointment_types_for_department_unions_across_doctors(client, db_connection):
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Type A",
        department_name="Union Dept",
        appointment_type_name="Type A",
    )

    admin_headers = create_admin_and_get_headers(db_connection)
    doctor_b = client.post(
        "/api/doctors", json={"name": "Dr. Type B"}, headers=admin_headers
    ).json()
    client.post(
        f"/api/doctors/{doctor_b['id']}/departments/{seeded['department_id']}",
        headers=admin_headers,
    )
    type_b_id = client.post(
        "/api/appointment-types", json={"name": "Type B"}, headers=admin_headers
    ).json()["id"]
    client.post(
        f"/api/doctors/{doctor_b['id']}/appointment-types/{type_b_id}",
        json={"duration_minutes": 20},
        headers=admin_headers,
    )

    with db_connection.cursor() as cur:
        types = get_appointment_types_for_department(cur, seeded["department_id"])

    assert {t["name"] for t in types} == {"Type A", "Type B"}


def test_list_doctors_with_slots_for_date_excludes_doctor_with_none_that_day(client, db_connection):
    """Doctor A works Mon-Fri, Doctor B works Saturday only -- on a
    weekday, only Doctor A should be returned, and never with an empty
    slots list (per the explicit 'never show a doctor with zero valid
    slots' requirement)."""
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Weekday",
        department_name="Weekday Dept",
        appointment_type_name="Weekday Type",
        schedule_days=(1, 2, 3, 4, 5),
    )
    doctor_b_id = add_doctor_to_department(
        client,
        db_connection,
        department_id=seeded["department_id"],
        appointment_type_id=seeded["appointment_type_id"],
        doctor_name="Dr. Saturday",
        schedule_days=(6,),
    )

    a_weekday = _next_weekday(date.today(), weekday=0)  # a Monday

    with db_connection.cursor() as cur:
        doctors_with_slots = list_doctors_with_slots_for_date(
            cur, seeded["department_id"], seeded["appointment_type_id"], a_weekday
        )

    doctor_ids = [d["id"] for d in doctors_with_slots]
    assert seeded["doctor_id"] in doctor_ids
    assert doctor_b_id not in doctor_ids
    for doctor in doctors_with_slots:
        assert doctor["slots"], "must never return a doctor with an empty slots list"


def test_list_doctors_with_slots_for_date_reflects_each_doctors_own_duration(client, db_connection):
    """Two doctors, same 09:00-17:00 (8 hour) schedule, same appointment
    type -- but 30-minute vs 60-minute duration for THAT doctor
    (doctor_appointment_types.duration_minutes is per doctor+type). Each
    doctor's slot count must reflect their own duration, not a shared
    one."""
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Thirty",
        department_name="Duration Dept",
        appointment_type_name="Duration Type",
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

    a_weekday = _next_weekday(date.today(), weekday=0)

    with db_connection.cursor() as cur:
        doctors_with_slots = list_doctors_with_slots_for_date(
            cur, seeded["department_id"], seeded["appointment_type_id"], a_weekday
        )

    by_id = {d["id"]: d for d in doctors_with_slots}
    # 8 hours / 30 min = 16 slots; 8 hours / 60 min = 8 slots.
    assert len(by_id[seeded["doctor_id"]]["slots"]) == 16
    assert len(by_id[doctor_b_id]["slots"]) == 8


def test_list_available_dates_for_department_is_true_if_any_doctor_available(client, db_connection):
    """Doctor A works Mon-Wed, Doctor B works Thu-Fri -- every weekday in
    range should be marked available (the union), and weekends
    unavailable (neither doctor works then)."""
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. MonWed",
        department_name="Union Dates Dept",
        appointment_type_name="Union Dates Type",
        schedule_days=(1, 2, 3),
    )
    add_doctor_to_department(
        client,
        db_connection,
        department_id=seeded["department_id"],
        appointment_type_id=seeded["appointment_type_id"],
        doctor_name="Dr. ThuFri",
        schedule_days=(4, 5),
    )

    start = date.today()
    end = start + timedelta(days=13)

    with db_connection.cursor() as cur:
        result = list_available_dates_for_department(
            cur, seeded["department_id"], seeded["appointment_type_id"], start, end
        )

    for iso_date, is_available in result.items():
        weekday = date.fromisoformat(iso_date).weekday() + 1
        expected = weekday in (1, 2, 3, 4, 5)
        assert is_available is expected, (
            f"{iso_date}: expected availability={expected}, got {is_available}"
        )
