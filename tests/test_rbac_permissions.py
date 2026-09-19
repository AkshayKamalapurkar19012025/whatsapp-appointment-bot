"""
Tests for P1.a (HospitalOS build plan) -- RBAC decomposition
(migrations/0029_rbac_decomposition.sql, migrations/0030_break_glass_
grants.sql). test_admin_rbac.py already proves every existing ADMIN-only
call site behaves identically after the require_role -> require_permission
swap; these tests cover the new mechanism itself: that the resolver
actually walks staff_roles -> role_permissions -> permissions (not just
"still happens to work for ADMIN because ADMIN has everything"), the
staff_roles dual-write, and the break-glass grant/expiry/eligibility
rules.
"""

from datetime import datetime, timedelta, timezone

from tests.helpers import create_staff_and_get_headers, create_staff_for_test


def test_create_staff_account_dual_writes_staff_roles(client, db_connection):
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")

    created = client.post(
        "/api/auth/staff/accounts",
        json={"username": "dualwritecheck", "password": "some-password-1", "role": "STAFF"},
        headers=admin_headers,
    ).json()

    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT r.name
            FROM staff_roles sr
            JOIN roles r ON r.id = sr.role_id
            WHERE sr.staff_id = %s
            """,
            (created["id"],),
        )
        rows = cur.fetchall()

    assert [row[0] for row in rows] == ["STAFF"]


def test_require_permission_resolves_through_role_permissions_not_hardcoded_admin(
    client, db_connection
):
    """Granting a non-ADMIN role a permission through role_permissions
    directly (no ADMIN involved at all) must be enough for
    require_permission to allow it -- proves the resolver is actually
    walking staff_roles -> role_permissions -> permissions, not just
    coincidentally working because every test so far used ADMIN, which
    holds every permission.

    Uses a brand-new, test-only role granted to just this one staff
    account, rather than granting the permission to the built-in STAFF
    role directly -- roles/permissions/role_permissions are global
    reference data (deliberately not truncated between tests, same as
    the seeded ADMIN/STAFF roles themselves), so mutating STAFF's own
    grants here would leak into every other test in the same run. Also
    why this test explicitly deletes its own role/role_permissions rows
    at the end: staff_roles gets cleaned up for free by the next test's
    "TRUNCATE staff ... CASCADE", but roles/role_permissions rows
    survive that (they don't reference staff), so an explicit cleanup
    is the only thing standing between this test and permanently
    polluting every later test in the same database -- exactly what an
    earlier draft of this test did to test_break_glass_grant_allows_the_
    named_permission before this cleanup was added.
    """
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    # Before granting, a plain STAFF session is rejected (matches
    # test_admin_rbac.py's existing coverage).
    denied = client.post("/api/departments", json={"name": "Cardiology"}, headers=staff_headers)
    assert denied.status_code == 403

    staff_id = client.get("/api/auth/staff/me", headers=staff_headers).json()["id"]

    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO roles (name) VALUES ('TEST_ONLY_DEPARTMENT_MANAGER') RETURNING id"
        )
        role_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT %s, id FROM permissions WHERE name = 'department.manage'
            """,
            (role_id,),
        )
        cur.execute(
            "INSERT INTO staff_roles (staff_id, role_id) VALUES (%s, %s)",
            (staff_id, role_id),
        )
    db_connection.commit()

    try:
        allowed = client.post("/api/departments", json={"name": "Cardiology"}, headers=staff_headers)
        assert allowed.status_code == 200
    finally:
        # See this test's own docstring -- roles/role_permissions aren't
        # truncated between tests, so this cleanup is load-bearing, not
        # just tidiness.
        with db_connection.cursor() as cur:
            cur.execute("DELETE FROM staff_roles WHERE role_id = %s", (role_id,))
            cur.execute("DELETE FROM role_permissions WHERE role_id = %s", (role_id,))
            cur.execute("DELETE FROM roles WHERE id = %s", (role_id,))
        db_connection.commit()


