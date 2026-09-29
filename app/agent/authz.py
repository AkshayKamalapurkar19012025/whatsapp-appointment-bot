"""
Authorization context for agent tasks.

A valid task is not an authorized task. The context is built HERE, on the
server, from the human's authenticated staff account -- never from a
request body and never from a model -- and it is rebuilt fresh whenever a
task resumes (permissions revoked or an account deactivated mid-task take
effect immediately). Permission names resolve through
app/services/staff_permissions.py, the same query the existing
require_permission dependency uses; the agent layer has no authorization
logic of its own beyond comparing what that returns.
"""

from app.agent.models import AuthContext, PatientScope
from app.services.staff_permissions import (
    list_staff_permissions,
    load_active_staff,
    staff_department_scope,
)


def build_auth_context(
    cur,
    staff: dict,
    *,
    patient_ids: list[int] | None = None,
    session_id: str = "-",
) -> AuthContext:
    return AuthContext(
        actor_id=staff["id"],
        actor_role=staff["role"],
        facility_id=staff["hospital_id"],
        department_id=staff_department_scope(cur, staff["id"]),
        permissions=tuple(list_staff_permissions(cur, staff["id"])),
        patient_scope=(
            PatientScope(mode="patients", patient_ids=tuple(patient_ids))
            if patient_ids
            else PatientScope()
        ),
        session_id=session_id,
    )


def reload_auth_context(cur, stored: dict) -> AuthContext | None:
    """Rebuild the initiator's context from the stored one, with FRESH
    permissions. None if the account no longer exists / is inactive."""
    staff = load_active_staff(cur, stored["actor_id"])
    if staff is None or staff["hospital_id"] != stored["facility_id"]:
        return None
    scope = stored.get("patient_scope") or {}
    return build_auth_context(
        cur,
        staff,
        patient_ids=list(scope.get("patient_ids", [])) if scope.get("mode") == "patients" else None,
        session_id=stored.get("session_id", "-"),
    )
