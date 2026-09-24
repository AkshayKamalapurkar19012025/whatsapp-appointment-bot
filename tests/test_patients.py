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
    # date_of_birth/gender (migrations/0023) and the migrations/0045
    # optional detail fields all round-trip as null here -- this test
    # never set any of them, and PatientUpdate leaves them null rather
    # than inventing a value when the caller omits them. uhid
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
        "email": None,
        "alternate_whatsapp_number": None,
        "address_line": None,
        "city": None,
        "state": None,
        "pincode": None,
        "emergency_contact_name": None,
        "emergency_contact_phone": None,
        "blood_group": None,
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


def test_create_patient_accepts_optional_registration_fields(client, db_connection):
    """migrations/0045_patient_registration_fields.sql -- email,
    alternate mobile, address, emergency contact, and blood group are
    all optional and round-trip through create."""
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post(
        "/api/patients",
        json={
            "name": "Full Details Patient",
            "whatsapp_number": "+919700000010",
            "email": "patient@example.com",
            "alternate_whatsapp_number": "+919700000011",
            "address_line": "12 MG Road",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pincode": "560001",
            "emergency_contact_name": "Next of Kin",
            "emergency_contact_phone": "+919700000012",
            "blood_group": "O+",
        },
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "patient@example.com"
    assert body["alternate_whatsapp_number"] == "+919700000011"
    assert body["address_line"] == "12 MG Road"
    assert body["city"] == "Bengaluru"
    assert body["state"] == "Karnataka"
    assert body["pincode"] == "560001"
    assert body["emergency_contact_name"] == "Next of Kin"
    assert body["emergency_contact_phone"] == "+919700000012"
    assert body["blood_group"] == "O+"


def test_create_patient_still_works_with_only_name_and_number(client, db_connection):
    """Fast walk-in registration must never be blocked on the new
    fields -- none of them become required."""
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post(
        "/api/patients",
        json={"name": "Minimal Patient", "whatsapp_number": "+919700000013"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] is None
    assert body["blood_group"] is None


def test_create_patient_rejects_invalid_blood_group(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post(
        "/api/patients",
        json={"name": "Bad Blood Group Patient", "whatsapp_number": "+919700000014", "blood_group": "Z+"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_update_patient_sets_optional_registration_fields(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "Update Details Patient", "+919700000015")

    response = client.patch(
        f"/api/patients/{patient['id']}",
        json={
            "name": patient["name"],
            "whatsapp_number": patient["whatsapp_number"],
            "email": "updated@example.com",
            "blood_group": "AB-",
        },
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "updated@example.com"
    assert body["blood_group"] == "AB-"


def test_get_patient_returns_full_record(client, db_connection):
    """GET /patients/{id} -- unlike listPatientsAdmin's rows, this
    includes the optional detail columns (migrations/0045), for the
    printable Patient Registration Summary (Printing phase section 1)."""
    admin_headers = create_admin_and_get_headers(db_connection)
    created = client.post(
        "/api/patients",
        json={
            "name": "Full Record Patient",
            "whatsapp_number": "+919700000020",
            "address_line": "12 MG Road",
            "emergency_contact_name": "Next of Kin",
            "emergency_contact_phone": "+919700000021",
        },
        headers=admin_headers,
    ).json()

    response = client.get(f"/api/patients/{created['id']}", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["name"] == "Full Record Patient"
    assert body["uhid"] == created["uhid"]
    assert body["address_line"] == "12 MG Road"
    assert body["emergency_contact_name"] == "Next of Kin"
    assert body["emergency_contact_phone"] == "+919700000021"
    assert body["registered_at"]


def test_get_patient_404_for_nonexistent_patient(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/patients/999999", headers=admin_headers)
    assert response.status_code == 404


def test_get_patient_requires_authentication(client):
    response = client.get("/api/patients/1")
    assert response.status_code == 401
