"""
Tests for patients.uhid (migrations/0024_patient_uhid.sql) and
GET /api/patients/search (app/api/patients.py) -- the OPD "find an
existing patient before registering" step's backend lookup. See the OPD
Patient Search & Registration redesign report for why this exists
alongside GET /patients (unchanged): that endpoint lists the whole
registry for the Patients directory page; this one is a targeted,
capped lookup meant to positively identify one patient (or a small
number of plausible matches) at the front desk before deciding whether
to register a new one.
"""

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers


def _create_patient(client, admin_headers, name, whatsapp_number, date_of_birth=None, gender=None):
    payload = {"name": name, "whatsapp_number": whatsapp_number}
    if date_of_birth is not None:
        payload["date_of_birth"] = date_of_birth
    if gender is not None:
        payload["gender"] = gender
    response = client.post("/api/patients", json=payload, headers=admin_headers)
    assert response.status_code == 200
    return response.json()


# ---------------------------------------------------------------------
# uhid
# ---------------------------------------------------------------------


def test_create_patient_returns_uhid_in_expected_format(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "UHID Patient", "+919800000001")

    assert patient["uhid"] == f"HOS-{patient['id']:07d}"


def test_uhid_is_stable_across_update(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "Stable UHID", "+919800000002")

    updated = client.patch(
        f"/api/patients/{patient['id']}",
        json={"name": "Renamed Patient", "whatsapp_number": "+919800000099"},
        headers=admin_headers,
    ).json()

    assert updated["uhid"] == patient["uhid"]


def test_uhid_is_included_in_patient_list(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "Listed UHID Patient", "+919800000003")

    listed = client.get("/api/patients", headers=admin_headers).json()
    row = next(p for p in listed if p["id"] == patient["id"])
    assert row["uhid"] == patient["uhid"]


def test_two_patients_get_distinct_uhids(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    first = _create_patient(client, admin_headers, "First Patient", "+919800000004")
    second = _create_patient(client, admin_headers, "Second Patient", "+919800000005")

    assert first["uhid"] != second["uhid"]


# ---------------------------------------------------------------------
# GET /patients/search
# ---------------------------------------------------------------------


def test_search_requires_query_or_dob(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    response = client.get("/api/patients/search", headers=admin_headers)
    assert response.status_code == 400


def test_search_by_partial_name(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "Ramesh Sakkargi", "+919800000010")
    _create_patient(client, admin_headers, "Unrelated Patient", "+919800000011")

    response = client.get("/api/patients/search", params={"q": "ramesh"}, headers=admin_headers)
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["id"] == patient["id"]
    assert results[0]["uhid"] == patient["uhid"]


def test_search_by_phone_number(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "Phone Search Patient", "+919912312312")

    response = client.get("/api/patients/search", params={"q": "9912312312"}, headers=admin_headers)
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["id"] == patient["id"]


def test_search_by_uhid(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "UHID Search Patient", "+919800000012")

    response = client.get("/api/patients/search", params={"q": patient["uhid"]}, headers=admin_headers)
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["id"] == patient["id"]


def test_search_by_dob_narrows_a_common_name(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    older = _create_patient(
        client, admin_headers, "Ramesh Sakkargi", "+919800000013", date_of_birth="1991-09-18", gender="MALE"
    )
    younger = _create_patient(
        client, admin_headers, "Ramesh Sakkargi", "+919800000014", date_of_birth="1985-01-12", gender="MALE"
    )

    response = client.get(
        "/api/patients/search",
        params={"q": "Ramesh Sakkargi", "dob": "1991-09-18"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["id"] == older["id"]
    assert results[0]["id"] != younger["id"]


def test_search_returns_multiple_matches_for_ambiguous_name(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    first = _create_patient(client, admin_headers, "Ramesh Sakkargi", "+919800000015", date_of_birth="1991-09-18")
    second = _create_patient(client, admin_headers, "Ramesh Sakkargi", "+919800000016", date_of_birth="1985-01-12")

    response = client.get("/api/patients/search", params={"q": "Ramesh Sakkargi"}, headers=admin_headers)
    assert response.status_code == 200
    results = response.json()
    ids = {r["id"] for r in results}
    assert ids == {first["id"], second["id"]}


def test_search_finds_no_patient_returns_empty_list_not_error(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    response = client.get("/api/patients/search", params={"q": "9999999999"}, headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_search_requires_authentication(client):
    response = client.get("/api/patients/search", params={"q": "anything"})
    assert response.status_code == 401


def test_search_accepts_either_staff_role(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    _create_patient(client, admin_headers, "Role Search Patient", "+919800000017")

    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    response = client.get("/api/patients/search", params={"q": "Role Search"}, headers=staff_headers)
    assert response.status_code == 200
    assert len(response.json()) == 1
