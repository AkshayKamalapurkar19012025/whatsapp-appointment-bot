"""
Tests for the printable OPD Appointment Slip (Printing phase section 3):
GET /api/appointments/{id}/slip.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _booked_context(client, db_connection, doctor_name: str) -> dict:
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

    return {"admin_headers": admin_headers, "patient": patient, "appointment": created, "seeded": seeded}


def test_get_appointment_slip(client, db_connection):
    ctx = _booked_context(client, db_connection, "Dr. Slip Basic")
    appointment_id = ctx["appointment"]["id"]

    response = client.get(f"/api/appointments/{appointment_id}/slip", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()

    assert body["appointment_id"] == appointment_id
    assert body["appointment_number"].startswith("APT-")
    assert body["hospital_name"]
    assert body["patient_name"] == ctx["patient"]["name"]
    assert body["patient_uhid"] == ctx["patient"]["uhid"]
    assert body["patient_contact"] == ctx["patient"]["whatsapp_number"]
    assert body["doctor_name"] == "Dr. Slip Basic"
    assert body["department_name"] == "Dr. Slip Basic Dept"
    assert body["visit_type"] == "Dr. Slip Basic Type"
    assert body["status"] == "PENDING"
    assert body["start_at"] and body["end_at"]
    assert body["booked_at"]


def test_appointment_slip_callable_before_checkin(client, db_connection):
    """Unlike the receipt (which needs a payment) or invoice, the slip
    is a pre-visit document -- must work for a freshly booked, not yet
    checked-in appointment."""
    ctx = _booked_context(client, db_connection, "Dr. Slip Prebooking")
    response = client.get(f"/api/appointments/{ctx['appointment']['id']}/slip", headers=ctx["admin_headers"])
    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"


def test_appointment_slip_404_for_nonexistent_appointment(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/appointments/999999/slip", headers=admin_headers)
    assert response.status_code == 404


def test_appointment_slip_requires_authentication(client):
    response = client.get("/api/appointments/1/slip")
    assert response.status_code == 401
