"""
Tests for GET /api/search (master spec audit gap #5's global search
half, section 14): cross-entity patient/appointment search from one box.
"""

from datetime import date, timedelta

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


def test_search_requires_authentication(client, db_connection):
    response = client.get("/api/search", params={"q": "anything"})
    assert response.status_code == 401


def test_search_requires_a_query(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/search", params={"q": ""}, headers=admin_headers)
    assert response.status_code == 422
