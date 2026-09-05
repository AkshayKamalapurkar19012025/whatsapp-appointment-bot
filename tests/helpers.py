"""Shared test data helpers -- not test files themselves."""

import secrets

from fastapi.testclient import TestClient
import psycopg

from app.services.staff_auth import login as _staff_login
from app.services.staff_management import create_staff_account


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


def create_staff_and_get_headers(db_connection: psycopg.Connection, role: str = "STAFF") -> dict:
    """Create a fresh, uniquely-named staff/admin account and return
    {"Authorization": "Bearer <token>"} for it -- for tests that need to
    call one of WEB P6's RBAC-gated endpoints as setup (not as the thing
    under test). A fresh account per call, not a shared fixture, so
    multiple calls within the same test (e.g. seed_basic_doctor called
    twice) never collide on username uniqueness. Calls
    app.services.staff_auth.login directly rather than going through the
    HTTP /api/auth/staff/login endpoint -- this is test setup, not a
    test of login itself, so skipping the round trip keeps the many
    tests that call this (via seed_basic_doctor) fast."""
    username = f"seed-{role.lower()}-{secrets.token_hex(4)}"
    password = "seed-staff-password"  # noqa: S105 -- test-only, never a real credential
    create_staff_for_test(db_connection, username=username, password=password, role=role)

    with db_connection.cursor() as cur:
        result = _staff_login(cur, username, password)
    db_connection.commit()

    return {"Authorization": f"Bearer {result['session_token']}"}


def create_admin_and_get_headers(db_connection: psycopg.Connection) -> dict:
    """ADMIN-specific convenience wrapper around
    create_staff_and_get_headers -- the common case, used throughout the
    existing test suite for endpoints that were already ADMIN-gated
    before RBAC had a STAFF-vs-ADMIN distinction worth testing."""
    return create_staff_and_get_headers(db_connection, role="ADMIN")


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

    Authenticates as a freshly-created ADMIN internally (WEB P6 gated
    these write endpoints) -- every caller of this helper across the
    existing test suite gets that for free, rather than needing its own
    admin-login boilerplate.
    """
    admin_headers = create_admin_and_get_headers(db_connection)

    department = client.post(
        "/api/departments", json={"name": department_name}, headers=admin_headers
    ).json()

    doctor = client.post(
        "/api/doctors",
        json={"name": doctor_name, "specialization": "General Medicine"},
        headers=admin_headers,
    ).json()

    if timezone != "Asia/Kolkata":
        with db_connection.cursor() as cur:
            cur.execute(
                "UPDATE doctors SET timezone = %s WHERE id = %s",
                (timezone, doctor["id"]),
            )
        db_connection.commit()

    client.post(
        f"/api/doctors/{doctor['id']}/departments/{department['id']}", headers=admin_headers
    )

    appointment_type_id = client.post(
        "/api/appointment-types", json={"name": appointment_type_name}, headers=admin_headers
    ).json()["id"]

    client.post(
        f"/api/doctors/{doctor['id']}/appointment-types/{appointment_type_id}",
        json={"duration_minutes": duration_minutes},
        headers=admin_headers,
    )

    for day in schedule_days:
        client.post(
            f"/api/doctors/{doctor['id']}/schedule",
            json={
                "day_of_week": day,
                "start_time": start_time,
                "end_time": end_time,
            },
            headers=admin_headers,
        )

    return {
        "department_id": department["id"],
        "doctor_id": doctor["id"],
        "appointment_type_id": appointment_type_id,
    }


def add_doctor_to_department(
    client: TestClient,
    db_connection: psycopg.Connection,
    *,
    department_id: int,
    appointment_type_id: int,
    doctor_name: str,
    timezone: str = "Asia/Kolkata",
    duration_minutes: int = 30,
    schedule_days=(1, 2, 3, 4, 5),
    start_time: str = "09:00",
    end_time: str = "17:00",
) -> int:
    """
    Add a second (or third...) doctor to an already-seeded department,
    offering an already-seeded appointment type -- unlike
    seed_basic_doctor(), which always creates a brand new department +
    appointment type. For Date-First tests, which need multiple doctors
    sharing the same department/appointment type (the whole point of
    aggregation) rather than one doctor per test. Returns the new
    doctor's id.
    """
    admin_headers = create_admin_and_get_headers(db_connection)

    doctor = client.post(
        "/api/doctors",
        json={"name": doctor_name, "specialization": "General Medicine"},
        headers=admin_headers,
    ).json()

    if timezone != "Asia/Kolkata":
        with db_connection.cursor() as cur:
            cur.execute(
                "UPDATE doctors SET timezone = %s WHERE id = %s",
                (timezone, doctor["id"]),
            )
        db_connection.commit()

    client.post(
        f"/api/doctors/{doctor['id']}/departments/{department_id}", headers=admin_headers
    )

    client.post(
        f"/api/doctors/{doctor['id']}/appointment-types/{appointment_type_id}",
        json={"duration_minutes": duration_minutes},
        headers=admin_headers,
    )

    for day in schedule_days:
        client.post(
            f"/api/doctors/{doctor['id']}/schedule",
            json={
                "day_of_week": day,
                "start_time": start_time,
                "end_time": end_time,
            },
            headers=admin_headers,
        )

    return doctor["id"]


def register_patient(client: TestClient, whatsapp_number: str, name: str) -> dict:
    """Drive the WhatsApp registration flow (Hi -> name) and return the
    resulting patient dict."""
    client.post("/api/booking", json={"whatsapp_number": whatsapp_number, "message": "Hi"})
    response = client.post(
        "/api/booking", json={"whatsapp_number": whatsapp_number, "message": name}
    )
    return response.json()["patient"]


def register_and_login_web_patient(client: TestClient, whatsapp_number: str, name: str) -> str:
    """Drive the web OTP login flow (request -> dev-lookup -> verify with
    name, since this is always a brand-new number in test isolation) and
    return the resulting Bearer session token."""
    client.post("/api/auth/patient/otp/request", json={"whatsapp_number": whatsapp_number})
    lookup = client.get(
        "/api/auth/patient/otp/_dev_lookup",
        params={"whatsapp_number": whatsapp_number},
    )
    code = lookup.json()["otp_code"]

    verified = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": whatsapp_number, "otp": code, "name": name},
    )
    return verified.json()["session_token"]


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

    msg("1")  # Book Appointment -> SELECT_BOOKING_MODE
    msg("1")  # Choose a Doctor (Doctor-First)
    msg("1")  # department 1
    msg("1")  # doctor 1
    msg("1")  # appointment type 1
    msg(date_option)
    msg(slot_option)
    return msg("1")  # confirm
