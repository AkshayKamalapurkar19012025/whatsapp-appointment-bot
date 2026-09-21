"""
Tests for PATCH /api/patients/{id} (app/api/patients.py): staff
correcting a patient's name/WhatsApp number, the "verify details /
update if required" step of front-desk check-in (patient arrival
workflow Phase 2). Authentication/role gating for this endpoint is
covered alongside the rest of patients.py in test_admin_rbac.py --
these tests cover the update logic itself.
"""

from tests.helpers import create_admin_and_get_headers


def _create_patient(client, admin_headers, name, whatsapp_number):
    return client.post(
        "/api/patients",
        json={"name": name, "whatsapp_number": whatsapp_number},
        headers=admin_headers,
    ).json()


def test_update_patient_changes_name_and_number(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "Original Name", "+919700000001")

    response = client.patch(
        f"/api/patients/{patient['id']}",
        json={"name": "Corrected Name", "whatsapp_number": "+919700000009"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    # date_of_birth/gender (migrations/0023) round-trip as null here --
    # this test never set either, and PatientUpdate leaves them null
    # rather than inventing a value when the caller omits them. uhid
    # (migrations/0024) is derived from id and never changes across an
    # update, same as id itself.
    assert body == {
        "id": patient["id"],
        "name": "Corrected Name",
        "whatsapp_number": "+919700000009",
        "date_of_birth": None,
        "gender": None,
        "government_id": None,
        "uhid": patient["uhid"],
    }


def test_update_patient_not_found_is_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    response = client.patch(
        "/api/patients/999999999",
        json={"name": "Nobody", "whatsapp_number": "+919700000002"},
        headers=admin_headers,
    )

    assert response.status_code == 404


def test_update_patient_keeping_own_number_is_not_a_conflict(client, db_connection):
    """Re-submitting a patient's own current number (e.g. only the name
    changed) must succeed -- only a *different* patient already owning
    that number is a real collision."""
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "Same Number Patient", "+919700000003")

    response = client.patch(
        f"/api/patients/{patient['id']}",
        json={"name": "Same Number Patient Renamed", "whatsapp_number": "+919700000003"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Same Number Patient Renamed"


def test_update_patient_number_already_owned_by_another_patient_is_409(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    _create_patient(client, admin_headers, "First Patient", "+919700000004")
    second = _create_patient(client, admin_headers, "Second Patient", "+919700000005")

    response = client.patch(
        f"/api/patients/{second['id']}",
        json={"name": "Second Patient", "whatsapp_number": "+919700000004"},
        headers=admin_headers,
    )

    assert response.status_code == 409

    # The rejected update must not have partially applied.
    with db_connection.cursor() as cur:
        cur.execute("SELECT whatsapp_number FROM patients WHERE id = %s", (second["id"],))
        (stored_number,) = cur.fetchone()
    assert stored_number == "+919700000005"


def test_update_patient_rejects_empty_name(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "Blank Name Patient", "+919700000006")

    response = client.patch(
        f"/api/patients/{patient['id']}",
        json={"name": "   ", "whatsapp_number": "+919700000006"},
        headers=admin_headers,
    )

    assert response.status_code == 422


def test_update_patient_does_not_touch_appointments(client, db_connection):
    """Confirms the patient/encounter split from Phase 1's plan holds in
    practice: editing a patient's own record never needs to cascade
    into, or otherwise affect, any of their appointments."""
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "Cascade Check Patient", "+919700000007")

    from tests.helpers import seed_basic_doctor
    from datetime import date, timedelta

    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Patient Update",
        department_name="Patient Update Dept", appointment_type_name="Patient Update Type",
    )
    scheduling_date = date.today() + timedelta(days=10)
    while scheduling_date.isoweekday() not in (1, 2, 3, 4, 5):
        scheduling_date += timedelta(days=1)

    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()

    client.patch(
        f"/api/patients/{patient['id']}",
        json={"name": "Cascade Check Patient Renamed", "whatsapp_number": "+919700000008"},
        headers=admin_headers,
    )

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT status, patient_id FROM appointments WHERE id = %s",
            (appointment["id"],),
        )
        status, patient_id = cur.fetchone()

    assert status == "PENDING"
    assert patient_id == patient["id"]
