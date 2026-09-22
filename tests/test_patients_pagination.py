"""
Tests for GET /api/patients/admin (master spec section 62/80: never
load the whole patient registry into the browser) -- server-side
search + limit/offset pagination for the admin Patients page, added
alongside the merged-spec-audit's own gap list.
"""

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers


def _create_patient(client, headers, name, whatsapp_number):
    return client.post(
        "/api/patients",
        json={"name": name, "whatsapp_number": whatsapp_number},
        headers=headers,
    ).json()


def test_admin_listing_requires_authentication(client):
    response = client.get("/api/patients/admin")
    assert response.status_code == 401


def test_admin_listing_paginates(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    for i in range(5):
        _create_patient(client, admin_headers, f"Pagination Patient {i}", f"+9199{i:08d}")

    first_page = client.get("/api/patients/admin?limit=2&offset=0", headers=admin_headers)
    assert first_page.status_code == 200
    body = first_page.json()
    assert len(body["items"]) == 2
    assert body["total"] >= 5
    assert body["limit"] == 2
    assert body["offset"] == 0

    second_page = client.get("/api/patients/admin?limit=2&offset=2", headers=admin_headers)
    second_body = second_page.json()
    assert len(second_body["items"]) == 2
    # No overlap between consecutive pages.
    first_ids = {p["id"] for p in body["items"]}
    second_ids = {p["id"] for p in second_body["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_admin_listing_searches_by_name_number_and_uhid(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    target = _create_patient(client, admin_headers, "Searchable Zephyrine", "+919888800001")
    _create_patient(client, admin_headers, "Someone Else Entirely", "+919888800002")

    by_name = client.get("/api/patients/admin?search=Zephyrine", headers=admin_headers).json()
    assert [p["id"] for p in by_name["items"]] == [target["id"]]

    by_number = client.get("/api/patients/admin?search=888800001", headers=admin_headers).json()
    assert [p["id"] for p in by_number["items"]] == [target["id"]]

    by_uhid = client.get(f"/api/patients/admin?search={target['uhid']}", headers=admin_headers).json()
    assert [p["id"] for p in by_uhid["items"]] == [target["id"]]


def test_admin_listing_default_limit_and_ordering(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    _create_patient(client, admin_headers, "Zzz Last Alphabetically", "+919888800003")
    _create_patient(client, admin_headers, "Aaa First Alphabetically", "+919888800004")

    response = client.get("/api/patients/admin", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 50
    names = [p["name"] for p in body["items"]]
    assert names == sorted(names)


def test_admin_listing_limit_capped(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/patients/admin?limit=10000", headers=admin_headers)
    assert response.status_code == 422


def test_plain_staff_can_use_admin_listing(client, db_connection):
    """Bare authentication, same tier as GET /patients and GET /patients/search
    -- browsing the registry is a read, not an admin-only action."""
    staff_headers = create_staff_and_get_headers(db_connection)
    response = client.get("/api/patients/admin", headers=staff_headers)
    assert response.status_code == 200


def test_plain_listing_unchanged_and_unpaginated(client, db_connection):
    """GET /patients (no admin suffix) keeps its original, unpaginated
    contract -- AppointmentsPanel's per-row patient lookup map still
    needs the full set."""
    admin_headers = create_admin_and_get_headers(db_connection)
    for i in range(3):
        _create_patient(client, admin_headers, f"Unpaginated Patient {i}", f"+9197{i:08d}0")

    response = client.get("/api/patients", headers=admin_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) >= 3
