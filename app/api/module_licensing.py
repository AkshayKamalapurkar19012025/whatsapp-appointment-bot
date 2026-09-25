"""
OPD/HIMS master spec sections 67-68: module licensing/enablement.
Two distinct actions, both ADMIN-gated (this app has no separate
platform-owner role -- see migrations/0052_module_licensing.sql's own
header):

- PATCH .../modules/{module_key}/license -- the platform-level action
  ("is this hospital licensed for this module at all").
- PATCH .../modules/{module_key}/enable -- the hospital-admin action
  ("turn a licensed module on/off"), rejected with 403 if the module
  isn't licensed -- the actual enforcement of "hospital admin must NOT
  be able to self-grant paid modules".

hospital_id is an explicit path param (not implicitly the caller's own
staff["hospital_id"]) because platform-level licensing is, by
definition, a cross-hospital action -- a real platform admin manages
licenses for hospitals they don't themselves work at. The hospital-
admin enablement action stays on the same path shape for consistency,
even though in this single-tenant-in-practice app every ADMIN's own
hospital_id is the only one that will ever be passed.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.staff_auth import require_permission
from app.db.connection import get_connection
from app.services import exceptions as svc_exc
from app.services.audit_log import record_audit_log
from app.services.module_services import (
    list_hospital_modules_service,
    set_module_licensed_service,
    set_module_enabled_service,
)

router = APIRouter(prefix="/hospitals", tags=["Module Licensing"])


class ModuleLicensedUpdate(BaseModel):
    licensed: bool


class ModuleEnabledUpdate(BaseModel):
    enabled: bool


ModuleKey = Literal["LAB_RADIOLOGY", "PHARMACY", "PACKAGES"]


@router.get("/{hospital_id}/modules")
def get_hospital_modules(hospital_id: int, admin: dict = Depends(require_permission("module.manage_license"))):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_hospital_modules_service(cur, hospital_id)


@router.patch("/{hospital_id}/modules/{module_key}/license")
def update_module_licensed(
    hospital_id: int,
    module_key: ModuleKey,
    body: ModuleLicensedUpdate,
    admin: dict = Depends(require_permission("module.manage_license")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = set_module_licensed_service(
                    cur, hospital_id, module_key, licensed=body.licensed, staff_id=admin["id"]
                )
            except svc_exc.ModuleNotFound:
                raise HTTPException(status_code=404, detail="Unknown module")
            record_audit_log(
                cur,
                hospital_id=hospital_id,
                staff_id=admin["id"],
                action="module.license_updated",
                resource_type="hospital_module",
                details=result,
            )
    return result


@router.patch("/{hospital_id}/modules/{module_key}/enable")
def update_module_enabled(
    hospital_id: int,
    module_key: ModuleKey,
    body: ModuleEnabledUpdate,
    admin: dict = Depends(require_permission("module.manage_enablement")),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                result = set_module_enabled_service(
                    cur, hospital_id, module_key, enabled=body.enabled, staff_id=admin["id"]
                )
            except svc_exc.ModuleNotFound:
                raise HTTPException(status_code=404, detail="Unknown module")
            except svc_exc.ModuleNotLicensed:
                raise HTTPException(
                    status_code=403,
                    detail="This hospital is not licensed for this module -- contact the platform administrator",
                )
            record_audit_log(
                cur,
                hospital_id=hospital_id,
                staff_id=admin["id"],
                action="module.enablement_updated",
                resource_type="hospital_module",
                details=result,
            )
    return result
