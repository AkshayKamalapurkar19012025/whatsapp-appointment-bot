"""
Tests for the Appointment Types OPD admin redesign's three new,
additive endpoints (app/api/appointment_types.py):

- GET /appointment-types/admin -- all types (active + inactive) with a
  real per-type doctor_count.
- GET /appointment-types/{id} -- the View drawer's detail, including
  the doctor/duration/fee breakdown from doctor_appointment_types.
- PATCH /appointment-types/{id}/active -- deactivate/reactivate,
  mirroring PATCH /doctors/{id}/active.

None of these touch the existing public GET "" (still active-only,
unauthenticated, depended on by AppointmentsPanel/DoctorWorkspace) or
the existing POST/PUT/DELETE endpoints -- those stay covered by
test_appointment_types.py unchanged.
"""

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def test_admin_listing_requires_staff_auth(client):
    response = client.get("/api/appointment-types/admin")
    assert response.status_code == 401


def test_admin_listing_is_not_confused_with_an_id(client, db_connection):
    """The literal "/admin" path segment must resolve to the admin
    listing route, never be parsed as {appointment_type_id}."""
    headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/appointment-types/admin", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_admin_listing_includes_inactive_types_with_doctor_count(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Count", appointment_type_name="Counted Type")
    admin_headers = create_admin_and_get_headers(db_connection)

    listed = client.get("/api/appointment-types/admin", headers=admin_headers).json()
    row = next(t for t in listed if t["id"] == seeded["appointment_type_id"])
    assert row["active"] is True
    assert row["doctor_count"] == 1

    client.delete(f"/api/appointment-types/{seeded['appointment_type_id']}", headers=admin_headers)

    listed_after = client.get("/api/appointment-types/admin", headers=admin_headers).json()
    row_after = next(t for t in listed_after if t["id"] == seeded["appointment_type_id"])
    assert row_after["active"] is False
    # Deactivating the appointment type itself doesn't touch the
    # doctor_appointment_types assignment row -- the count reflects
    # doctors still assigned, independent of the type's own active flag.
    assert row_after["doctor_count"] == 1

    # And it's gone from the existing public, active-only listing --
    # unchanged behaviour, not a regression.
    public_listed = client.get("/api/appointment-types").json()
    assert all(t["id"] != seeded["appointment_type_id"] for t in public_listed)


def test_admin_listing_doctor_count_excludes_inactive_doctor(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Vanishing", appointment_type_name="Vanishing Doctor Type")
    admin_headers = create_admin_and_get_headers(db_connection)

    client.patch(f"/api/doctors/{seeded['doctor_id']}/active", json={"active": False}, headers=admin_headers)

    listed = client.get("/api/appointment-types/admin", headers=admin_headers).json()
    row = next(t for t in listed if t["id"] == seeded["appointment_type_id"])
    assert row["doctor_count"] == 0


def test_detail_returns_doctor_duration_and_fee(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Detail",
        appointment_type_name="Detail Type",
        duration_minutes=25,
    )

    response = client.get(f"/api/appointment-types/{seeded['appointment_type_id']}", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Detail Type"
    assert body["active"] is True
    assert body["doctor_count"] == 1
    assert len(body["doctors"]) == 1
    doctor_row = body["doctors"][0]
    assert doctor_row["doctor_name"] == "Dr. Detail"
    assert doctor_row["duration_minutes"] == 25
    assert doctor_row["consultation_fee"] == 0  # seed_basic_doctor doesn't set one -- 0 is honest, not fabricated


def test_detail_excludes_inactive_doctor_assignment(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Removed", appointment_type_name="Removed Assignment Type")

    client.delete(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{seeded['appointment_type_id']}",
        headers=admin_headers,
    )

    body = client.get(f"/api/appointment-types/{seeded['appointment_type_id']}", headers=admin_headers).json()
    assert body["doctor_count"] == 0
    assert body["doctors"] == []


def test_detail_missing_type_returns_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/appointment-types/999999999", headers=admin_headers)
    assert response.status_code == 404


def test_deactivate_and_reactivate_appointment_type(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    created = client.post("/api/appointment-types", json={"name": "Togglable Type"}, headers=admin_headers).json()

    deactivated = client.patch(
        f"/api/appointment-types/{created['id']}/active", json={"active": False}, headers=admin_headers
    )
    assert deactivated.status_code == 200
    assert deactivated.json() == {"id": created["id"], "active": False}

    # Gone from the public active-only listing...
    public_listed = client.get("/api/appointment-types").json()
    assert all(t["id"] != created["id"] for t in public_listed)

    # ...but still visible (and reactivatable) via the admin listing/detail.
    admin_listed = client.get("/api/appointment-types/admin", headers=admin_headers).json()
    assert any(t["id"] == created["id"] and t["active"] is False for t in admin_listed)

    reactivated = client.patch(
        f"/api/appointment-types/{created['id']}/active", json={"active": True}, headers=admin_headers
    )
    assert reactivated.status_code == 200
    assert reactivated.json() == {"id": created["id"], "active": True}

    public_listed_after = client.get("/api/appointment-types").json()
    assert any(t["id"] == created["id"] for t in public_listed_after)


def test_deactivate_missing_appointment_type_returns_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.patch(
        "/api/appointment-types/999999999/active", json={"active": False}, headers=admin_headers
    )
    assert response.status_code == 404


def test_toggle_active_requires_admin_not_just_staff(client, db_connection):
    created = client.post(
        "/api/appointment-types",
        json={"name": "Admin Only Toggle"},
        headers=create_admin_and_get_headers(db_connection),
    ).json()

    staff_headers = create_staff_and_get_headers(db_connection)
    response = client.patch(
        f"/api/appointment-types/{created['id']}/active", json={"active": False}, headers=staff_headers
    )
    assert response.status_code == 403


def test_toggle_active_requires_staff_auth(client, db_connection):
    created = client.post(
        "/api/appointment-types",
        json={"name": "Unauthenticated Toggle"},
        headers=create_admin_and_get_headers(db_connection),
    ).json()

    response = client.patch(f"/api/appointment-types/{created['id']}/active", json={"active": False})
    assert response.status_code == 401
