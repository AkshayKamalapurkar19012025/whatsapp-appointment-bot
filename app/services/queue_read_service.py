"""
Doctor queue read, moved verbatim out of app/api/doctors.py's
GET /doctors/{id}/queue so the AI agent's queue.get tool shares the
route's query and ordering rules (priority first, then token; held and
tokenless entries excluded) instead of re-implementing them. Returns None
for an unknown/inactive doctor; the route maps that to its 404.
See the route's docstring history for the business rules -- unchanged.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.utils.timezone import convert_to_timezone, validate_timezone


def get_doctor_queue_service(cur, doctor_id: int, *, hospital_id: int | None = None) -> dict | None:
    # hospital_id is optional so the existing route's behavior is
    # unchanged; the agent tool always passes it.
    cur.execute(
        "SELECT name, timezone FROM doctors WHERE id = %s AND active = TRUE"
        " AND (%s::bigint IS NULL OR hospital_id = %s)",
        (doctor_id, hospital_id, hospital_id),
    )
    doctor = cur.fetchone()

    if doctor is None:
        return None

    doctor_name, doctor_tz = doctor
    if not validate_timezone(doctor_tz):
        doctor_tz = "Asia/Kolkata"

    today = datetime.now(ZoneInfo(doctor_tz)).date()

    cur.execute(
        """
        SELECT a.id, a.status, a.token_number, a.visited_at, p.id, p.name,
               a.queue_held_at, a.is_priority
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        WHERE a.doctor_id = %s
          AND a.status IN ('CHECKED_IN', 'COMPLETED')
          AND a.token_number IS NOT NULL
          AND (a.visited_at AT TIME ZONE %s)::date = %s
        ORDER BY a.is_priority DESC, a.token_number
        """,
        (doctor_id, doctor_tz, today),
    )
    rows = cur.fetchall()

    def entry(row):
        return {
            "appointment_id": row[0],
            "token_number": row[2],
            # Converted to the doctor's own local time before returning
            # -- a raw TIMESTAMPTZ read-back always comes back UTC-
            # labeled from psycopg regardless of what offset it was
            # written with (the same bug class already fixed at every
            # other appointment-time display in this app; see e.g.
            # app/api/appointments.py's admin listing docstring).
            "visited_at": convert_to_timezone(row[3], doctor_tz).isoformat(),
            "patient_id": row[4],
            "patient_name": row[5],
            "is_priority": row[7],
        }

    checked_in_rows = [r for r in rows if r[1] == "CHECKED_IN"]
    completed = [entry(r) for r in rows if r[1] == "COMPLETED"]

    # Query's own ORDER BY already puts these in call order (priority
    # first, then token_number) -- held/not-held is the only split left
    # to do here.
    held = [entry(r) for r in checked_in_rows if r[6] is not None]
    active = [entry(r) for r in checked_in_rows if r[6] is None]
    now_serving = active.pop(0) if active else None

    return {
        "doctor_id": doctor_id,
        "doctor_name": doctor_name,
        "date": today.isoformat(),
        "now_serving": now_serving,
        "waiting": active,
        "held": held,
        "completed": completed,
    }
