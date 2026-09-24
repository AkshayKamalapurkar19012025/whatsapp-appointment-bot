"""
GET /analytics/waiting-time (OPD/HIMS master spec audit "subsequent
gaps" list, screen 33) -- see
app/services/waiting_time_analytics_service.py for the full design
rationale.

Bare staff-readable (get_current_staff, not require_permission(...)):
same tier as GET /dashboard/trends and GET /dashboard/billing --
viewing a wait-time trend is no more sensitive than those.
"""

from fastapi import APIRouter, Depends, Query

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.services.waiting_time_analytics_service import get_waiting_time_analytics_service

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/waiting-time")
def get_waiting_time_analytics(
    days: int = Query(default=14, ge=7, le=90),
    doctor_id: int | None = None,
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return get_waiting_time_analytics_service(
                cur,
                hospital_id=staff["hospital_id"],
                days=days,
                doctor_id=doctor_id,
            )
