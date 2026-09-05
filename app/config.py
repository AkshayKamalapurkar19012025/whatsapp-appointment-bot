"""
Application configuration, sourced from environment variables.

Nothing here changes default behaviour: with no environment variables set,
DATABASE_URL resolves to the exact same DSN that was previously hardcoded
in app/db/connection.py ("dbname=appointment_bot user=akshaykumar"), so
existing local setups keep working unchanged. Environment variables only
override that default when explicitly provided (e.g. in production).
"""

import os

from dotenv import load_dotenv

# Loads variables from a local .env file (if one exists) into os.environ.
# By default this never overrides a variable that is already set in the
# real environment (e.g. exported by the shell, or set by a container
# runtime/deploy platform) -- it only fills gaps. Safe no-op if no .env
# file is present, which is the case for anyone who already exports env
# vars directly and never had a .env before this change.
load_dotenv()


def _build_default_database_url() -> str:
    # Explicit full DSN/URL takes priority over the individual DB_* parts.
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return database_url

    db_name = os.environ.get("DB_NAME", "appointment_bot")
    db_user = os.environ.get("DB_USER", "akshaykumar")
    db_password = os.environ.get("DB_PASSWORD")
    db_host = os.environ.get("DB_HOST")
    db_port = os.environ.get("DB_PORT")

    parts = [f"dbname={db_name}", f"user={db_user}"]

    if db_password:
        parts.append(f"password={db_password}")
    if db_host:
        parts.append(f"host={db_host}")
    if db_port:
        parts.append(f"port={db_port}")

    return " ".join(parts)


DATABASE_URL = _build_default_database_url()

# Connection pool sizing. Defaults are conservative for a single-instance
# deployment; override via environment for larger-scale usage.
DB_POOL_MIN_SIZE = int(os.environ.get("DB_POOL_MIN_SIZE", "1"))
DB_POOL_MAX_SIZE = int(os.environ.get("DB_POOL_MAX_SIZE", "10"))

# Defaults to "development" so local setups and tests work unchanged with
# nothing set. A real deployment MUST set ENVIRONMENT=production -- this
# gates dev-only endpoints such as the mock-OTP lookup endpoint
# (app/api/patient_auth.py), which would otherwise let anyone read a
# patient's current OTP code.
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

# WEB P10 (security pass): cross-origin allowlist for the browser
# frontend(s), used by app/main.py's CORSMiddleware. Empty by default --
# a deployment where FastAPI serves the frontend same-origin (or a local
# dev setup using frontend/vite.config.ts's dev-time proxy) needs no
# entries here at all, since the browser never sees a cross-origin
# request either way. A separately-deployed frontend (its own domain/
# port) MUST set this explicitly, e.g.
# ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com --
# there is no wildcard fallback, since "allow any origin" would let any
# website's JavaScript call this API using a stolen bearer token from
# its own storage, not just the intended frontend(s).
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

# Real SMS delivery for patient OTP codes, via Authkey.io (see
# app/services/sms_provider.py) -- an India-focused SMS/OTP provider,
# chosen over Twilio since this app's phone numbers default to +91 (see
# app/utils/phone.py). AUTHKEY_API_KEY and AUTHKEY_TEMPLATE_ID must both
# be set for real SMS to be attempted -- with either unset (the default),
# OTP delivery stays on the pre-existing mock_sms_outbox path used by
# local dev/tests, and no Authkey API call is ever made.
AUTHKEY_API_KEY = os.environ.get("AUTHKEY_API_KEY", "")
# The DLT-approved OTP template ID from your Authkey dashboard (their
# `sid` parameter) -- not a free-text message, see sms_provider.py's
# module docstring for why.
AUTHKEY_TEMPLATE_ID = os.environ.get("AUTHKEY_TEMPLATE_ID", "")
AUTHKEY_COUNTRY_CODE = os.environ.get("AUTHKEY_COUNTRY_CODE", "91")

# Optional testing bypass: when set (and never in production, see below),
# this exact code is accepted as valid for ANY patient's OTP verification,
# in addition to the real per-number code. Lets someone testing over a
# tunnel/public URL who can't reach *_dev_lookup (e.g. it's disabled, or
# they're just not comfortable with curl) log in with a fixed code you
# tell them out of band, without needing real SMS delivery configured.
# Empty by default (disabled) -- must be set explicitly to opt in.
#
# Hard-disabled whenever ENVIRONMENT=production, regardless of this
# variable's value -- accepting a fixed, widely-known code for any phone
# number would otherwise let anyone log in as any patient.
TEST_STATIC_OTP = os.environ.get("TEST_STATIC_OTP", "") if ENVIRONMENT != "production" else ""
