"""
Admin dashboard summary stats -- a single read-only endpoint backing the
Dashboard landing page (frontend/src/admin/DashboardPanel.tsx). Staff-
readable (get_current_staff, not require_role("ADMIN")): viewing these
counts is no more sensitive than viewing the appointments list, which any
STAFF session can already do via GET /appointments.

This app's appointments only ever carry status BOOKED or CANCELLED (see
migrations/0001_baseline_schema.sql's column comment) -- there is no
Pending/Confirmed/Completed/Rejected/Visited lifecycle to report on, so
the stat cards here are limited to what's actually queryable: today's,
upcoming, total (lifetime, any status) and cancelled appointment counts,
plus total active doctors and total patients.
"""

from fastapi import APIRouter, Depends

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
)


@router.get("/stats")
def get_dashboard_stats(staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            # "Today" is evaluated in each doctor's own local timezone
            # (joined in from doctors.timezone) rather than server/UTC
            # time -- the same per-doctor-local-day convention GET
            # /appointments already uses for its date_from/date_to
            # filters, just computed in SQL here since this is an
            # aggregate count rather than a per-row display value.
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE a.status = 'BOOKED'
                        AND (a.start_at AT TIME ZONE d.timezone)::date
                            = (NOW() AT TIME ZONE d.timezone)::date
                    ) AS today_appointments,
                    COUNT(*) FILTER (
                        WHERE a.status = 'BOOKED' AND a.start_at > NOW()
                    ) AS upcoming_appointments,
                    COUNT(*) AS total_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'CANCELLED') AS cancelled_appointments
                FROM appointments a
                JOIN doctors d ON d.id = a.doctor_id
                """
            )
            today, upcoming, total, cancelled = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM doctors WHERE active = TRUE")
            (total_doctors,) = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM patients")
            (total_patients,) = cur.fetchone()

    return {
        "today_appointments": today,
        "upcoming_appointments": upcoming,
        "total_appointments": total,
        "cancelled_appointments": cancelled,
        "total_doctors": total_doctors,
        "total_patients": total_patients,
    }
