"""
Tests for WEB P6 -- RBAC gating of the existing admin CRUD routers
(departments, doctors, doctor_schedule, doctor_blocks, appointment_types,
doctor_appointment_types) plus the patients.py PHI-exposure fix.

Deliberately does NOT re-test each router's underlying CRUD correctness
(create/list/duplicate-rejection/etc.) -- that's already covered by the
existing test suite (largely exercised indirectly through
tests/helpers.py's seed_basic_doctor, itself now authenticated as of
this phase). These tests are specifically about the new auth/RBAC layer:
who can call each endpoint, and who's correctly rejected -- and that the
handful of GET endpoints the patient web frontend depends on
(GET /departments, GET /departments/{id}/doctors,
GET /doctors/{id}/appointment-types) are untouched and still public.

app/api/appointments.py's own three endpoints are deliberately NOT
gated in this phase -- see docs/WEB_P6_ADMIN_RBAC.md for why (dozens of
pre-existing concurrency/exclusion-constraint tests call it directly,
and gating it is scoped to its own later phase).
"""

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def _seed_full_reference_data(client, db_connection):
    """Seed a department/doctor/appointment-type/schedule/block, all via
    the real ADMIN-gated endpoints, and return every id a PUT/DELETE
    test below needs to target an existing row."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(client, db_connection)

    schedule = client.post(
        f"/api/doctors/{seeded['doctor_id']}/schedule",
        json={"day_of_week": 6, "start_time": "09:00", "end_time": "12:00"},
        headers=admin_headers,
    ).json()

    block = client.post(
        f"/api/doctors/{seeded['doctor_id']}/blocks",
        json={
            "start_at": "2027-01-01T09:00:00+05:30",
            "end_at": "2027-01-01T10:00:00+05:30",
            "reason": "Holiday",
        },
        headers=admin_headers,
    ).json()

    return {
        **seeded,
        "schedule_id": schedule["id"],
        "block_id": block["id"],
    }


# -- Write endpoints that require ADMIN specifically --------------------

def _admin_only_write_calls(ids):
    """(method, path, json) for every write endpoint that should reject
    a STAFF session with 403 and an unauthenticated one with 401."""
    return [
        ("post", "/api/departments", {"name": "RBAC New Department"}),
        ("post", "/api/doctors", {"name": "Dr. RBAC New", "specialization": "General Medicine"}),
        (
            "delete",
            f"/api/doctors/{ids['doctor_id']}/departments/{ids['department_id']}",
            None,
        ),
        (
            "post",
            f"/api/doctors/{ids['doctor_id']}/schedule",
            {"day_of_week": 7, "start_time": "09:00", "end_time": "10:00"},
        ),
        (
            "put",
            f"/api/doctors/{ids['doctor_id']}/schedule/{ids['schedule_id']}",
            {"day_of_week": 6, "start_time": "10:00", "end_time": "11:00"},
        ),
        (
            "delete",
            f"/api/doctors/{ids['doctor_id']}/schedule/{ids['schedule_id']}",
            None,
        ),
        ("post", "/api/appointment-types", {"name": "RBAC New Type"}),
        (
            "put",
            f"/api/doctors/{ids['doctor_id']}/appointment-types/{ids['appointment_type_id']}",
            {"duration_minutes": 45},
        ),
        (
            "delete",
            f"/api/doctors/{ids['doctor_id']}/appointment-types/{ids['appointment_type_id']}",
            None,
        ),
    ]


def _call(client, method, path, json, headers=None):
    kwargs = {"headers": headers} if headers else {}
    if json is not None:
        kwargs["json"] = json
    return getattr(client, method)(path, **kwargs)


def test_admin_only_writes_require_authentication(client, db_connection):
    ids = _seed_full_reference_data(client, db_connection)

    for method, path, json in _admin_only_write_calls(ids):
        response = _call(client, method, path, json)
        assert response.status_code == 401, f"{method.upper()} {path} -> {response.status_code}"


def test_admin_only_writes_reject_staff_role(client, db_connection):
    ids = _seed_full_reference_data(client, db_connection)
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    for method, path, json in _admin_only_write_calls(ids):
        response = _call(client, method, path, json, headers=staff_headers)
        assert response.status_code == 403, f"{method.upper()} {path} -> {response.status_code}"


def test_admin_only_writes_succeed_for_admin_role(client, db_connection):
    ids = _seed_full_reference_data(client, db_connection)
    admin_headers = create_admin_and_get_headers(db_connection)

    for method, path, json in _admin_only_write_calls(ids):
        response = _call(client, method, path, json, headers=admin_headers)
        assert response.status_code < 400, f"{method.upper()} {path} -> {response.status_code} {response.text}"


def test_assign_department_to_doctor_requires_admin(client, db_connection):
    # A fresh doctor/department pair not yet assigned, so the ADMIN-role
    # success case actually exercises a real insert (the shared ids from
    # _seed_full_reference_data are already assigned to each other by
    # seed_basic_doctor).
    admin_headers = create_admin_and_get_headers(db_connection)
    department = client.post(
        "/api/departments", json={"name": "Second Department"}, headers=admin_headers
    ).json()
    doctor = client.post(
        "/api/doctors",
        json={"name": "Dr. Second", "specialization": "General Medicine"},
        headers=admin_headers,
    ).json()

    unauthenticated = client.post(f"/api/doctors/{doctor['id']}/departments/{department['id']}")
    assert unauthenticated.status_code == 401

    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    as_staff = client.post(
        f"/api/doctors/{doctor['id']}/departments/{department['id']}", headers=staff_headers
    )
    assert as_staff.status_code == 403

    as_admin = client.post(
        f"/api/doctors/{doctor['id']}/departments/{department['id']}", headers=admin_headers
    )
    assert as_admin.status_code == 200


def test_assign_doctor_appointment_type_requires_admin(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Assign Type")
    new_type = client.post(
        "/api/appointment-types", json={"name": "Second Type"}, headers=admin_headers
    ).json()

    unauthenticated = client.post(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{new_type['id']}",
        json={"duration_minutes": 20},
    )
    assert unauthenticated.status_code == 401

    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    as_staff = client.post(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{new_type['id']}",
        json={"duration_minutes": 20},
        headers=staff_headers,
    )
    assert as_staff.status_code == 403

    as_admin = client.post(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{new_type['id']}",
        json={"duration_minutes": 20},
        headers=admin_headers,
    )
    assert as_admin.status_code == 200


# -- doctor_blocks: any authenticated staff (ADMIN or STAFF) -----------

def test_doctor_blocks_require_authentication_but_allow_either_role(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Blocks")
    block_body = {
        "start_at": "2027-02-01T09:00:00+05:30",
        "end_at": "2027-02-01T10:00:00+05:30",
        "reason": "Conference",
    }

    unauthenticated = client.post(f"/api/doctors/{seeded['doctor_id']}/blocks", json=block_body)
    assert unauthenticated.status_code == 401

    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    as_staff = client.post(
        f"/api/doctors/{seeded['doctor_id']}/blocks", json=block_body, headers=staff_headers
    )
    assert as_staff.status_code == 200, as_staff.text
    block_id = as_staff.json()["id"]

    updated_body = {**block_body, "reason": "Conference (rescheduled)"}
    update = client.put(
        f"/api/doctors/{seeded['doctor_id']}/blocks/{block_id}",
        json=updated_body,
        headers=staff_headers,
    )
    assert update.status_code == 200

    admin_headers = create_admin_and_get_headers(db_connection)
    delete = client.delete(
        f"/api/doctors/{seeded['doctor_id']}/blocks/{block_id}", headers=admin_headers
    )
    assert delete.status_code == 200


# -- patients.py: PHI protection, any authenticated staff --------------

def test_patients_endpoints_require_authentication(client):
    unauthenticated_get = client.get("/api/patients")
    assert unauthenticated_get.status_code == 401

    unauthenticated_post = client.post(
        "/api/patients", json={"name": "No Auth", "whatsapp_number": "+919000000001"}
    )
    assert unauthenticated_post.status_code == 401


def test_patients_endpoints_accept_either_staff_role(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    created = client.post(
        "/api/patients",
        json={"name": "Staff Created", "whatsapp_number": "+919000000002"},
        headers=staff_headers,
    )
    assert created.status_code == 200

    admin_headers = create_admin_and_get_headers(db_connection)
    listed = client.get("/api/patients", headers=admin_headers)
    assert listed.status_code == 200
    assert any(p["name"] == "Staff Created" for p in listed.json())


# -- Regression guard: the three GETs the patient web frontend calls ---
# unauthenticated must stay exactly that way. This is the crux of this
# phase's scoping decision -- gating these would break the already-
# shipped, merged WEB P3 booking flow.

def test_patient_facing_reference_data_reads_remain_public(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Public Reads")

    departments = client.get("/api/departments")
    assert departments.status_code == 200

    doctors_in_department = client.get(f"/api/departments/{seeded['department_id']}/doctors")
    assert doctors_in_department.status_code == 200

    appointment_types_for_doctor = client.get(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types"
    )
    assert appointment_types_for_doctor.status_code == 200