def test_break_glass_grant_allows_the_named_permission(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    denied = client.post("/api/departments", json={"name": "Neurology"}, headers=staff_headers)
    assert denied.status_code == 403

    grant = client.post(
        "/api/auth/staff/break-glass",
        json={
            "permission_name": "department.manage",
            "reason": "Covering for the admin during a system outage",
            "duration_minutes": 30,
        },
        headers=staff_headers,
    )
    assert grant.status_code == 201
    body = grant.json()
    assert body["permission_name"] == "department.manage"
    assert body["reason"] == "Covering for the admin during a system outage"

    allowed = client.post("/api/departments", json={"name": "Neurology"}, headers=staff_headers)
    assert allowed.status_code == 200


def test_break_glass_grant_rejects_blank_reason(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    response = client.post(
        "/api/auth/staff/break-glass",
        json={"permission_name": "department.manage", "reason": "   ", "duration_minutes": 30},
        headers=staff_headers,
    )
    assert response.status_code == 422


def test_break_glass_grant_rejects_duration_out_of_bounds(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    too_long = client.post(
        "/api/auth/staff/break-glass",
        json={"permission_name": "department.manage", "reason": "test", "duration_minutes": 481},
        headers=staff_headers,
    )
    assert too_long.status_code == 422

    zero = client.post(
        "/api/auth/staff/break-glass",
        json={"permission_name": "department.manage", "reason": "test", "duration_minutes": 0},
        headers=staff_headers,
    )
    assert zero.status_code == 422


def test_break_glass_grant_rejects_unknown_permission(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    response = client.post(
        "/api/auth/staff/break-glass",
        json={
            "permission_name": "not_a_real_permission",
            "reason": "test",
            "duration_minutes": 30,
        },
        headers=staff_headers,
    )
    assert response.status_code == 404


def test_break_glass_cannot_grant_staff_manage(client, db_connection):
    """staff.manage governs account creation/deactivation -- allowing a
    self-service break-glass grant for it would let any authenticated
    staff member escalate themselves to ADMIN-equivalent access with
    nothing but a text reason and no approval step."""
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    response = client.post(
        "/api/auth/staff/break-glass",
        json={"permission_name": "staff.manage", "reason": "test", "duration_minutes": 30},
        headers=staff_headers,
    )
    assert response.status_code == 403

    listing = client.get("/api/auth/staff/accounts", headers=staff_headers)
    assert listing.status_code == 403


def test_expired_break_glass_grant_no_longer_grants_access(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    staff_id = client.get("/api/auth/staff/me", headers=staff_headers).json()["id"]

    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO break_glass_grants (staff_id, permission_name, reason, expires_at)
            VALUES (%s, 'department.manage', 'already expired', %s)
            """,
            (staff_id, datetime.now(timezone.utc) - timedelta(minutes=1)),
        )
    db_connection.commit()

    response = client.post("/api/departments", json={"name": "Oncology"}, headers=staff_headers)
    assert response.status_code == 403


def test_list_break_glass_grants_requires_staff_manage(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")

    client.post(
        "/api/auth/staff/break-glass",
        json={"permission_name": "department.manage", "reason": "test grant", "duration_minutes": 30},
        headers=staff_headers,
    )

    denied = client.get("/api/auth/staff/break-glass", headers=staff_headers)
    assert denied.status_code == 403

    listing = client.get("/api/auth/staff/break-glass", headers=admin_headers)
    assert listing.status_code == 200
    grants = listing.json()
    assert len(grants) == 1
    assert grants[0]["permission_name"] == "department.manage"
    assert grants[0]["reason"] == "test grant"
    assert grants[0]["reviewed_at"] is None


def test_review_break_glass_grant(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")

    grant = client.post(
        "/api/auth/staff/break-glass",
        json={"permission_name": "department.manage", "reason": "test grant", "duration_minutes": 30},
        headers=staff_headers,
    ).json()

    denied = client.post(
        f"/api/auth/staff/break-glass/{grant['id']}/review", headers=staff_headers
    )
    assert denied.status_code == 403

    reviewed = client.post(
        f"/api/auth/staff/break-glass/{grant['id']}/review", headers=admin_headers
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["reviewed_at"]

    listing = client.get("/api/auth/staff/break-glass", headers=admin_headers).json()
    assert listing[0]["reviewed_at"] is not None


def test_review_unknown_break_glass_grant_is_404(client, db_connection):
    admin_headers = create_staff_and_get_headers(db_connection, role="ADMIN")

    response = client.post("/api/auth/staff/break-glass/999999/review", headers=admin_headers)
    assert response.status_code == 404
