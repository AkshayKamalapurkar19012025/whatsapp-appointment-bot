"""
Tests for app/api/appointment_types.py -- specifically covering the
router being reachable at all. It existed, fully implemented, since
before this project's review, but app/main.py never imported/mounted
it, so /api/appointment-types was a 404 for every caller until that was
fixed. These tests exist mainly to catch a regression of that specific
mistake (a router silently not wired into main.py), not because the
CRUD logic itself is complex.
"""


def test_create_and_list_appointment_type(client):
    created = client.post("/api/appointment-types", json={"name": "Consultation"})
    assert created.status_code == 200
    body = created.json()
    assert body["name"] == "Consultation"
    assert body["active"] is True

    listed = client.get("/api/appointment-types")
    assert listed.status_code == 200
    names = [item["name"] for item in listed.json()]
    assert "Consultation" in names


def test_duplicate_appointment_type_name_is_rejected(client):
    client.post("/api/appointment-types", json={"name": "Follow-up"})
    duplicate = client.post("/api/appointment-types", json={"name": "Follow-up"})

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Appointment type already exists"


def test_empty_name_is_rejected(client):
    response = client.post("/api/appointment-types", json={"name": "   "})
    assert response.status_code == 422
