"""
Waiting-Time Analytics (OPD/HIMS master spec audit "subsequent gaps"
list, screen 33): "Not built as its own screen (raw average shown
inline on Appointments page)." AppointmentsPanel.tsx's own
avgWaitMinutes is a live snapshot only -- today's currently-waiting
patients, recomputed client-side from getDoctorQueue every render, with
no history once they're seen. This is the historical counterpart: how
long patients who were already seen actually waited, trended over a
real date range and broken down by doctor.

Wait time is measured from appointments.visited_at (queue check-in,
migrations/0012 -- the same instant AppointmentsPanel's own snapshot
metric starts counting from) to consultations.started_at (when the
doctor actually opened this patient's consultation, migrations/0029) --
the same two timestamps that already exist for exactly this purpose, no
new column needed. Restricted to started_at >= visited_at as a sanity
guard: a doctor reopening a stale DRAFT consultation long after the
visit, or any row where the two clocks disagree, should not report a
negative or fabricated wait.

Bucketed like GET /dashboard/trends (app/api/dashboard.py): zero-filled
generate_series over the trailing `days` days, not a free
date_from/date_to range -- this is a trend view, not a searchable
history list like Billing/Payment History.
"""

from datetime import date, timedelta

DEFAULT_DAYS = 14

_BASE_FROM = """
    FROM appointments a
    JOIN encounters e ON e.id = a.encounter_id
    JOIN consultations c ON c.encounter_id = e.id
"""

_WAIT_EXPR = "EXTRACT(EPOCH FROM (c.started_at - a.visited_at)) / 60"


def get_waiting_time_analytics_service(
    cur,
    *,
    hospital_id: int,
    days: int = DEFAULT_DAYS,
    doctor_id: int | None = None,
) -> dict:
    window_start = date.today() - timedelta(days=days - 1)

    where_clauses = [
        "e.hospital_id = %s",
        "a.visited_at IS NOT NULL",
        "c.started_at IS NOT NULL",
        "c.started_at >= a.visited_at",
        "a.visited_at::date >= %s",
    ]
    params: list = [hospital_id, window_start]
    if doctor_id is not None:
        where_clauses.append("a.doctor_id = %s")
        params.append(doctor_id)
    where_sql = " AND ".join(where_clauses)

    cur.execute(
        f"""
        SELECT COUNT(*), COALESCE(AVG({_WAIT_EXPR}), 0)
        {_BASE_FROM}
        WHERE {where_sql}
        """,
        params,
    )
    overall_count, overall_avg = cur.fetchone()

    cur.execute(
        f"""
        SELECT d::date, COUNT(w.wait_minutes), COALESCE(AVG(w.wait_minutes), 0)
        FROM generate_series(%s::date, CURRENT_DATE, INTERVAL '1 day') AS d
        LEFT JOIN (
            SELECT a.visited_at::date AS visit_date, {_WAIT_EXPR} AS wait_minutes
            {_BASE_FROM}
            WHERE {where_sql}
        ) w ON w.visit_date = d::date
        GROUP BY d
        ORDER BY d
        """,
        [window_start, *params],
    )
    by_day = cur.fetchall()

    cur.execute(
        f"""
        SELECT doc.id, doc.name, COUNT(*), COALESCE(AVG({_WAIT_EXPR}), 0)
        {_BASE_FROM}
        JOIN doctors doc ON doc.id = a.doctor_id
        WHERE {where_sql}
        GROUP BY doc.id, doc.name
        ORDER BY AVG({_WAIT_EXPR}) DESC
        """,
        params,
    )
    by_doctor = cur.fetchall()

    return {
        "window_days": days,
        "overall": {
            "count": overall_count,
            "avg_wait_minutes": round(float(overall_avg), 1),
        },
        "by_day": [
            {"date": day.isoformat(), "count": count, "avg_wait_minutes": round(float(avg), 1)}
            for day, count, avg in by_day
        ],
        "by_doctor": [
            {
                "doctor_id": doctor_row_id,
                "doctor_name": doctor_name,
                "count": count,
                "avg_wait_minutes": round(float(avg), 1),
            }
            for doctor_row_id, doctor_name, count, avg in by_doctor
        ],
    }
