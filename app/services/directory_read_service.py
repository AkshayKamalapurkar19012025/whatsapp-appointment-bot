"""Small read-only directory lookups for the AI agent's department.get
tool (no by-id department read existed; GET /departments only lists)."""


def get_department_service(cur, department_id: int, *, hospital_id: int) -> dict | None:
    cur.execute(
        """
        SELECT id, name, active
        FROM departments
        WHERE id = %s AND hospital_id = %s
        """,
        (department_id, hospital_id),
    )
    row = cur.fetchone()
    return {"id": row[0], "name": row[1], "active": row[2]} if row else None
