"""
Package catalog (OPD/HIMS master spec Phase 12, section 39:
migrations/0038_packages.sql). Same shape as app/api/appointment_
types.py's admin CRUD (list/create/update/toggle-active) -- a package
is a hospital-owned priced catalog entry, not a clinical workflow, so
it gets the same simple admin-managed-list treatment, not its own
service module.

GET "" is bare-staff and active-only (billing staff picking a package
to charge need to see only sellable ones); the rest is package.manage
-- same admin-only tier as every other catalog's manage permission
(appointment_type.manage, department.manage).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import psycopg

from app.api.staff_auth import get_current_staff, require_permission
from app.db.connection import get_connection
from app.services.audit_log import record_audit_log
from app.services.module_services import is_module_available

router = APIRouter(prefix="/packages", tags=["Packages"])

_COLUMNS = ("id", "name", "description", "price", "active")


def _row_to_dict(row) -> dict:
    return dict(zip(_COLUMNS, row))


class PackageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    price: float = Field(gt=0)


class PackageActiveUpdate(BaseModel):
    active: bool


@router.get("")
def list_packages(staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {', '.join(_COLUMNS)} FROM packages
                WHERE hospital_id = %s AND active = TRUE
                ORDER BY name
                """,
                (staff["hospital_id"],),
            )
            rows = cur.fetchall()
    return [_row_to_dict(row) for row in rows]


@router.get("/admin")
def list_packages_admin(admin: dict = Depends(require_permission("package.manage"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {', '.join(_COLUMNS)} FROM packages
                WHERE hospital_id = %s
                ORDER BY name
                """,
                (admin["hospital_id"],),
            )
            rows = cur.fetchall()
    return [_row_to_dict(row) for row in rows]


@router.post("")
def create_package(body: PackageCreate, admin: dict = Depends(require_permission("package.manage"))):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Master spec section 68: PACKAGES degrades to HIDDEN --
                # creating a *new* package is blocked; existing ones
                # (and any invoice already billed through one) are
                # untouched -- this check only runs on the way in.
                if not is_module_available(cur, admin["hospital_id"], "PACKAGES"):
                    raise HTTPException(
                        status_code=403,
                        detail="The Packages module isn't enabled for this hospital",
                    )
                cur.execute(
                    f"""
                    INSERT INTO packages (hospital_id, name, description, price, created_by)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING {', '.join(_COLUMNS)}
                    """,
                    (admin["hospital_id"], body.name, body.description, body.price, admin["id"]),
                )
                row = cur.fetchone()

                record_audit_log(
                    cur,
                    hospital_id=admin["hospital_id"],
                    staff_id=admin["id"],
                    action="package.create",
                    resource_type="package",
                    resource_id=row[0],
                    details={"name": row[1], "price": row[3]},
                )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="A package with that name already exists")

    return _row_to_dict(row)


@router.put("/{package_id}")
def update_package(
    package_id: int,
    body: PackageCreate,
    admin: dict = Depends(require_permission("package.manage")),
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE packages
                    SET name = %s, description = %s, price = %s, updated_at = NOW()
                    WHERE id = %s AND hospital_id = %s
                    RETURNING {', '.join(_COLUMNS)}
                    """,
                    (body.name, body.description, body.price, package_id, admin["hospital_id"]),
                )
                row = cur.fetchone()

                if row is None:
                    raise HTTPException(status_code=404, detail="Package not found")

                record_audit_log(
                    cur,
                    hospital_id=admin["hospital_id"],
                    staff_id=admin["id"],
                    action="package.update",
                    resource_type="package",
                    resource_id=row[0],
                    details={"name": row[1], "price": row[3]},
                )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="A package with that name already exists")

    return _row_to_dict(row)


@router.patch("/{package_id}/active")
def update_package_active(
    package_id: int,
    body: PackageActiveUpdate,
    admin: dict = Depends(require_permission("package.manage")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE packages
                SET active = %s, updated_at = NOW()
                WHERE id = %s AND hospital_id = %s
                RETURNING id, active
                """,
                (body.active, package_id, admin["hospital_id"]),
            )
            row = cur.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail="Package not found")

            record_audit_log(
                cur,
                hospital_id=admin["hospital_id"],
                staff_id=admin["id"],
                action="package.active_update",
                resource_type="package",
                resource_id=row[0],
                details={"active": row[1]},
            )

    return {"id": row[0], "active": row[1]}
