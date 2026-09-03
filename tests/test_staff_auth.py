"""
Tests for WEB P5 -- staff/admin authentication (username + password) and
RBAC, covering: valid login, wrong username, wrong password (both map to
the same generic message -- no username enumeration), inactive account,
account lockout after repeated failures and its expiry, session
expiry/logout/deactivation invalidation, unauthorized requests, and the
require_role RBAC dependency (403 for the wrong role, 200/201 for the
right one) exercised through the real ADMIN-only account-management
endpoints -- not just unit-tested in isolation.
"""

from datetime import datetime, timedelta, timezone

from tests.helpers import create_staff_for_test
from app.services.staff_auth import MAX_FAILED_LOGIN_ATTEMPTS


def _login(client, username: str, password: str):
    return client.post(
        "/api/auth/staff/login",
        json={"username": username, "password": password},
    )


def test_valid_login_issues_session(client, db_connection):
    create_staff_for_test(db_connection, username="staffuser1", password="correct-horse-1")

    response = _login(client, "staffuser1", "correct-horse-1")

    assert response.status_code == 200
    body = response.json()
    assert body["staff"]["username"] == "staffuser1"
    assert body["staff"]["role"] == "STAFF"
    assert body["session_token"]


def test_login_is_case_insensitive_on_username(client, db_connection):
    create_staff_for_test(db_connection, username="mixedcase", password="correct-horse-2")

    response = _login(client, "MixedCase", "correct-horse-2")

    assert response.status_code == 200


def test_unknown_username_is_rejected(client):
    response = _login(client, "nobody-like-this-exists", "whatever")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_wrong_password_is_rejected_with_the_same_message_as_unknown_username(client, db_connection):
    create_staff_for_test(db_connection, username="staffuser3", password="correct-horse-3")

    unknown_username = _login(client, "definitely-not-a-real-user", "whatever")
    wrong_password = _login(client, "staffuser3", "wrong-password")

    assert unknown_username.status_code == wrong_password.status_code == 401
    assert unknown_username.json()["detail"] == wrong_password.json()["detail"]


def test_inactive_account_is_rejected_distinctly(client, db_connection):
    account = create_staff_for_test(db_connection, username="staffuser4", password="correct-horse-4")
    with db_connection.cursor() as cur:
        cur.execute("UPDATE staff SET active = FALSE WHERE id = %s", (account["id"],))
    db_connection.commit()

    response = _login(client, "staffuser4", "correct-horse-4")

    assert response.status_code == 403
    assert response.json()["detail"] == "This account has been deactivated"


def test_account_locks_after_max_failed_attempts(client, db_connection):
    create_staff_for_test(db_connection, username="staffuser5", password="correct-horse-5")

    for _ in range(MAX_FAILED_LOGIN_ATTEMPTS):
        wrong = _login(client, "staffuser5", "wrong-password")
        assert wrong.status_code == 401

    # Even the correct password is now locked out.
    locked = _login(client, "staffuser5", "correct-horse-5")
    assert locked.status_code == 423
    assert "locked" in locked.json()["detail"].lower()


def test_lockout_expires_and_allows_a_fresh_attempt(client, db_connection):
    account = create_staff_for_test(db_connection, username="staffuser6", password="correct-horse-6")

    for _ in range(MAX_FAILED_LOGIN_ATTEMPTS):
        _login(client, "staffuser6", "wrong-password")

    # Simulate the lockout window having already elapsed.
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE staff SET locked_until = %s WHERE id = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=1), account["id"]),
        )
    db_connection.commit()

    response = _login(client, "staffuser6", "correct-horse-6")
    assert response.status_code == 200


