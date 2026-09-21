"""
Admin dashboard: summary stats + trend charts backing the Dashboard
landing page (frontend/src/admin/DashboardPanel.tsx). Both endpoints are
staff-readable (get_current_staff, not require_permission(...)): viewing
these counts is no more sensitive than viewing the appointments list,
which any STAFF session can already do via GET /appointments.

Appointments move through a real lifecycle (migrations/0011_appointment_
lifecycle_statuses.sql, extended by migrations/0015): PENDING ->
CONFIRMED -> CHECKED_IN -> COMPLETED, with PENDING -> REJECTED or
PENDING/CONFIRMED -> CANCELLED, or CONFIRMED -> NO_SHOW (manual
front-desk action, migrations/0015) as exits. /stats reports a count
for every one of the original six statuses plus the two time-based
cuts (today's / upcoming) already used before this lifecycle existed --
NO_SHOW is not yet broken out as its own stat here (deliberately out of
scope for the migration that introduced it; add a
no_show_appointments count the same way as the others above if/when
front-desk visibility into no-show counts is wanted).
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

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
            # "Today"/"upcoming" are evaluated in each doctor's own local
            # timezone (joined in from doctors.timezone) rather than
            # server/UTC time -- the same per-doctor-local-day convention
            # GET /appointments already uses for its date_from/date_to
            # filters, just computed in SQL here since these are
            # aggregate counts rather than per-row display values. Both
            # only count PENDING/CONFIRMED -- an appointment that's
            # already Visited, Completed, Cancelled, or Rejected isn't
            # "today's" or "upcoming" in the sense a front-desk staffer
            # means by those words.
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE a.status IN ('PENDING', 'CONFIRMED')
                        AND (a.start_at AT TIME ZONE d.timezone)::date
                            = (NOW() AT TIME ZONE d.timezone)::date
                    ) AS today_appointments,
                    COUNT(*) FILTER (
                        WHERE a.status IN ('PENDING', 'CONFIRMED') AND a.start_at > NOW()
                    ) AS upcoming_appointments,
                    COUNT(*) AS total_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'PENDING') AS pending_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'CONFIRMED') AS confirmed_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'REJECTED') AS rejected_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'CANCELLED') AS cancelled_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'CHECKED_IN') AS visited_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'COMPLETED') AS completed_appointments
                FROM appointments a
                JOIN doctors d ON d.id = a.doctor_id
                """
            )
            (
                today,
                upcoming,
                total,
                pending,
                confirmed,
                rejected,
                cancelled,
                visited,
                completed,
            ) = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM doctors WHERE active = TRUE")
            (total_doctors,) = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM patients")
            (total_patients,) = cur.fetchone()

    return {
        "today_appointments": today,
        "upcoming_appointments": upcoming,
        "total_appointments": total,
        "pending_appointments": pending,
        "confirmed_appointments": confirmed,
        "rejected_appointments": rejected,
        "cancelled_appointments": cancelled,
        "visited_appointments": visited,
        "completed_appointments": completed,
        "total_doctors": total_doctors,
        "total_patients": total_patients,
    }


