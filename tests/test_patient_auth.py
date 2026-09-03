"""
Tests for WEB P2 -- patient authentication (mobile number -> OTP ->
login/registration), covering every case the phase spec explicitly
requires: valid OTP, invalid OTP, expired OTP, reused OTP, new patient,
existing patient, rate limiting, unauthorized requests -- plus the
registration_required intermediate state and the OTP-lock-after-max-
attempts defense.

Uses the dev-only /api/auth/patient/otp/_dev_lookup endpoint to retrieve
the mock-delivered OTP code, the same way a real client/tester would --
not by reaching into app.services.patient_auth internals, so these tests
also exercise the dev-lookup endpoint itself.
"""

from datetime import datetime, timedelta, timezone

from app import config
from app.services.patient_auth import OTP_REQUEST_RATE_LIMIT_MAX, OTP_MAX_VERIFY_ATTEMPTS
from tests.helpers import create_admin_and_get_headers


def _request_and_fetch_code(client, whatsapp_number: str) -> str:
    response = client.post(
        "/api/auth/patient/otp/request",
        json={"whatsapp_number": whatsapp_number},
    )
    assert response.status_code == 200

    lookup = client.get(
        "/api/auth/patient/otp/_dev_lookup",
        params={"whatsapp_number": whatsapp_number},
    )
    assert lookup.status_code == 200
    return lookup.json()["otp_code"]


def test_valid_otp_registers_new_patient_and_issues_session(client, db_connection):
    number = "+919820000001"
    code = _request_and_fetch_code(client, number)

    response = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": number, "otp": code, "name": "New Web Patient"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_new_patient"] is True
    assert body["patient"]["name"] == "New Web Patient"
    assert body["patient"]["whatsapp_number"] == number
    assert body["session_token"]

    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM patients WHERE whatsapp_number = %s", (number,))
        assert cur.fetchone()[0] == 1


def test_verify_without_name_for_new_number_requires_registration(client, db_connection):
    number = "+919820000002"
    code = _request_and_fetch_code(client, number)

    response = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": number, "otp": code},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["registration_required"] is True

    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM patients WHERE whatsapp_number = %s", (number,))
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT consumed_at FROM patient_otp_codes WHERE whatsapp_number = %s",
            (number,),
        )
        assert cur.fetchone()[0] is None, "OTP must stay unconsumed pending registration"

    # The SAME code should still work once a name is supplied.
    follow_up = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": number, "otp": code, "name": "Completed Registration"},
    )
    assert follow_up.status_code == 200
    assert follow_up.json()["is_new_patient"] is True


def test_existing_patient_logs_in_without_duplicate(client, db_connection):
    number = "+919820000003"
    staff_headers = create_admin_and_get_headers(db_connection)
    created = client.post(
        "/api/patients",
        json={"name": "Pre-existing Patient", "whatsapp_number": number},
        headers=staff_headers,
    )
    assert created.status_code == 200

    code = _request_and_fetch_code(client, number)
    response = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": number, "otp": code},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_new_patient"] is False
    assert body["patient"]["name"] == "Pre-existing Patient"

    with db_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM patients WHERE whatsapp_number = %s", (number,))
        assert cur.fetchone()[0] == 1


def test_invalid_otp_is_rejected(client):
    number = "+919820000004"
    _request_and_fetch_code(client, number)

    response = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": number, "otp": "000000", "name": "Whoever"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid OTP"


def test_expired_otp_is_rejected(client, db_connection):
    number = "+919820000005"
    code = _request_and_fetch_code(client, number)

    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE patient_otp_codes SET expires_at = %s WHERE whatsapp_number = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=1), number),
        )
    db_connection.commit()

    response = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": number, "otp": code, "name": "Too Late"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "OTP has expired"


def test_reused_otp_is_rejected(client):
    number = "+919820000006"
    code = _request_and_fetch_code(client, number)

    first = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": number, "otp": code, "name": "First Use"},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": number, "otp": code, "name": "Second Use"},
    )
    assert second.status_code == 401
    assert second.json()["detail"] == "OTP has already been used"


def test_otp_locked_after_max_verify_attempts(client):
    number = "+919820000007"
    code = _request_and_fetch_code(client, number)

    for _ in range(OTP_MAX_VERIFY_ATTEMPTS):
        wrong = client.post(
            "/api/auth/patient/otp/verify",
            json={"whatsapp_number": number, "otp": "111111", "name": "Attempt"},
        )
        assert wrong.status_code == 401

    # Even the correct code is now locked out -- must request a new OTP.
    locked = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": number, "otp": code, "name": "Too Late"},
    )
    assert locked.status_code == 401
    assert "Too many incorrect attempts" in locked.json()["detail"]


def test_otp_request_rate_limiting(client):
    number = "+919820000008"

    for _ in range(OTP_REQUEST_RATE_LIMIT_MAX):
        ok = client.post(
            "/api/auth/patient/otp/request",
            json={"whatsapp_number": number},
        )
        assert ok.status_code == 200

    limited = client.post(
        "/api/auth/patient/otp/request",
        json={"whatsapp_number": number},
    )
    assert limited.status_code == 429


def test_unauthorized_requests_to_me(client):
    no_header = client.get("/api/auth/patient/me")
    assert no_header.status_code == 401

    bogus_token = client.get(
        "/api/auth/patient/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert bogus_token.status_code == 401


def test_me_and_logout_with_valid_session(client):
    number = "+919820000009"
    code = _request_and_fetch_code(client, number)
    verified = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": number, "otp": code, "name": "Session Owner"},
    )
    token = verified.json()["session_token"]

    me = client.get(
        "/api/auth/patient/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me.status_code == 200
    assert me.json()["whatsapp_number"] == number

    logout = client.post(
        "/api/auth/patient/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout.status_code == 200

    me_after_logout = client.get(
        "/api/auth/patient/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_after_logout.status_code == 401


def test_dev_lookup_disabled_in_production(client, monkeypatch):
    number = "+919820000010"
    client.post("/api/auth/patient/otp/request", json={"whatsapp_number": number})

    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    try:
        response = client.get(
            "/api/auth/patient/otp/_dev_lookup",
            params={"whatsapp_number": number},
        )
    finally:
        monkeypatch.undo()

    assert response.status_code == 404
