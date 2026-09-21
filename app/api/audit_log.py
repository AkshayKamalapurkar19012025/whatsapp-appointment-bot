"""
Audit log read endpoint (P1.b): GET /api/audit-log, staff.manage-gated --
the same permission that already governs seeing who has which role
(app/api/staff_auth.py's account-management endpoints), since this
exposes the same category of information (who did what) rather than
patient/clinical data.

Writes happen exclusively via app/services/audit_log.py's
record_audit_log, called directly from within each mutating endpoint's
own transaction -- there is no POST here, and there never should be: an
audit trail that could be created out-of-band from the action it
describes isn't trustworthy.
"""

from datetime import datetime

from fastapi import APIRouter, Depends

from app.api.staff_auth import require_permission
from app.db.connection import get_connection

router = APIRouter(prefix="/audit-log", tags=["Audit Log"])

# A filtered query with no bound is a full table scan of an append-only,
# ever-growing table -- this cap is the difference between "admin review
# screen" and "accidental denial-of-service against yourself". Filter by
# staff/action/resource/date range to see further back than this.
MAX_RESULTS = 200


@router.get("")
def list_audit_log(
    staff_id: int | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    admin: dict = Depends(require_permission("staff.manage")),
):
    where_clauses = ["a.hospital_id = %s"]
    params: list = [admin["hospital_id"]]

    if staff_id is not None:
        where_clauses.append("a.staff_id = %s")
        params.append(staff_id)
    if action is not None:
        where_clauses.append("a.action = %s")
        params.append(action)
    if resource_type is not None:
        where_clauses.append("a.resource_type = %s")
        params.append(resource_type)
    if resource_id is not None:
        where_clauses.append("a.resource_id = %s")
        params.append(resource_id)
    if date_from is not None:
        where_clauses.append("a.created_at >= %s")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("a.created_at <= %s")
        params.append(date_to)

    where_sql = " AND ".join(where_clauses)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT a.id, a.staff_id, s.username, a.action, a.resource_type,
                       a.resource_id, a.details, a.created_at
                FROM audit_log a
                LEFT JOIN staff s ON s.id = a.staff_id
                WHERE {where_sql}
                ORDER BY a.created_at DESC
                LIMIT {MAX_RESULTS}
                """,
                params,
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "staff_id": row[1],
            "staff_username": row[2],
            "action": row[3],
            "resource_type": row[4],
            "resource_id": row[5],
            "details": row[6],
            "created_at": row[7].isoformat(),
        }
        for row in rows
    ]
