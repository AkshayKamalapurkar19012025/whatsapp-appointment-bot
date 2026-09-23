"""
Tests for GET /api/search (master spec audit gap #5's global search
half, section 14): cross-entity patient/appointment search from one box.
"""

from datetime import date, timedelta
import secrets

from app.db.connection import get_connection
from app.services.search_service import global_search_service
from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def test_search_finds_patient_by_name_number_and_uhid(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = client.post(
        "/api/patients",
        json={"name": "Global Search Patient", "whatsapp_number": "+919500000001"},
        headers=admin_headers,
    ).json()

    for q in ("Global Search", "9500000001", patient["uhid"]):
        response = client.get("/api/search", params={"q": q}, headers=admin_headers)
        assert response.status_code == 200
        body = response.json()
        assert any(p["id"] == patient["id"] for p in body["patients"]), f"query {q!r} -> {body}"


def test_search_finds_appointment_by_patient_or_doctor_name(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Global Search",
        department_name="Dr. Global Search Dept",
        appointment_type_name="Dr. Global Search Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Appointment Search Patient", "whatsapp_number": "+919500000002"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()

    by_patient = client.get(
        "/api/search", params={"q": "Appointment Search Patient"}, headers=admin_headers
    ).json()
    assert any(a["id"] == created["id"] for a in by_patient["appointments"])

    by_doctor = client.get("/api/search", params={"q": "Dr. Global Search"}, headers=admin_headers).json()
    assert any(a["id"] == created["id"] for a in by_doctor["appointments"])


def test_search_appointment_branch_is_isolated_by_hospital(client, db_connection):
    """Regression test tied directly to the appointments.hospital_id
    insert bug fixed in app/services/appointment_services.py (see
    tests/test_admin_appointments.py::
    test_appointment_and_reschedule_carry_the_booking_doctors_real_hospital_id
    for the booking-side proof): before that fix, every appointment
    silently carried hospital_id=1 regardless of its doctor's real
    hospital, so this search_service.py query -- WHERE a.hospital_id =
    %s -- would have returned this hospital-2 appointment for a
    hospital-1 caller (and missed it for hospital 2) whenever the two
    ids differed. Calls global_search_service directly (no second
    hospital's own staff/auth flow exists to call GET /api/search
    through -- see the booking-side test's docstring for why) so this
    checks the actual consumer of the column, not just that the column
    itself now holds the right value."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Search Hospital Isolation",
        department_name="Dr. Search Hospital Isolation Dept",
        appointment_type_name="Dr. Search Hospital Isolation Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Search Hospital Isolation Patient", "whatsapp_number": "+919500000099"},
        headers=admin_headers,
    ).json()

    # hospitals is never truncated between tests (reference data -- see
    # conftest.py's APP_TABLES), so a fixed code would collide with a
    # leftover row from a previous run against the persistent test
    # database. Randomized per run, same as tests/helpers.py's
    # create_staff_and_get_headers already does for usernames.
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO hospitals (code, name) VALUES (%s, %s) RETURNING id",
            (f"TEST-{secrets.token_hex(4)}", "Search Isolation Hospital"),
        )
        (second_hospital_id,) = cur.fetchone()
        cur.execute(
            "UPDATE doctors SET hospital_id = %s WHERE id = %s",
            (second_hospital_id, seeded["doctor_id"]),
        )
    db_connection.commit()

    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()

    with get_connection() as conn:
        with conn.cursor() as cur:
            found_in_hospital_2 = global_search_service(cur, "Search Hospital Isolation", hospital_id=second_hospital_id)
            found_in_hospital_1 = global_search_service(cur, "Search Hospital Isolation", hospital_id=1)

    assert any(a["id"] == created["id"] for a in found_in_hospital_2["appointments"])
    assert not any(a["id"] == created["id"] for a in found_in_hospital_1["appointments"])


def test_search_requires_authentication(client, db_connection):
    response = client.get("/api/search", params={"q": "anything"})
    assert response.status_code == 401


def test_search_requires_a_query(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/search", params={"q": ""}, headers=admin_headers)
    assert response.status_code == 422
