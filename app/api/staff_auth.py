"""
Staff/admin authentication + RBAC (WEB P5, decomposed into permissions
by P1.a -- see migrations/0031_rbac_decomposition.sql).

Endpoints:
  POST /api/auth/staff/login                      -- username+password -> session token
  POST /api/auth/staff/logout
  GET  /api/auth/staff/me                          -- any authenticated staff
  GET  /api/auth/staff/accounts                    -- staff.manage: list staff accounts
  POST /api/auth/staff/accounts                    -- staff.manage: create a staff account
  PATCH /api/auth/staff/accounts/{id}/active       -- staff.manage: activate/deactivate
  POST /api/auth/staff/break-glass                 -- any authenticated staff: emergency
                                                       temporary grant, see require_permission
  GET  /api/auth/staff/break-glass                 -- staff.manage: list grants for review
  POST /api/auth/staff/break-glass/{id}/review      -- staff.manage: mark a grant reviewed

require_permission() is the RBAC dependency (formerly require_role(),
which every call site used with "ADMIN" and nothing else -- there was
never a require_role("STAFF")): checked server-side on every gated
route in this app, never trusting a frontend-only role check. Now wired
into every existing doctor/department/appointment-type/etc. CRUD router
that used to call require_role("ADMIN") directly, not just this file's
own account-management endpoints -- see require_permission's own
docstring for exactly what it resolves against.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Literal

from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.audit_log import record_audit_log
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
    # Every role migrations/0031_rbac_decomposition.sql seeded into
    # `roles`, now actually usable (master spec audit gap #3) --
    # migrations/0043_role_based_access.sql is what actually enforces
    # this set at the database layer (a foreign key to roles.name);
    # this Literal is just the same set spelled out for early,
    # request-time validation and OpenAPI docs, not the source of truth.
    role: Literal["ADMIN", "STAFF", "DOCTOR", "NURSE", "RECEPTIONIST", "LAB_TECH", "PHARMACIST", "BILLING"]


class StaffActiveBody(BaseModel):
    active: bool


class BreakGlassGrantBody(BaseModel):
    permission_name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    # Capped at 8 hours (one working shift) -- an "emergency override"
    # with no upper bound stops being temporary. A grant that's still
    # needed past this just gets requested again.
    duration_minutes: int = Field(gt=0, le=480)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("A reason is required for a break-glass grant")

        return value


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


def require_permission(permission_name: str):
    """
    P1.a RBAC dependency factory, e.g.
    Depends(require_permission("doctor.manage")) -- replaces the former
    require_role("ADMIN"), which every current call site used (there
    was never a require_role("STAFF") anywhere). Layers on top of
    get_current_staff exactly as require_role did: first establishes who
    is calling, then checks authorization. A caller authenticated as the
    wrong role/permission gets 403 (they ARE someone, just not
    authorized for this), distinct from the 401 get_current_staff raises
    for no/invalid session at all.

    Resolved through staff_roles -> role_permissions -> permissions
    (migrations/0031_rbac_decomposition.sql), or an active,
    non-expired break_glass_grants row for this exact permission --
    never a direct staff["role"] string comparison, which is the one
    thing this replaces staff-wide.
    """
    def dependency(staff: dict = Depends(get_current_staff)) -> dict:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM staff_roles sr
                        JOIN role_permissions rp ON rp.role_id = sr.role_id
                        JOIN permissions p ON p.id = rp.permission_id
                        WHERE sr.staff_id = %(staff_id)s
                          AND p.name = %(permission_name)s
                    ) OR EXISTS (
                        SELECT 1
                        FROM break_glass_grants g
                        WHERE g.staff_id = %(staff_id)s
                          AND g.permission_name = %(permission_name)s
                          AND g.expires_at > NOW()
                    )
                    """,
                    {"staff_id": staff["id"], "permission_name": permission_name},
                )
                has_permission = cur.fetchone()[0]

        if not has_permission:
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
def list_accounts(admin: dict = Depends(require_permission("staff.manage"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_staff_accounts(cur)


@router.post("/accounts", status_code=201)
def create_account(body: StaffCreateBody, admin: dict = Depends(require_permission("staff.manage"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = create_staff_account(cur, body.username, body.password, body.role)
            except svc_exc.UsernameAlreadyExists:
                raise HTTPException(status_code=409, detail="Username already exists")

            record_audit_log(
                cur,
                hospital_id=admin["hospital_id"],
                staff_id=admin["id"],
                action="staff.create",
                resource_type="staff",
                resource_id=result["id"],
                details={"username": result["username"], "role": result["role"]},
            )

    return result


@router.patch("/accounts/{staff_id}/active")
def set_account_active(
    staff_id: int,
    body: StaffActiveBody,
    admin: dict = Depends(require_permission("staff.manage")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = set_staff_active(cur, staff_id, body.active)
            except svc_exc.StaffNotFound:
                raise HTTPException(status_code=404, detail="Staff account not found")

            record_audit_log(
                cur,
                hospital_id=admin["hospital_id"],
                staff_id=admin["id"],
                action="staff.active_update",
                resource_type="staff",
                resource_id=staff_id,
                details={"active": result["active"]},
            )

    return result


# staff.manage is deliberately not grantable through break-glass: it's
# what governs creating/deactivating staff accounts (and thus who holds
# every other permission), so allowing a self-service, no-approval
# override for it would let any authenticated staff member escalate
# themselves to ADMIN-equivalent access with nothing but a text reason.
# Every other permission's worst case is misuse of a single resource
# area; this one's worst case is minting new access permanently.
BREAK_GLASS_INELIGIBLE_PERMISSIONS = {"staff.manage"}


@router.post("/break-glass", status_code=201)
def grant_break_glass(
    body: BreakGlassGrantBody,
    staff: dict = Depends(get_current_staff),
):
    if body.permission_name in BREAK_GLASS_INELIGIBLE_PERMISSIONS:
        raise HTTPException(
            status_code=403,
            detail="This permission cannot be granted through break-glass",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM permissions WHERE name = %s",
                (body.permission_name,),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Unknown permission")

            cur.execute(
                """
                INSERT INTO break_glass_grants (staff_id, permission_name, reason, expires_at)
                VALUES (%s, %s, %s, NOW() + make_interval(mins => %s))
                RETURNING id, staff_id, permission_name, reason, granted_at, expires_at
                """,
                (staff["id"], body.permission_name, body.reason, body.duration_minutes),
            )
            row = cur.fetchone()

            record_audit_log(
                cur,
                hospital_id=staff["hospital_id"],
                staff_id=staff["id"],
                action="break_glass.grant",
                resource_type="break_glass_grant",
                resource_id=row[0],
                details={
                    "permission_name": body.permission_name,
                    "reason": body.reason,
                    "duration_minutes": body.duration_minutes,
                },
            )

    return {
        "id": row[0],
        "staff_id": row[1],
        "permission_name": row[2],
        "reason": row[3],
        "granted_at": row[4].isoformat(),
        "expires_at": row[5].isoformat(),
    }


@router.get("/break-glass")
def list_break_glass_grants(admin: dict = Depends(require_permission("staff.manage"))):
    """Newest first -- the review queue an admin works through, not a
    per-staff self-service history."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT g.id, g.staff_id, s.username, g.permission_name, g.reason,
                       g.granted_at, g.expires_at, g.reviewed_at, g.reviewed_by_staff_id
                FROM break_glass_grants g
                JOIN staff s ON s.id = g.staff_id
                ORDER BY g.granted_at DESC
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "staff_id": row[1],
            "staff_username": row[2],
            "permission_name": row[3],
            "reason": row[4],
            "granted_at": row[5].isoformat(),
            "expires_at": row[6].isoformat(),
            "reviewed_at": row[7].isoformat() if row[7] else None,
            "reviewed_by_staff_id": row[8],
        }
        for row in rows
    ]


@router.post("/break-glass/{grant_id}/review")
def review_break_glass_grant(
    grant_id: int,
    admin: dict = Depends(require_permission("staff.manage")),
):
    """Marks a grant as reviewed -- purely a record that someone looked
    at it after the fact, never a gate on the grant itself (which took
    effect immediately at creation). Re-reviewing just updates
    reviewed_at/reviewed_by_staff_id rather than rejecting -- there's no
    meaningful "already reviewed" conflict to guard against here."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE break_glass_grants
                SET reviewed_at = NOW(), reviewed_by_staff_id = %s
                WHERE id = %s
                RETURNING id, reviewed_at
                """,
                (admin["id"], grant_id),
            )
            row = cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Grant not found")

            record_audit_log(
                cur,
                hospital_id=admin["hospital_id"],
                staff_id=admin["id"],
                action="break_glass.review",
                resource_type="break_glass_grant",
                resource_id=row[0],
            )

    return {"id": row[0], "reviewed_at": row[1].isoformat()}
