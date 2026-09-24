"""
Tests for master spec audit gap #3 -- "Role-based work is schema-only,
not real": migrations/0043_role_based_access.sql widens staff.role's
foreign key to every name in `roles` (previously CHECK-constrained to
just ADMIN/STAFF) and grants DOCTOR/PHARMACIST/BILLING a real subset of
the app's existing admin-tier permissions. NURSE/RECEPTIONIST/LAB_TECH
deliberately get no new permission rows -- see the migration's own
comment for why that's correct, not an oversight.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers


def test_account_can_be_created_with_every_seeded_role(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    for role in ("DOCTOR", "NURSE", "RECEPTIONIST", "LAB_TECH", "PHARMACIST", "BILLING"):
        response = client.post(
            "/api/auth/staff/accounts",
            json={"username": f"rbac-{role.lower()}", "password": "a-strong-password", "role": role},
            headers=admin_headers,
        )
        assert response.status_code == 201, f"{role} -> {response.status_code}: {response.text}"
        assert response.json()["role"] == role


def test_unknown_role_is_rejected(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post(
        "/api/auth/staff/accounts",
        json={"username": "rbac-bogus", "password": "a-strong-password", "role": "SURGEON"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_new_role_account_can_log_in_and_reports_its_own_role(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    client.post(
        "/api/auth/staff/accounts",
        json={"username": "rbac-login-nurse", "password": "a-strong-password", "role": "NURSE"},
        headers=admin_headers,
    )

    login = client.post(
        "/api/auth/staff/login",
        json={"username": "rbac-login-nurse", "password": "a-strong-password"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['session_token']}"}

    me = client.get("/api/auth/staff/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["role"] == "NURSE"


def test_pharmacist_role_can_manage_stock_staff_role_cannot(client, db_connection):
    pharmacist_headers = create_staff_and_get_headers(db_connection, role="PHARMACIST")
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    body = {
        "medicine_name": "RBAC Test Medicine",
        "batch_number": "RBAC-BATCH-1",
        "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
        "quantity_on_hand": 10,
    }

    denied = client.post("/api/pharmacy/stock", json=body, headers=staff_headers)
    assert denied.status_code == 403

    allowed = client.post("/api/pharmacy/stock", json=body, headers=pharmacist_headers)
    assert allowed.status_code == 200
    assert allowed.json()["medicine_name"] == "RBAC Test Medicine"


def test_nurse_receptionist_lab_tech_get_no_new_permissions(client, db_connection):
    """NURSE/RECEPTIONIST/LAB_TECH are real, loggable-in roles now, but
    migrations/0043 deliberately grants none of them any permission row
    -- an account holding one of these today has exactly STAFF's own
    access (see the migration's own comment), so each is rejected here
    the same way STAFF already is."""
    body = {
        "medicine_name": "RBAC Test Medicine 2",
        "batch_number": "RBAC-BATCH-2",
        "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
        "quantity_on_hand": 5,
    }
    for role in ("NURSE", "RECEPTIONIST", "LAB_TECH"):
        headers = create_staff_and_get_headers(db_connection, role=role)
        response = client.post("/api/pharmacy/stock", json=body, headers=headers)
        assert response.status_code == 403, f"{role} -> {response.status_code}"