def test_unauthorized_requests_to_me(client):
    no_header = client.get("/api/auth/staff/me")
    assert no_header.status_code == 401

    bogus_token = client.get(
        "/api/auth/staff/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert bogus_token.status_code == 401


def test_me_and_logout_with_valid_session(client, db_connection):
    create_staff_for_test(db_connection, username="staffuser7", password="correct-horse-7")
    token = _login(client, "staffuser7", "correct-horse-7").json()["session_token"]

    me = client.get("/api/auth/staff/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "staffuser7"

    logout = client.post("/api/auth/staff/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout.status_code == 200

    me_after_logout = client.get("/api/auth/staff/me", headers={"Authorization": f"Bearer {token}"})
    assert me_after_logout.status_code == 401


def test_deactivating_account_invalidates_its_existing_session(client, db_connection):
    account = create_staff_for_test(db_connection, username="staffuser8", password="correct-horse-8", role="ADMIN")
    token = _login(client, "staffuser8", "correct-horse-8").json()["session_token"]

    me = client.get("/api/auth/staff/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200

    admin_headers = {"Authorization": f"Bearer {token}"}
    deactivate = client.patch(
        f"/api/auth/staff/accounts/{account['id']}/active",
        json={"active": False},
        headers=admin_headers,
    )
    assert deactivate.status_code == 200

    me_after_deactivation = client.get("/api/auth/staff/me", headers={"Authorization": f"Bearer {token}"})
    assert me_after_deactivation.status_code == 401


def test_unauthenticated_request_to_admin_only_endpoint_is_401_not_403(client):
    # No session at all must fail authentication (401) before RBAC's own
    # role check ever runs (403) -- require_role delegates to
    # get_current_staff first, not "unknown role" -> reject.
    response = client.get("/api/auth/staff/accounts")

    assert response.status_code == 401


def test_staff_role_is_forbidden_from_admin_only_accounts_endpoint(client, db_connection):
    create_staff_for_test(db_connection, username="staffuser9", password="correct-horse-9", role="STAFF")
    token = _login(client, "staffuser9", "correct-horse-9").json()["session_token"]

    response = client.get("/api/auth/staff/accounts", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


def test_admin_role_can_list_create_and_deactivate_staff_accounts(client, db_connection):
    create_staff_for_test(db_connection, username="admin1", password="correct-horse-10", role="ADMIN")
    token = _login(client, "admin1", "correct-horse-10").json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    listing = client.get("/api/auth/staff/accounts", headers=headers)
    assert listing.status_code == 200
    assert any(a["username"] == "admin1" for a in listing.json())
    # password_hash must never be exposed.
    assert all("password_hash" not in a and "password" not in a for a in listing.json())

    created = client.post(
        "/api/auth/staff/accounts",
        json={"username": "newstaffmember", "password": "brand-new-password", "role": "STAFF"},
        headers=headers,
    )
    assert created.status_code == 201
    new_id = created.json()["id"]
    assert created.json()["active"] is True

    # The newly created account can log in with the password just set.
    new_login = _login(client, "newstaffmember", "brand-new-password")
    assert new_login.status_code == 200

    deactivated = client.patch(
        f"/api/auth/staff/accounts/{new_id}/active",
        json={"active": False},
        headers=headers,
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False

    now_locked_out = _login(client, "newstaffmember", "brand-new-password")
    assert now_locked_out.status_code == 403


def test_create_account_rejects_duplicate_username(client, db_connection):
    create_staff_for_test(db_connection, username="admin2", password="correct-horse-11", role="ADMIN")
    token = _login(client, "admin2", "correct-horse-11").json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post(
        "/api/auth/staff/accounts",
        json={"username": "duplicateuser", "password": "some-password-1", "role": "STAFF"},
        headers=headers,
    )
    assert first.status_code == 201

    second = client.post(
        "/api/auth/staff/accounts",
        json={"username": "duplicateuser", "password": "some-password-2", "role": "STAFF"},
        headers=headers,
    )
    assert second.status_code == 409


def test_deactivate_nonexistent_account_returns_404(client, db_connection):
    create_staff_for_test(db_connection, username="admin3", password="correct-horse-12", role="ADMIN")
    token = _login(client, "admin3", "correct-horse-12").json()["session_token"]

    response = client.patch(
        "/api/auth/staff/accounts/999999/active",
        json={"active": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
