"""
Tests for app/api/doctor_appointment_types.py's own CRUD correctness --
not RBAC (tests/test_admin_rbac.py already covers who can call these
endpoints). This file exists because assign/remove/reassign had a real,
untested gap: DELETE is a soft delete (active = FALSE, not a row
delete, per migrations/0001's UNIQUE(doctor_id, appointment_type_id)),
and the original POST handler only ever INSERTed, so re-assigning a
previously-removed appointment type always failed with 409 --
permanently, since nothing in the admin UI's flow (DoctorDetail.tsx's
AppointmentTypeAssignment) ever calls PUT (the endpoint that *could*
reactivate the row) from the "assign" action.
"""

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def test_reassigning_a_removed_appointment_type_succeeds(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reassign")
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor_id = seeded["doctor_id"]
    appointment_type_id = seeded["appointment_type_id"]

    # seed_basic_doctor already assigned this type; remove it (soft
    # delete) the same way the admin UI does.
    removed = client.delete(
        f"/api/doctors/{doctor_id}/appointment-types/{appointment_type_id}",
        headers=admin_headers,
    )
    assert removed.status_code == 200

    assert appointment_type_id not in {
        t["id"]
        for t in client.get(f"/api/doctors/{doctor_id}/appointment-types").json()
    }

    # Re-assigning it -- exactly what the admin UI's "add appointment
    # type" dropdown does once GET no longer lists it as assigned --
    # must succeed, not 409 forever.
    reassigned = client.post(
        f"/api/doctors/{doctor_id}/appointment-types/{appointment_type_id}",
        json={"duration_minutes": 45},
        headers=admin_headers,
    )
    assert reassigned.status_code == 200
    body = reassigned.json()
    assert body["active"] is True
    assert body["duration_minutes"] == 45

    listed = client.get(f"/api/doctors/{doctor_id}/appointment-types").json()
    assert any(t["id"] == appointment_type_id and t["duration_minutes"] == 45 for t in listed)


def test_reassigning_an_already_removed_type_reuses_the_same_row(client, db_connection):
    # Not a new row each time -- UNIQUE(doctor_id, appointment_type_id)
    # means there can only ever be one, so remove-then-reassign must
    # UPDATE the existing row, not attempt a second INSERT.
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Reassign Row")
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor_id = seeded["doctor_id"]
    appointment_type_id = seeded["appointment_type_id"]

    client.delete(
        f"/api/doctors/{doctor_id}/appointment-types/{appointment_type_id}",
        headers=admin_headers,
    )
    reassigned = client.post(
        f"/api/doctors/{doctor_id}/appointment-types/{appointment_type_id}",
        json={"duration_minutes": 20},
        headers=admin_headers,
    )
    assert reassigned.status_code == 200

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM doctor_appointment_types WHERE doctor_id = %s AND appointment_type_id = %s",
            (doctor_id, appointment_type_id),
        )
        (row_count,) = cur.fetchone()
    assert row_count == 1


def test_assigning_an_already_active_appointment_type_still_returns_409(client, db_connection):
    # The fix must not weaken this: an assignment that's already active
    # stays a 409 (use PUT to change its duration), not a silent
    # duration-changing POST.
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Duplicate Assign")
    admin_headers = create_admin_and_get_headers(db_connection)

    response = client.post(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{seeded['appointment_type_id']}",
        json={"duration_minutes": 30},
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert "already assigned" in response.json()["detail"]
