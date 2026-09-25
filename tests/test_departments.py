"""
CRUD correctness for PUT/DELETE /departments/{id} (rename and soft-delete).
RBAC gating for these two endpoints is covered separately in
tests/test_admin_rbac.py's admin-only-write-calls list -- this file is
about behaviour, not auth.
"""

from tests.helpers import create_admin_and_get_headers


def test_create_is_visible_immediately_despite_the_list_cache(client, db_connection):
    """Phase 12 hardening (master spec audit gap #7): GET /departments
    is cached in-process (app/utils/reference_cache.py), so this
    specifically protects the invalidation contract -- a write must
    never leave a stale list behind, even for the very next request.
    Every rename/delete test above already re-reads the list right
    after its own write and would fail the same way if invalidation
    broke, but this one names the property directly rather than
    leaving it as incidental coverage."""
    admin_headers = create_admin_and_get_headers(db_connection)

    before = client.get("/api/departments").json()
    created = client.post(
        "/api/departments", json={"name": "Cache Invalidation Dept"}, headers=admin_headers
    ).json()
    after = client.get("/api/departments").json()

    assert not any(d["id"] == created["id"] for d in before)
    assert any(d["id"] == created["id"] and d["name"] == "Cache Invalidation Dept" for d in after)


def test_rename_department(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    department = client.post(
        "/api/departments", json={"name": "Original Name"}, headers=admin_headers
    ).json()

    response = client.put(
        f"/api/departments/{department['id']}",
        json={"name": "Renamed Department"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == department["id"]
    assert body["name"] == "Renamed Department"

    listed = client.get("/api/departments").json()
    assert any(d["id"] == department["id"] and d["name"] == "Renamed Department" for d in listed)


def test_rename_department_rejects_duplicate_name(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    client.post("/api/departments", json={"name": "Taken Name"}, headers=admin_headers)
    other = client.post(
        "/api/departments", json={"name": "Other Department"}, headers=admin_headers
    ).json()

    response = client.put(
        f"/api/departments/{other['id']}",
        json={"name": "Taken Name"},
        headers=admin_headers,
    )

    assert response.status_code == 409


def test_rename_missing_department_returns_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    response = client.put(
        "/api/departments/999999999",
        json={"name": "Doesn't matter"},
        headers=admin_headers,
    )

    assert response.status_code == 404


def test_delete_department_removes_it_from_listing(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    department = client.post(
        "/api/departments", json={"name": "To Be Removed"}, headers=admin_headers
    ).json()

    response = client.delete(f"/api/departments/{department['id']}", headers=admin_headers)
    assert response.status_code == 200

    listed = client.get("/api/departments").json()
    assert all(d["id"] != department["id"] for d in listed)


def test_delete_department_is_idempotent_safe_404_on_repeat(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    department = client.post(
        "/api/departments", json={"name": "Delete Twice"}, headers=admin_headers
    ).json()

    first = client.delete(f"/api/departments/{department['id']}", headers=admin_headers)
    second = client.delete(f"/api/departments/{department['id']}", headers=admin_headers)

    assert first.status_code == 200
    assert second.status_code == 404


def test_deleted_department_doctors_endpoint_404s(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    department = client.post(
        "/api/departments", json={"name": "Removed With Doctors"}, headers=admin_headers
    ).json()

    client.delete(f"/api/departments/{department['id']}", headers=admin_headers)

    response = client.get(f"/api/departments/{department['id']}/doctors")
    assert response.status_code == 404


def test_deleted_department_name_stays_reserved(client, db_connection):
    # departments.name is a plain UNIQUE column (migrations/
    # 0001_baseline_schema.sql), same as doctors.name -- soft-deleting
    # (active = FALSE) doesn't free the name for reuse. Documenting the
    # existing behaviour here, not asserting it's ideal.
    admin_headers = create_admin_and_get_headers(db_connection)
    department = client.post(
        "/api/departments", json={"name": "Reserved Name"}, headers=admin_headers
    ).json()
    client.delete(f"/api/departments/{department['id']}", headers=admin_headers)

    recreate = client.post(
        "/api/departments", json={"name": "Reserved Name"}, headers=admin_headers
    )

    assert recreate.status_code == 409
