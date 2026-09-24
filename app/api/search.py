"""
GET /api/search (OPD/HIMS master spec section 14) -- see
app/services/search_service.py for the full design rationale.

Bare staff-readable (get_current_staff, not require_permission(...)):
same tier as GET /exceptions and GET /dashboard/stats -- searching the
same patients/appointments any STAFF session can already list/view page
by page is no more sensitive than that.
"""

from fastapi import APIRouter, Depends, Query

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.services.search_service import global_search_service

router = APIRouter(
    prefix="/search",
    tags=["Search"],
)


@router.get("")
def search(
    q: str = Query(min_length=1),
    staff: dict = Depends(get_current_staff),
):
    if not q.strip():
        return {"patients": [], "appointments": []}

    with get_connection() as conn:
        with conn.cursor() as cur:
            return global_search_service(cur, q, hospital_id=staff["hospital_id"])
