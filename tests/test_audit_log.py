"""
Tests for P1.b (HospitalOS build plan) -- audit log
(migrations/0033_audit_log.sql, app/services/audit_log.py). Scoping note
(not in the plan's literal text, which wasn't available when this was
built -- see the commit message): every require_permission(...)-gated
mutation P1.a wired RBAC into, plus break-glass grant/review, writes one
audit_log row. Not every representative call site is tested here (that
would just re-test each endpoint's own business logic) -- these tests
cover the audit_log mechanism itself: that a representative sample of
actions across different routers all land correctly (right staff_id,
hospital_id, resource_id, details), that a failed mutation writes no row
at all, that reads never write anything, and the GET /api/audit-log
listing endpoint's auth gating and filters.
"""

from tests.helpers import (
    create_staff_and_get_headers,
    seed_basic_doctor,
)


def _audit_rows(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT staff_id, action, resource_type, resource_id, details, hospital_id "
            "FROM audit_log ORDER BY id"
        )
        return cur.fetchall()


def test_department_create_writes_an_audit_row(client, db_connection):
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")
    admin_id = client.get("/api/auth/staff/me", headers=admin_headers).json()["id"]

    created = client.post(
        "/api/departments", json={"name": "Radiology"}, headers=admin_headers
    ).json()

    rows = _audit_rows(db_connection)
    assert len(rows) == 1
    staff_id, action, resource_type, resource_id, details, hospital_id = rows[0]
    assert staff_id == admin_id
    assert action == "department.create"
    assert resource_type == "department"
    assert resource_id == created["id"]
    assert details == {"name": "Radiology"}
    assert hospital_id == 1


def test_failed_mutation_writes_no_audit_row(client, db_connection):
    """A 404/409 from a gated endpoint must not still log an action that
    never actually happened."""
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")

    not_found = client.put(
        "/api/departments/999999", json={"name": "Ghost"}, headers=admin_headers
    )
    assert not_found.status_code == 404

    client.post("/api/departments", json={"name": "Dermatology"}, headers=admin_headers)
    duplicate = client.post(
        "/api/departments", json={"name": "Dermatology"}, headers=admin_headers
    )
    assert duplicate.status_code == 409

    rows = _audit_rows(db_connection)
    # Exactly the one successful create -- neither the 404 update nor the
    # 409 duplicate contributed a row.
    assert len(rows) == 1
    assert rows[0][1] == "department.create"


def test_read_only_endpoints_write_no_audit_rows(client, db_connection):
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")
    seed_basic_doctor(client, db_connection, doctor_name="Dr. Audit Read Check")

    client.get("/api/departments")
    client.get("/api/doctors")
    client.get("/api/appointment-types")
    client.get("/api/auth/staff/accounts", headers=admin_headers)

    rows = _audit_rows(db_connection)
    # seed_basic_doctor's own writes (department create, doctor create,
    # department assign, appointment-type create, appointment-type
    # assign, 5 schedule creates -- schedule_days defaults to 5 days) are
    # the only rows -- none of the GETs above added more. Its own
    # create_admin_and_get_headers seeds the acting admin via
    # create_staff_account() directly (not through the HTTP endpoint),
    # so that doesn't contribute a staff.create row either.
    assert len(rows) == 10
    assert all(row[1] != "" for row in rows)


