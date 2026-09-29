"""
A doctor's active recurring schedule, moved verbatim out of
app/api/doctor_schedule.py's GET /doctors/{id}/schedule so the AI agent's
doctor_schedule.get tool shares the route's query. Returns None for an
unknown/inactive doctor (the route maps that to its 404).
"""


def get_doctor_schedule_service(cur, doctor_id: int, *, hospital_id: int | None = None) -> list[dict] | None:
    # hospital_id is optional so the route's behavior is unchanged; the
    # agent tool always passes it.
    cur.execute(
        """
        SELECT id
        FROM doctors
        WHERE id = %s
          AND active = TRUE
          AND (%s::bigint IS NULL OR hospital_id = %s)
        """,
        (doctor_id, hospital_id, hospital_id),
    )

    if cur.fetchone() is None:
        return None

    cur.execute(
        """
        SELECT
            id,
            day_of_week,
            start_time,
            end_time,
            active,
            start_date,
            end_date,
            department_id
        FROM doctor_schedule
        WHERE doctor_id = %s
          AND active = TRUE
        ORDER BY day_of_week, start_time
        """,
        (doctor_id,),
    )

    return [
        {
            "id": row[0],
            "day_of_week": row[1],
            "start_time": row[2].strftime("%H:%M"),
            "end_time": row[3].strftime("%H:%M"),
            "active": row[4],
            "start_date": row[5].isoformat() if row[5] else None,
            "end_date": row[6].isoformat() if row[6] else None,
            "department_id": row[7],
        }
        for row in cur.fetchall()
    ]
