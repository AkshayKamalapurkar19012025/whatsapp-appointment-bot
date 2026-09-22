"""
GET /api/exceptions (OPD/HIMS master spec Phase 11, sections 46-47) --
see app/services/exception_engine.py for the full design rationale.

Bare staff-readable (get_current_staff, not require_permission(...)):
same tier as GET /dashboard/stats -- viewing operational exceptions is
no more sensitive than viewing the appointments/queue data they're all
computed from, which any STAFF session can already see.
"""

from fastapi import APIRouter, Depends

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.services.exception_engine import get_active_exceptions_service

router = APIRouter(
    prefix="/exceptions",
    tags=["Exceptions"],
)


@router.get("")
def get_active_exceptions(staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return get_active_exceptions_service(cur, hospital_id=staff["hospital_id"])