def test_doctor_manage_actions_are_logged_with_correct_resource(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Audit Check")

    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")
    client.patch(
        f"/api/doctors/{seeded['doctor_id']}/active",
        json={"active": False},
        headers=admin_headers,
    )

    rows = _audit_rows(db_connection)
    active_update_rows = [r for r in rows if r[1] == "doctor.active_update"]
    assert len(active_update_rows) == 1
    staff_id, action, resource_type, resource_id, details, hospital_id = active_update_rows[0]
    assert resource_type == "doctor"
    assert resource_id == seeded["doctor_id"]
    assert details == {"active": False}


def test_break_glass_grant_and_review_are_logged(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")
    staff_id = client.get("/api/auth/staff/me", headers=staff_headers).json()["id"]
    admin_id = client.get("/api/auth/staff/me", headers=admin_headers).json()["id"]

    grant = client.post(
        "/api/auth/staff/break-glass",
        json={
            "permission_name": "department.manage",
            "reason": "covering for admin",
            "duration_minutes": 30,
        },
        headers=staff_headers,
    ).json()

    client.post(f"/api/auth/staff/break-glass/{grant['id']}/review", headers=admin_headers)

    rows = _audit_rows(db_connection)
    grant_rows = [r for r in rows if r[1] == "break_glass.grant"]
    review_rows = [r for r in rows if r[1] == "break_glass.review"]

    assert len(grant_rows) == 1
    assert grant_rows[0][0] == staff_id
    assert grant_rows[0][2] == "break_glass_grant"
    assert grant_rows[0][3] == grant["id"]
    assert grant_rows[0][4]["permission_name"] == "department.manage"

    assert len(review_rows) == 1
    assert review_rows[0][0] == admin_id
    assert review_rows[0][3] == grant["id"]


def test_staff_account_creation_is_logged(client, db_connection):
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")
    admin_id = client.get("/api/auth/staff/me", headers=admin_headers).json()["id"]

    created = client.post(
        "/api/auth/staff/accounts",
        json={"username": "auditedstaff", "password": "some-password-1", "role": "STAFF"},
        headers=admin_headers,
    ).json()

    rows = _audit_rows(db_connection)
    create_rows = [r for r in rows if r[1] == "staff.create"]
    assert len(create_rows) == 1
    assert create_rows[0][0] == admin_id
    assert create_rows[0][3] == created["id"]
    assert create_rows[0][4] == {"username": "auditedstaff", "role": "STAFF"}


def test_list_audit_log_requires_staff_manage(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")

    client.post("/api/departments", json={"name": "Pathology"}, headers=admin_headers)

    denied = client.get("/api/audit-log", headers=staff_headers)
    assert denied.status_code == 403

    listing = client.get("/api/audit-log", headers=admin_headers)
    assert listing.status_code == 200
    entries = listing.json()
    assert any(e["action"] == "department.create" for e in entries)


def test_list_audit_log_filters_by_action_and_resource_type(client, db_connection):
    """Deliberately not built on seed_basic_doctor -- it creates its own
    department internally, which would make a department.create filter
    match more than the one call made here. Two distinct, single-
    occurrence actions instead, so each filter has an unambiguous
    expected count."""
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")

    department = client.post(
        "/api/departments", json={"name": "Urology"}, headers=admin_headers
    ).json()
    doctor = client.post(
        "/api/doctors",
        json={"name": "Dr. Filter Check", "specialization": "General Medicine"},
        headers=admin_headers,
    ).json()

    by_action = client.get(
        "/api/audit-log", params={"action": "doctor.create"}, headers=admin_headers
    ).json()
    assert len(by_action) == 1
    assert by_action[0]["action"] == "doctor.create"
    assert by_action[0]["resource_id"] == doctor["id"]

    by_resource_type = client.get(
        "/api/audit-log", params={"resource_type": "department"}, headers=admin_headers
    ).json()
    assert len(by_resource_type) == 1
    assert by_resource_type[0]["resource_id"] == department["id"]


def test_list_audit_log_filters_by_staff_id(client, db_connection):
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")
    admin_id = client.get("/api/auth/staff/me", headers=admin_headers).json()["id"]

    other_admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")
    other_admin_id = client.get("/api/auth/staff/me", headers=other_admin_headers).json()["id"]

    client.post("/api/departments", json={"name": "Cardiology Filter"}, headers=admin_headers)
    client.post("/api/departments", json={"name": "Neurology Filter"}, headers=other_admin_headers)

    filtered = client.get(
        "/api/audit-log", params={"staff_id": admin_id}, headers=admin_headers
    ).json()
    assert len(filtered) == 1
    assert filtered[0]["staff_id"] == admin_id
    assert filtered[0]["staff_id"] != other_admin_id
