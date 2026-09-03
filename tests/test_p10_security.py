"""
Tests for WEB P10 -- the security/audit pass flagged as a "P10 candidate"
across earlier phase reports (WEB P2's session-idle-timeout note, WEB
P3's CORS deferral, docs/WEB_EXPANSION_ARCHITECTURE.md's "P10 audit"
reference): configurable CORS, and a session idle timeout layered on top
of the absolute expires_at cap that has existed since WEB P2/P5.

Deliberately does not re-test the absolute-TTL/lockout/anti-enumeration
behavior WEB P2 and P5 already cover -- this file is about what's new in
this phase, not a re-run of everything session-related.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import importlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.services.patient_auth import SESSION_IDLE_TIMEOUT_MINUTES as PATIENT_IDLE_MINUTES
from app.services.staff_auth import SESSION_IDLE_TIMEOUT_MINUTES as STAFF_IDLE_MINUTES
from tests.helpers import create_admin_and_get_headers, register_and_login_web_patient


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def test_cors_denies_unlisted_origin_by_default(client):
    # The shared test app is built with ALLOWED_ORIGINS unset (empty --
    # confirmed via `env | grep ALLOWED_ORIGINS` returning nothing in
    # this environment), the same default a real deployment starts with.
    # A same-origin/no-Origin-header request (what every existing test in
    # this suite makes) is unaffected either way; this test is the one
    # that actually sends a cross-origin Origin header and checks the
    # browser-relevant response header.
    response = client.get(
        "/api/health/db", headers={"Origin": "https://not-allowlisted.example.com"}
    )
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers.keys()}


def test_cors_middleware_allows_listed_origin_rejects_others():
    # Exercises the exact CORSMiddleware wiring app/main.py uses
    # (allow_credentials=False, the same methods/headers), on a minimal
    # standalone app rather than the shared test app -- ALLOWED_ORIGINS
    # is read once from the environment at app.main import time, so
    # there's no way to flip it per-test on the already-imported shared
    # app without reloading (and re-opening) the whole application.
    # This proves the parameters we pass to CORSMiddleware behave as
    # intended; test_allowed_origins_env_var_parses_comma_separated_list
    # below separately proves app.config parses the env var into exactly
    # that kind of list.
    probe_app = FastAPI()
    probe_app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://allowed.example.com"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @probe_app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(probe_app) as probe_client:
        allowed = probe_client.get("/ping", headers={"Origin": "https://allowed.example.com"})
        assert allowed.headers.get("access-control-allow-origin") == "https://allowed.example.com"

        denied = probe_client.get("/ping", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in {k.lower() for k in denied.headers.keys()}


def test_cors_allows_every_http_method_the_api_actually_uses(client):
    # A real regression: allow_methods in app/main.py's CORSMiddleware
    # once omitted PATCH, even though PATCH /api/auth/staff/accounts/
    # {id}/active (activate/deactivate a staff account) is a real,
    # frontend-used endpoint -- StaffAccountsPanel.tsx's Deactivate/
    # Reactivate button. A cross-origin frontend deployment would have
    # had its browser block that request at the CORS preflight stage,
    # silently breaking the only account-activation control in the
    # admin UI, with nothing else in this suite ever sending a real
    # preflight against the actual app to catch it (the sibling test
    # above only checks a hand-copied method list on a throwaway probe
    # app, not app/main.py's real one). Drives an actual CORS preflight
    # (OPTIONS + Access-Control-Request-Method) against the real app for
    # every HTTP method any route in this API uses, per RFC -- what a
    # browser sends before the real cross-origin request.
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        response = client.options(
            "/api/auth/staff/accounts/1/active",
            headers={
                "Origin": "https://not-allowlisted.example.com",
                "Access-Control-Request-Method": method,
            },
        )
        allowed = response.headers.get("access-control-allow-methods", "")
        assert method in allowed, f"{method} missing from Access-Control-Allow-Methods: {allowed!r}"


def test_allowed_origins_env_var_parses_comma_separated_list(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        " https://a.example.com ,https://b.example.com,,  ",
    )
    import app.config as config

    importlib.reload(config)
    try:
        assert config.ALLOWED_ORIGINS == [
            "https://a.example.com",
            "https://b.example.com",
        ]
    finally:
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        importlib.reload(config)
        assert config.ALLOWED_ORIGINS == []


# ---------------------------------------------------------------------------
# Staff session idle timeout
# ---------------------------------------------------------------------------


def test_staff_session_idle_timeout_rejects_after_idle_window(client, db_connection):
    headers = create_admin_and_get_headers(db_connection)
    token = headers["Authorization"].removeprefix("Bearer ")

    still_fresh = client.get("/api/auth/staff/me", headers=headers)
    assert still_fresh.status_code == 200

    idle_past_cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=STAFF_IDLE_MINUTES + 1
    )
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE staff_sessions SET last_seen_at = %s WHERE token_hash = %s",
            (idle_past_cutoff, _hash_token(token)),
        )
    db_connection.commit()

    response = client.get("/api/auth/staff/me", headers=headers)
    assert response.status_code == 401


def test_staff_session_last_seen_at_advances_on_activity(client, db_connection):
    headers = create_admin_and_get_headers(db_connection)
    token = headers["Authorization"].removeprefix("Bearer ")
    token_hash = _hash_token(token)

    # Back-date last_seen_at (but still well inside the idle window) so a
    # subsequent authenticated call's effect on it is unambiguous rather
    # than indistinguishable from "already close to now" at login time.
    backdated = datetime.now(timezone.utc) - timedelta(
        minutes=min(STAFF_IDLE_MINUTES - 5, 10)
    )
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE staff_sessions SET last_seen_at = %s WHERE token_hash = %s",
            (backdated, token_hash),
        )
    db_connection.commit()

    response = client.get("/api/auth/staff/me", headers=headers)
    assert response.status_code == 200

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT last_seen_at FROM staff_sessions WHERE token_hash = %s",
            (token_hash,),
        )
        (advanced_last_seen_at,) = cur.fetchone()

    assert advanced_last_seen_at > backdated


# ---------------------------------------------------------------------------
# Patient session idle timeout
# ---------------------------------------------------------------------------


def test_patient_session_idle_timeout_rejects_after_idle_window(client, db_connection):
    token = register_and_login_web_patient(client, "+919400055501", "P10 Idle Patient")

    still_fresh = client.get("/api/auth/patient/me", headers={"Authorization": f"Bearer {token}"})
    assert still_fresh.status_code == 200

    idle_past_cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=PATIENT_IDLE_MINUTES + 1
    )
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE patient_sessions SET last_seen_at = %s WHERE token_hash = %s",
            (idle_past_cutoff, _hash_token(token)),
        )
    db_connection.commit()

    response = client.get("/api/auth/patient/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_patient_session_last_seen_at_advances_on_activity(client, db_connection):
    token = register_and_login_web_patient(client, "+919400055502", "P10 Activity Patient")
    token_hash = _hash_token(token)

    backdated = datetime.now(timezone.utc) - timedelta(
        minutes=min(PATIENT_IDLE_MINUTES - 5, 10)
    )
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE patient_sessions SET last_seen_at = %s WHERE token_hash = %s",
            (backdated, token_hash),
        )
    db_connection.commit()

    response = client.get("/api/auth/patient/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT last_seen_at FROM patient_sessions WHERE token_hash = %s",
            (token_hash,),
        )
        (advanced_last_seen_at,) = cur.fetchone()

    assert advanced_last_seen_at > backdated