@router.get("/trends")
def get_dashboard_trends(
    days: int = Query(default=14, ge=7, le=90),
    staff: dict = Depends(get_current_staff),
):
    """
    Two daily-count series for the Dashboard's "Appointment Trends" and
    "Patient Registration" charts: appointments created and patients
    registered per calendar day over the trailing `days` days (default
    14), oldest first, zero-filled for days with no activity.

    Both series bucket by created_at's UTC calendar date. Unlike /stats'
    today/upcoming counts, this deliberately does NOT convert to each
    doctor's local timezone -- a trend chart's day boundary being off by
    a few hours for some doctors doesn't change the shape of the line,
    and a single shared UTC axis is what lets the two series (patients
    have no per-row timezone at all) share one set of x-axis labels.
    """
    window_start = date.today() - timedelta(days=days - 1)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d::date, COUNT(a.id)
                FROM generate_series(%s::date, CURRENT_DATE, INTERVAL '1 day') AS d
                LEFT JOIN appointments a ON a.created_at::date = d::date
                GROUP BY d
                ORDER BY d
                """,
                (window_start,),
            )
            appointment_counts = cur.fetchall()

            cur.execute(
                """
                SELECT d::date, COUNT(p.id)
                FROM generate_series(%s::date, CURRENT_DATE, INTERVAL '1 day') AS d
                LEFT JOIN patients p ON p.created_at::date = d::date
                GROUP BY d
                ORDER BY d
                """,
                (window_start,),
            )
            patient_counts = cur.fetchall()

    return {
        "appointments": [{"date": day.isoformat(), "count": count} for day, count in appointment_counts],
        "patients": [{"date": day.isoformat(), "count": count} for day, count in patient_counts],
    }


@router.get("/billing")
def get_billing_report(
    days: int = Query(default=14, ge=1, le=90),
    staff: dict = Depends(get_current_staff),
):
    """
    OPD billing reconciliation view backing a future front-desk/admin
    "Billing" panel (no frontend for this yet -- see DashboardPanel.tsx
    for where /stats and /trends are consumed; this endpoint exists so
    that panel has a real API to build against). Staff-readable, same
    posture as /stats and /trends above: GET /appointments already
    returns payment_status/payment_method/payment_amount per row to any
    STAFF session, so an aggregate over the same columns isn't more
    sensitive.

    Four independent pieces, not one combined query -- each answers a
    different front-desk question and has a different natural time
    scope:
      * collections: money actually collected (PAID), bucketed by
        payment_method and by doctor, over the trailing `days` days
        (payment_recorded_at-scoped -- when it was collected, not when
        the appointment was scheduled).
      * outstanding: what's currently owed right now -- every CHECKED_IN
        appointment still UNPAID or FAILED. Deliberately NOT time-scoped
        by `days`: "who owes money today" means everyone outstanding,
        not just the ones from this window.
      * waivers: count + reasons for the trailing `days` days. No dollar
        total -- waive_consultation_fee_service and settle_free_visit_
        service both record payment_amount = 0 for a waived visit (see
        their docstrings), so there is no real "amount waived" number to
        report; fabricating one from doctor_appointment_types.
        consultation_fee's *current* price would misrepresent what was
        actually waived at the time.
      * refunds: count + total refund_amount for the trailing `days`
        days (refund_amount is actually recorded, unlike waivers, so a
        real total is reportable here).
    """
    window_start = date.today() - timedelta(days=days - 1)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payment_method, COUNT(*), COALESCE(SUM(payment_amount), 0)
                FROM appointments
                WHERE payment_status = 'PAID'
                  AND payment_recorded_at::date >= %s
                GROUP BY payment_method
                ORDER BY payment_method
                """,
                (window_start,),
            )
            collections_by_method = cur.fetchall()

            cur.execute(
                """
                SELECT d.id, d.name, COUNT(*), COALESCE(SUM(a.payment_amount), 0)
                FROM appointments a
                JOIN doctors d ON d.id = a.doctor_id
                WHERE a.payment_status = 'PAID'
                  AND a.payment_recorded_at::date >= %s
                GROUP BY d.id, d.name
                ORDER BY d.name
                """,
                (window_start,),
            )
            collections_by_doctor = cur.fetchall()

            cur.execute(
                """
                SELECT a.id, p.name, d.name, a.payment_status, a.visited_at
                FROM appointments a
                JOIN patients p ON p.id = a.patient_id
                JOIN doctors d ON d.id = a.doctor_id
                WHERE a.status = 'CHECKED_IN'
                  AND a.payment_status IN ('UNPAID', 'FAILED')
                ORDER BY a.visited_at
                """
            )
            outstanding_rows = cur.fetchall()

            cur.execute(
                """
                SELECT a.id, p.name, d.name, a.waive_reason, a.payment_recorded_at
                FROM appointments a
                JOIN patients p ON p.id = a.patient_id
                JOIN doctors d ON d.id = a.doctor_id
                WHERE a.payment_status = 'WAIVED'
                  AND a.payment_recorded_at::date >= %s
                ORDER BY a.payment_recorded_at DESC
                """,
                (window_start,),
            )
            waiver_rows = cur.fetchall()

            cur.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(refund_amount), 0)
                FROM appointments
                WHERE payment_status = 'REFUNDED'
                  AND refunded_at::date >= %s
                """,
                (window_start,),
            )
            refund_count, refund_total = cur.fetchone()

            cur.execute(
                """
                SELECT a.id, p.name, d.name, a.refund_amount, a.refund_reason, a.refunded_at
                FROM appointments a
                JOIN patients p ON p.id = a.patient_id
                JOIN doctors d ON d.id = a.doctor_id
                WHERE a.payment_status = 'REFUNDED'
                  AND a.refunded_at::date >= %s
                ORDER BY a.refunded_at DESC
                """,
                (window_start,),
            )
            refund_rows = cur.fetchall()

    return {
        "window_days": days,
        "collections_by_method": [
            {"method": method, "count": count, "amount": amount}
            for method, count, amount in collections_by_method
        ],
        "collections_by_doctor": [
            {"doctor_id": doctor_id, "doctor_name": doctor_name, "count": count, "amount": amount}
            for doctor_id, doctor_name, count, amount in collections_by_doctor
        ],
        "total_collected": sum(amount for _, _, amount in collections_by_method),
        "outstanding_unpaid": [
            {
                "appointment_id": row[0],
                "patient_name": row[1],
                "doctor_name": row[2],
                "payment_status": row[3],
                "visited_at": row[4].isoformat() if row[4] else None,
            }
            for row in outstanding_rows
        ],
        "waivers": {
            "count": len(waiver_rows),
            "records": [
                {
                    "appointment_id": row[0],
                    "patient_name": row[1],
                    "doctor_name": row[2],
                    "reason": row[3],
                    "waived_at": row[4].isoformat() if row[4] else None,
                }
                for row in waiver_rows
            ],
        },
        "refunds": {
            "count": refund_count,
            "total_refunded": refund_total,
            "records": [
                {
                    "appointment_id": row[0],
                    "patient_name": row[1],
                    "doctor_name": row[2],
                    "refund_amount": row[3],
                    "refund_reason": row[4],
                    "refunded_at": row[5].isoformat() if row[5] else None,
                }
                for row in refund_rows
            ],
        },
    }
