import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from app import config
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.patient_auth import (
    request_otp,
    verify_otp,
    get_patient_by_session_token,
    revoke_session,
)
from app.utils.phone import normalize_whatsapp_number

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/auth/patient",
    tags=["Patient Authentication"],
)


class OtpRequestBody(BaseModel):
    whatsapp_number: str = Field(min_length=1, max_length=30)

    @field_validator("whatsapp_number")
    @classmethod
    def validate_whatsapp_number(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("WhatsApp number cannot be empty")
        return normalize_whatsapp_number(value)


class OtpVerifyBody(BaseModel):
    whatsapp_number: str = Field(min_length=1, max_length=30)
    otp: str = Field(min_length=1, max_length=10)
    name: str | None = Field(default=None, max_length=150)

    @field_validator("whatsapp_number")
    @classmethod
    def validate_whatsapp_number(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("WhatsApp number cannot be empty")
        return normalize_whatsapp_number(value)


def get_current_patient(authorization: str | None = Header(default=None)):
    """
    FastAPI dependency resolving the bearer session token in the
    Authorization header to the patient it belongs to. Shared by this
    router's own /me and /logout below, and intended for WEB P4's
    patient-facing scheduling endpoints to depend on too, so a patient's
    identity always comes from a verified session -- never from a
    client-supplied patient_id (see app/services/appointment_services.py's
    requesting_patient_id, which this is meant to feed).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization[len("Bearer "):].strip()

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                patient = get_patient_by_session_token(cur, token)
            except svc_exc.InvalidSession:
                raise HTTPException(status_code=401, detail="Invalid or expired session")

    return patient


@router.post("/otp/request")
def otp_request(body: OtpRequestBody):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                request_otp(cur, body.whatsapp_number)
            except svc_exc.OtpRateLimited:
                raise HTTPException(
                    status_code=429,
                    detail="Too many OTP requests. Please try again later.",
                )

    return {"message": "OTP sent"}


@router.post("/otp/verify")
def otp_verify(body: OtpVerifyBody):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = verify_otp(
                    cur,
                    body.whatsapp_number,
                    body.otp,
                    name=body.name,
                )
            except svc_exc.RegistrationRequired:
                return {
                    "registration_required": True,
                    "whatsapp_number": body.whatsapp_number,
                }
            except svc_exc.OtpNotFound:
                conn.commit()
                raise HTTPException(status_code=401, detail="No OTP was requested for this number")
            except svc_exc.OtpExpired:
                conn.commit()
                raise HTTPException(status_code=401, detail="OTP has expired")
            except svc_exc.OtpAlreadyUsed:
                conn.commit()
                raise HTTPException(status_code=401, detail="OTP has already been used")
            except svc_exc.OtpLocked:
                conn.commit()
                raise HTTPException(
                    status_code=401,
                    detail="Too many incorrect attempts. Please request a new OTP.",
                )
            except svc_exc.OtpInvalid:
                # Unlike the other branches above, verify_otp() has
                # already written an attempt_count increment here -- it
                # must survive even though this request is reported as a
                # 401, or the attempt limit (OtpLocked above) could never
                # be reached. Raising HTTPException would otherwise
                # propagate out of the `with get_connection()` block and
                # roll that increment back (see app/db/connection.py's
                # docstring: exception -> rollback, clean exit -> commit).
                conn.commit()
                raise HTTPException(status_code=401, detail="Invalid OTP")

    return result


@router.get("/me")
def get_me(patient: dict = Depends(get_current_patient)):
    return patient


@router.post("/logout")
def logout(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        # Logging out without a session is a no-op success, not an error --
        # there's nothing to revoke, and the caller's goal (be logged out)
        # is already true.
        return {"message": "Logged out"}

    token = authorization[len("Bearer "):].strip()

    with get_connection() as conn:
        with conn.cursor() as cur:
            revoke_session(cur, token)

    return {"message": "Logged out"}


@router.get("/otp/_dev_lookup")
def otp_dev_lookup(whatsapp_number: str):
    """
    Dev/test-only: returns the most recent mock "SMS" sent to a number,
    including its OTP code in the clear. This is the mock provider's
    outbox (migrations/0004, generalized in WEB P8 to hold other kinds
    of notification too -- see app/services/notifications.py), not the
    hashed verification record -- see app/services/patient_auth.py's
    module docstring. Disabled whenever ENVIRONMENT=production; returns
    404 rather than 403 so its existence isn't revealed in a production
    deployment either.

    Filters on kind='OTP' explicitly (added in WEB P8) -- without it,
    this would return whatever mock_sms_outbox row for this number is
    newest regardless of kind, which silently breaks the moment a
    patient's first action after OTP login is a web scheduling/cancel/
    reschedule (now also written to this same table).
    """
    if config.ENVIRONMENT == "production":
        raise HTTPException(status_code=404, detail="Not found")

    whatsapp_number = normalize_whatsapp_number(whatsapp_number.strip())

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT otp_code, message_body, created_at
                FROM mock_sms_outbox
                WHERE whatsapp_number = %s
                  AND kind = 'OTP'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (whatsapp_number,),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="No mock OTP found for this number")

    return {
        "whatsapp_number": whatsapp_number,
        "otp_code": row[0],
        "message": row[1],
        "sent_at": row[2].isoformat(),
    }
