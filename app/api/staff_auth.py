"""
Staff/admin authentication + RBAC (WEB P5).

Endpoints:
  POST /api/auth/staff/login                      -- username+password -> session token
  POST /api/auth/staff/logout
  GET  /api/auth/staff/me                          -- any authenticated staff
  GET  /api/auth/staff/accounts                    -- ADMIN only: list staff accounts
  POST /api/auth/staff/accounts                    -- ADMIN only: create a staff account
  PATCH /api/auth/staff/accounts/{id}/active       -- ADMIN only: activate/deactivate

require_role() is the RBAC dependency
(docs/WEB_EXPANSION_ARCHITECTURE.md section 8): checked server-side on
every gated route below, never trusting a frontend-only role check.
Nothing outside this router is gated by it yet -- wiring RBAC into the
existing doctor/department/etc. CRUD routers is a separate, later phase
(the "Admin web API" step), matching the same P2-then-P3 precedent
patient auth followed (P2 built the auth subsystem alone; P3 wired it
into a consumer). The account-management endpoints above exist here
because they're part of the auth subsystem's own self-management surface
(bootstrapping and testing RBAC needs *some* way to create an account),
not a preview of that later phase.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.staff_auth import (
    login,
    get_staff_by_session_token,
    revoke_session,
)
from app.services.staff_management import (
    create_staff_account,
    list_staff_accounts,
    set_staff_active,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/auth/staff",
    tags=["Staff Authentication"],
)


class StaffLoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class StaffCreateBody(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=200)
    role: Literal["ADMIN", "STAFF"]


class StaffActiveBody(BaseModel):
    active: bool


def get_current_staff(authorization: str | None = Header(default=None)) -> dict:
    """
    FastAPI dependency resolving the bearer session token in the
    Authorization header to the staff account it belongs to. Mirrors
    app/api/patient_auth.py's get_current_patient, but deliberately a
    separate function (not shared/generalized) -- staff and patient are
    distinct subject types with their own session tables, and must never
    be authenticatable through each other's token.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization[len("Bearer "):].strip()

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                staff = get_staff_by_session_token(cur, token)
            except svc_exc.InvalidSession:
                raise HTTPException(status_code=401, detail="Invalid or expired session")

    return staff


def require_role(role: str):
    """RBAC dependency factory, e.g. Depends(require_role("ADMIN")).
    Layers on top of get_current_staff: first establishes who's calling,
    then checks their role. A caller authenticated as the wrong role gets
    403 (they ARE someone, just not authorized for this), distinct from
    the 401 get_current_staff raises for no/invalid session at all."""
    def dependency(staff: dict = Depends(get_current_staff)) -> dict:
        if staff["role"] != role:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return staff

    return dependency


@router.post("/login")
def staff_login(body: StaffLoginBody):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = login(cur, body.username, body.password)
            except svc_exc.InvalidCredentials:
                # login() already wrote a failed-attempt counter bump for
                # a known username -- that must survive even though this
                # request is reported as a 401, or StaffAccountLocked
                # below could never be reached. Raising HTTPException
                # would otherwise propagate out of the `with
                # get_connection()` block and roll that increment back
                # (see app/db/connection.py's docstring: exception ->
                # rollback, clean exit -> commit). Same pattern as
                # app/api/patient_auth.py's OtpInvalid handling.
                conn.commit()
                raise HTTPException(status_code=401, detail="Invalid username or password")
            except svc_exc.StaffAccountLocked:
                raise HTTPException(
                    status_code=423,
                    detail="Account temporarily locked due to too many failed login attempts",
                )
            except svc_exc.StaffAccountInactive:
                raise HTTPException(status_code=403, detail="This account has been deactivated")

    return result


@router.get("/me")
def get_me(staff: dict = Depends(get_current_staff)):
    return staff


@router.post("/logout")
def staff_logout(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        # Logging out without a session is a no-op success, not an
        # error -- there's nothing to revoke, and the caller's goal (be
        # logged out) is already true.
        return {"message": "Logged out"}

    token = authorization[len("Bearer "):].strip()

    with get_connection() as conn:
        with conn.cursor() as cur:
            revoke_session(cur, token)

    return {"message": "Logged out"}


@router.get("/accounts")
def list_accounts(admin: dict = Depends(require_role("ADMIN"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_staff_accounts(cur)


@router.post("/accounts", status_code=201)
def create_account(body: StaffCreateBody, admin: dict = Depends(require_role("ADMIN"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return create_staff_account(cur, body.username, body.password, body.role)
            except svc_exc.UsernameAlreadyExists:
                raise HTTPException(status_code=409, detail="Username already exists")


@router.patch("/accounts/{staff_id}/active")
def set_account_active(
    staff_id: int,
    body: StaffActiveBody,
    admin: dict = Depends(require_role("ADMIN")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                return set_staff_active(cur, staff_id, body.active)
            except svc_exc.StaffNotFound:
                raise HTTPException(status_code=404, detail="Staff account not found")
