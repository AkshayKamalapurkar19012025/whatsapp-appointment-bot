"""
Staff account management (WEB P5): create, list, and activate/deactivate
staff accounts.

This module has no role-awareness of its own -- authorization (that only
an ADMIN may call these) is enforced at the API layer
(app/api/staff_auth.py's require_role dependency), the same
transport-layer-owns-authorization pattern used throughout
app/services/*. There is no self-service staff signup (unlike patients,
who register themselves via OTP) -- an account only ever comes into
existence via create_staff_account, called by an existing ADMIN, or via
scripts/create_staff_account.py for the very first bootstrap account.
"""

from app.services.exceptions import StaffNotFound, UsernameAlreadyExists
from app.services.staff_auth import hash_password


def create_staff_account(cur, username: str, password: str, role: str) -> dict:
    """Raises UsernameAlreadyExists if the (case-insensitively
    normalized) username is already taken."""
    username_normalized = username.strip().lower()
    password_hash = hash_password(password)

    cur.execute(
        """
        INSERT INTO staff (username, password_hash, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (username) DO NOTHING
        RETURNING id, username, role, active
        """,
        (username_normalized, password_hash, role),
    )
    row = cur.fetchone()

    if row is None:
        raise UsernameAlreadyExists()

    return {"id": row[0], "username": row[1], "role": row[2], "active": row[3]}


def list_staff_accounts(cur) -> list[dict]:
    """Never returns password_hash -- only what an admin dashboard needs
    to display and act on."""
    cur.execute(
        "SELECT id, username, role, active FROM staff ORDER BY username"
    )
    return [
        {"id": r[0], "username": r[1], "role": r[2], "active": r[3]}
        for r in cur.fetchall()
    ]


def set_staff_active(cur, staff_id: int, active: bool) -> dict:
    """Raises StaffNotFound for an unknown id. Deactivating a currently-
    logged-in account also revokes its existing sessions immediately
    (rather than relying solely on get_staff_by_session_token's live
    `active` check to catch it on the account's next request) -- belt
    and suspenders, and means a direct query of staff_sessions never
    shows a stale-but-technically-unexpired row for a disabled
    account."""
    cur.execute(
        """
        UPDATE staff SET active = %s WHERE id = %s
        RETURNING id, username, role, active
        """,
        (active, staff_id),
    )
    row = cur.fetchone()

    if row is None:
        raise StaffNotFound()

    if not active:
        cur.execute(
            """
            UPDATE staff_sessions
            SET revoked_at = NOW()
            WHERE staff_id = %s
              AND revoked_at IS NULL
            """,
            (staff_id,),
        )

    return {"id": row[0], "username": row[1], "role": row[2], "active": row[3]}
