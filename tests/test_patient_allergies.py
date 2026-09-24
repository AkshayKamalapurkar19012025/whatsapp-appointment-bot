"""
Tests for patient allergies (master spec section 91's clinical-safety
UX requirement): GET/POST /api/patients/{id}/allergies,
POST .../allergies/{id}/resolve.
"""

from tests.helpers import create_admin_and_get_headers


def _create_patient(client, headers, name="Allergy Test Patient", number="+919600000001"):
    return client.post(
        "/api/patients", json={"name": name, "whatsapp_number": number}, headers=headers
    ).json()


def test_list_allergies_empty_for_new_patient(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers)
    response = client.get(f"/api/patients/{patient['id']}/allergies", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_add_allergy(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers)

    response = client.post(
        f"/api/patients/{patient['id']}/allergies",
        json={"allergen": "Penicillin", "reaction": "Rash", "severity": "SEVERE"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["allergen"] == "Penicillin"
    assert body["severity"] == "SEVERE"
    assert body["active"] is True

    listed = client.get(f"/api/patients/{patient['id']}/allergies", headers=admin_headers).json()
    assert len(listed) == 1
    assert listed[0]["allergen"] == "Penicillin"


def test_invalid_severity_rejected(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers)
    response = client.post(
        f"/api/patients/{patient['id']}/allergies",
        json={"allergen": "Penicillin", "severity": "EXTREME"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_resolve_allergy_removes_from_active_list(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers)
    created = client.post(
        f"/api/patients/{patient['id']}/allergies",
        json={"allergen": "Sulfa drugs"},
        headers=admin_headers,
    ).json()

    resolved = client.post(
        f"/api/patients/{patient['id']}/allergies/{created['id']}/resolve",
        json={"reason": "Entered in error"},
        headers=admin_headers,
    )
    assert resolved.status_code == 200
    assert resolved.json()["active"] is False
    assert resolved.json()["resolved_reason"] == "Entered in error"

    listed = client.get(f"/api/patients/{patient['id']}/allergies", headers=admin_headers).json()
    assert listed == []


def test_resolve_requires_reason(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers)
    created = client.post(
        f"/api/patients/{patient['id']}/allergies", json={"allergen": "Latex"}, headers=admin_headers
    ).json()

    response = client.post(
        f"/api/patients/{patient['id']}/allergies/{created['id']}/resolve",
        json={"reason": ""},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_allergies_404_for_nonexistent_patient(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/patients/999999/allergies", headers=admin_headers)
    assert response.status_code == 404


def test_resolve_nonexistent_allergy_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers)
    response = client.post(
        f"/api/patients/{patient['id']}/allergies/999999/resolve",
        json={"reason": "n/a"},
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_allergies_require_authentication(client):
    response = client.get("/api/patients/1/allergies")
    assert response.status_code == 401
