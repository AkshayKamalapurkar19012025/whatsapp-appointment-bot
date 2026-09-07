"""
Tests for app/api/appointment_types.py -- specifically covering the
router being reachable at all. It existed, fully implemented, since
before this project's review, but app/main.py never imported/mounted
it, so /api/appointment-types was a 404 for every caller until that was
fixed. These tests exist mainly to catch a regression of that specific
mistake (a router silently not wired into main.py), not because the
CRUD logic itself is complex.

POST is ADMIN-gated as of WEB P6 -- these tests authenticate as a fresh
admin via tests/helpers.py's create_admin_and_get_headers. GET stays
public (unchanged), matching the patient scheduling flow's own reliance on
public reference-data reads elsewhere.

PUT/DELETE (rename and soft-delete) were added later -- RBAC gating for
those two is covered separately in tests/test_admin_rbac.py's
admin-only-write-calls list, so the tests below are about behaviour,
not auth.
"""

from tests.helpers import create_admin_and_get_headers


def test_create_and_list_appointment_type(client, db_connection):
    headers = create_admin_and_get_headers(db_connection)
    created = client.post("/api/appointment-types", json={"name": "Consultation"}, headers=headers)
    assert created.status_code == 200
    body = created.json()
    assert body["name"] == "Consultation"
    assert body["active"] is True

    listed = client.get("/api/appointment-types")
    assert listed.status_code == 200
    names = [item["name"] for item in listed.json()]
    assert "Consultation" in names


def test_duplicate_appointment_type_name_is_rejected(client, db_connection):
    headers = create_admin_and_get_headers(db_connection)
    client.post("/api/appointment-types", json={"name": "Follow-up"}, headers=headers)
    duplicate = client.post("/api/appointment-types", json={"name": "Follow-up"}, headers=headers)

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Appointment type already exists"


def test_empty_name_is_rejected(client, db_connection):
    headers = create_admin_and_get_headers(db_connection)
    response = client.post("/api/appointment-types", json={"name": "   "}, headers=headers)
    assert response.status_code == 422


def test_create_appointment_type_requires_staff_auth(client):
    response = client.post("/api/appointment-types", json={"name": "Unauthenticated"})
    assert response.status_code == 401


def test_rename_appointment_type(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    appointment_type = client.post(
        "/api/appointment-types", json={"name": "Original Type"}, headers=admin_headers
    ).json()

    response = client.put(
        f"/api/appointment-types/{appointment_type['id']}",
        json={"name": "Renamed Type"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == appointment_type["id"]
    assert body["name"] == "Renamed Type"

    listed = client.get("/api/appointment-types").json()
    assert any(t["id"] == appointment_type["id"] and t["name"] == "Renamed Type" for t in listed)


def test_rename_appointment_type_rejects_duplicate_name(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    client.post("/api/appointment-types", json={"name": "Taken Type Name"}, headers=admin_headers)
    other = client.post(
        "/api/appointment-types", json={"name": "Other Type"}, headers=admin_headers
    ).json()

    response = client.put(
        f"/api/appointment-types/{other['id']}",
        json={"name": "Taken Type Name"},
        headers=admin_headers,
    )

    assert response.status_code == 409


def test_rename_missing_appointment_type_returns_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    response = client.put(
        "/api/appointment-types/999999999",
        json={"name": "Doesn't matter"},
        headers=admin_headers,
    )

    assert response.status_code == 404


def test_delete_appointment_type_removes_it_from_listing(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    appointment_type = client.post(
        "/api/appointment-types", json={"name": "To Be Removed"}, headers=admin_headers
    ).json()

    response = client.delete(
        f"/api/appointment-types/{appointment_type['id']}", headers=admin_headers
    )
    assert response.status_code == 200

    listed = client.get("/api/appointment-types").json()
    assert all(t["id"] != appointment_type["id"] for t in listed)


def test_delete_appointment_type_is_idempotent_safe_404_on_repeat(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    appointment_type = client.post(
        "/api/appointment-types", json={"name": "Delete Twice"}, headers=admin_headers
    ).json()

    first = client.delete(f"/api/appointment-types/{appointment_type['id']}", headers=admin_headers)
    second = client.delete(f"/api/appointment-types/{appointment_type['id']}", headers=admin_headers)

    assert first.status_code == 200
    assert second.status_code == 404
