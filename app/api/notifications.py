"""
GET/POST /api/notifications (OPD/HIMS master spec section 15) -- see
app/services/notification_center_service.py for the full design
rationale and where notifications actually get created.

Bare staff-readable/writable (get_current_staff, not
require_permission(...)): same tier as GET /exceptions -- viewing and
clearing a hospital-wide operational feed is no more sensitive than the
appointment/queue data it's generated from.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.services.notification_center_service import (
    list_notifications_service,
    mark_all_notifications_read_service,
    mark_notification_read_service,
)

router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"],
)


@router.get("")
def get_notifications(staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_notifications_service(cur, hospital_id=staff["hospital_id"])


@router.post("/{notification_id}/read")
def mark_notification_read(notification_id: int, staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            result = mark_notification_read_service(cur, notification_id, hospital_id=staff["hospital_id"])
            if result is None:
                raise HTTPException(status_code=404, detail="Notification not found")
            return result


@router.post("/read-all")
def mark_all_notifications_read(staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            updated = mark_all_notifications_read_service(cur, hospital_id=staff["hospital_id"])
            return {"updated": updated}
