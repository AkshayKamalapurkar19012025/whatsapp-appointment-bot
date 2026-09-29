"""
Permission resolution for a staff account, extracted from
app/api/staff_auth.py's require_permission so the AI agent layer resolves
permissions through the SAME query rather than a second authorization
path. require_permission (a FastAPI dependency, not callable outside a
request) now delegates here; its behavior is unchanged.

A permission is held via staff_roles -> role_permissions -> permissions
(migrations/0031) or an active, unexpired break_glass_grants row
(migrations/0032).
"""


def staff_has_permission(cur, staff_id: int, permission_name: str) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM staff_roles sr
            JOIN role_permissions rp ON rp.role_id = sr.role_id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE sr.staff_id = %(staff_id)s
              AND p.name = %(permission_name)s
        ) OR EXISTS (
            SELECT 1
            FROM break_glass_grants g
            WHERE g.staff_id = %(staff_id)s
              AND g.permission_name = %(permission_name)s
              AND g.expires_at > NOW()
        )
        """,
        {"staff_id": staff_id, "permission_name": permission_name},
    )
    return cur.fetchone()[0]


def list_staff_permissions(cur, staff_id: int) -> list[str]:
    """Every permission name this staff account currently holds (role
    grants plus live break-glass grants), sorted."""
    cur.execute(
        """
        SELECT p.name
        FROM staff_roles sr
        JOIN role_permissions rp ON rp.role_id = sr.role_id
        JOIN permissions p ON p.id = rp.permission_id
        WHERE sr.staff_id = %(staff_id)s
        UNION
        SELECT g.permission_name
        FROM break_glass_grants g
        WHERE g.staff_id = %(staff_id)s
          AND g.expires_at > NOW()
        ORDER BY 1
        """,
        {"staff_id": staff_id},
    )
    return [row[0] for row in cur.fetchall()]


def load_active_staff(cur, staff_id: int) -> dict | None:
    """{id, username, role, hospital_id} for an ACTIVE account, else None."""
    cur.execute(
        "SELECT id, username, role, hospital_id FROM staff WHERE id = %s AND active = TRUE",
        (staff_id,),
    )
    row = cur.fetchone()
    return {"id": row[0], "username": row[1], "role": row[2], "hospital_id": row[3]} if row else None


def staff_department_scope(cur, staff_id: int) -> int | None:
    """The single department this account is scoped to, or None if any of
    its roles is hospital-wide (department_id NULL) or it spans several."""
    cur.execute("SELECT department_id FROM staff_roles WHERE staff_id = %s", (staff_id,))
    scopes = {row[0] for row in cur.fetchall()}
    return next(iter(scopes)) if len(scopes) == 1 and None not in scopes else None
