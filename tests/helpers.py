"""Shared test data helpers -- not test files themselves."""

from fastapi.testclient import TestClient
import psycopg

from app.services.staff_management import create_staff_account


def seed_basic_doctor(
    client: TestClient,
    db_connection: psycopg.Connection,
    *,
    doctor_name: str = "Dr. Test",
    department_name: str = "Test Department",
    appointment_type_name: str = "Consultation",
    timezone: str = "Asia/Kolkata",
    duration_minutes: int = 30,
    schedule_days=(1, 2, 3, 4, 5),
    start_time: str = "09:00",
    end_time: str = "17:00",
) -> dict:
    """
    Create a department, doctor, appointment type, assignment, and
    weekly schedule -- the minimum reference data any booking flow test
    needs. Returns the ids involved.

    department_name/appointment_type_name are parameterized (not just
    doctor_name) because both `departments.name` and
    `appointment_types.name` are UNIQUE -- a test that calls this more
    than once in the same database state (e.g. a concurrency test
    looping over several attempts) needs distinct names each time, not
    just a distinct doctor.
    """
    department = client.post("/api/departments", json={"name": department_name}).json()

    doctor = client.post("/api/doctors", json={"name": doctor_name}).json()

    if timezone != "Asia/Kolkata":
        with db_connection.cursor() as cur:
            cur.execute(
                "UPDATE doctors SET timezone = %s WHERE id = %s",
                (timezone, doctor["id"]),
            )
        db_connection.commit()

    client.post(f"/api/doctors/{doctor['id']}/departments/{department['id']}")

    appointment_type_id = client.post(
        "/api/appointment-types", json={"name": appointment_type_name}
    ).json()["id"]

    client.post(
        f"/api/doctors/{doctor['id']}/appointment-types/{appointment_type_id}",
        json={"duration_minutes": duration_minutes},
    )

    for day in schedule_days:
        client.post(
            f"/api/doctors/{doctor['id']}/schedule",
            json={
                "day_of_week": day,
                "start_time": start_time,
                "end_time": end_time,
            },
        )

    return {
        "department_id": department["id"],
        "doctor_id": doctor["id"],
        "appointment_type_id": appointment_type_id,
    }


def create_staff_for_test(
    db_connection: psycopg.Connection,
    *,
    username: str,
    password: str,
    role: str = "STAFF",
) -> dict:
    """Seed a staff/admin account directly (there's no self-service staff
    signup -- see app/services/staff_management.py's module docstring),
    using the exact same service function the real ADMIN-only creation
    endpoint calls, so a test account is created identically to a real
    one."""
    with db_connection.cursor() as cur:
        account = create_staff_account(cur, username, password, role)
    db_connection.commit()
    return account


def register_patient(client: TestClient, whatsapp_number: str, name: str) -> dict:
    """Drive the WhatsApp registration flow (Hi -> name) and return the
    resulting patient dict."""
    client.post("/api/booking", json={"whatsapp_number": whatsapp_number, "message": "Hi"})
    response = client.post(
        "/api/booking", json={"whatsapp_number": whatsapp_number, "message": name}
    )
    return response.json()["patient"]


def book_first_available_slot(
    client: TestClient,
    whatsapp_number: str,
    *,
    date_option: str = "1",
    slot_option: str = "1",
) -> dict:
    """Drive the WhatsApp flow from MAIN_MENU through a completed booking
    for department 1 / doctor 1 / appointment type 1, picking the given
    date and slot option numbers. Returns the final BOOKED response
    body."""
    msg = lambda m: client.post(
        "/api/booking", json={"whatsapp_number": whatsapp_number, "message": m}
    ).json()

    msg("1")  # Book Appointment
    msg("1")  # department 1
    msg("1")  # doctor 1
    msg("1")  # appointment type 1
    msg(date_option)
    msg(slot_option)
    return msg("1")  # confirm
