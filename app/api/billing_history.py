"""
GET /billing/invoices, GET /billing/payments (OPD/HIMS master spec
audit "subsequent gaps" list, screens 29-30) -- see
app/services/billing_history_service.py for the full design rationale.

Bare staff-readable (get_current_staff, not require_permission(...)):
same tier as GET /dashboard/billing (the existing reconciliation
report) -- viewing billing/payment history is no more sensitive than
that.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.services.billing_history_service import list_invoices_service, list_payments_service

router = APIRouter(prefix="/billing", tags=["Billing History"])


@router.get("/invoices")
def get_invoices(
    patient_name: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_invoices_service(
                cur,
                hospital_id=staff["hospital_id"],
                patient_name=patient_name,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
                offset=offset,
            )


@router.get("/payments")
def get_payments(
    patient_name: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    method: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    staff: dict = Depends(get_current_staff),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            return list_payments_service(
                cur,
                hospital_id=staff["hospital_id"],
                patient_name=patient_name,
                date_from=date_from,
                date_to=date_to,
                method=method,
                limit=limit,
                offset=offset,
            )
