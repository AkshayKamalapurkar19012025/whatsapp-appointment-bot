"""
Tests for the four staff-driven appointment lifecycle transitions added
in migrations/0011_appointment_lifecycle_statuses.sql:
POST /api/appointments/{id}/confirm, /reject, /visit, /complete.

Every appointment now starts PENDING (create_appointment_service, used
by WhatsApp, patient web booking, and this same admin create endpoint)
and moves forward one step at a time:
    PENDING -> CONFIRMED -> VISITED -> COMPLETED
       \\-> REJECTED

Each endpoint only accepts its one specific starting status -- confirm
only from PENDING, reject only from PENDING, visit only from CONFIRMED,
complete only from VISITED -- rejecting everything else with 409.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _seed_and_book(client, db_connection, doctor_name: str) -> dict:
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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9199{abs(hash(doctor_name)) % 10**8:08d}"},
        headers=admin_headers,
    ).json()
    booking_date = _next_weekday(date.today() + timedelta(days=10))
    # Doctor-local wall-clock string, deliberately kept alongside the
    # response below -- create_appointment_service's returned start_at
    # is read back from Postgres and comes back UTC-labeled (the same
    # "UTC-normalized on read-back" characteristic documented throughout
    # this codebase, e.g. appointment_services.py's reschedule test),
    # NOT the doctor's local wall-clock time. Reusing that UTC-labeled
    # value to target "the same slot" in a later request would shift
    # which doctor_schedule row it's checked against and spuriously fail
    # -- callers that need to re-target this exact slot must reuse this
    # original request string, not the response.
    start_at_local = f"{booking_date.isoformat()}T09:00:00+05:30"
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": start_at_local,
        },
        headers=admin_headers,
    ).json()
    assert created["status"] == "PENDING"

    return {
        "admin_headers": admin_headers,
        "seeded": seeded,
        "patient": patient,
        "appointment": created,
        "start_at_local": start_at_local,
    }


# ---------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------


def test_confirm_requires_staff_auth(client, db_connection):
    ctx = _seed_and_book(client, db_connection, "Dr. Confirm Auth")
    response = client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm")
    assert response.status_code == 401


def test_confirm_pending_appointment_succeeds(client, db_connection):
    ctx = _seed_and_book(client, db_connection, "Dr. Confirm Normal")
    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CONFIRMED"

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (ctx["appointment"]["id"],))
        assert cur.fetchone()[0] == "CONFIRMED"


def test_confirm_nonexistent_appointment_is_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post("/api/appointments/999999999/confirm", headers=admin_headers)
    assert response.status_code == 404


def test_confirm_already_confirmed_appointment_is_409(client, db_connection):
    ctx = _seed_and_book(client, db_connection, "Dr. Confirm Twice")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------


def test_reject_pending_appointment_succeeds_and_releases_slot(client, db_connection):
    ctx = _seed_and_book(client, db_connection, "Dr. Reject Normal")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/reject", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"

    # A Rejected appointment releases its slot (migrations/0011's
    # RELEASED_STATUSES) -- a brand new booking for the exact same
    # doctor/time must now succeed rather than 409 on overlap.
    other_patient = client.post(
        "/api/patients",
        json={"name": "Reject Slot Reuser", "whatsapp_number": "+919912340001"},
        headers=ctx["admin_headers"],
    ).json()
    retry = client.post(
        "/api/appointments",
        json={
            "doctor_id": ctx["seeded"]["doctor_id"],
            "patient_id": other_patient["id"],
            "appointment_type_id": ctx["seeded"]["appointment_type_id"],
            "start_at": ctx["start_at_local"],
        },
        headers=ctx["admin_headers"],
    )
    assert retry.status_code == 200


def test_reject_confirmed_appointment_is_409(client, db_connection):
    ctx = _seed_and_book(client, db_connection, "Dr. Reject Confirmed")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/reject", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------
# Visit
# ---------------------------------------------------------------------


def test_visit_confirmed_appointment_succeeds(client, db_connection):
    ctx = _seed_and_book(client, db_connection, "Dr. Visit Normal")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "VISITED"


def test_visit_pending_appointment_is_409(client, db_connection):
    ctx = _seed_and_book(client, db_connection, "Dr. Visit Pending")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------


def test_complete_visited_appointment_succeeds(client, db_connection):
    ctx = _seed_and_book(client, db_connection, "Dr. Complete Normal")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])
    client.post(f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/complete", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"


def test_complete_confirmed_but_not_visited_appointment_is_409(client, db_connection):
    ctx = _seed_and_book(client, db_connection, "Dr. Complete Skip Visit")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/complete", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


def test_lifecycle_endpoints_usable_by_plain_staff_not_just_admin(client, db_connection):
    # Same RBAC as cancel/reschedule on this router -- any authenticated
    # STAFF session, not require_role("ADMIN").
    ctx = _seed_and_book(client, db_connection, "Dr. Lifecycle Staff")
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    response = client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=staff_headers)
    assert response.status_code == 200
