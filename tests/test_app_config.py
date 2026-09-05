"""
Tests for GET /api/app-config -- the small, public, non-sensitive
endpoint that exposes app.config.DEFAULT_TIMEZONE so the frontend never
has to hardcode a timezone for display purposes (e.g. the patient site's
header clock, see frontend/src/LiveClock.tsx).
"""

from app.config import DEFAULT_TIMEZONE
from app.utils.timezone import validate_timezone


def test_app_config_returns_default_timezone(client):
    response = client.get("/api/app-config")
    assert response.status_code == 200
    body = response.json()
    assert body == {"default_timezone": DEFAULT_TIMEZONE}


def test_app_config_default_timezone_is_valid_iana_name(client):
    response = client.get("/api/app-config")
    assert validate_timezone(response.json()["default_timezone"])


def test_app_config_requires_no_authentication(client):
    # Deliberately public -- no Authorization header sent.
    response = client.get("/api/app-config")
    assert response.status_code == 200
