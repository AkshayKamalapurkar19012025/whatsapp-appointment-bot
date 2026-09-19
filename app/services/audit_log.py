"""
Audit log (P1.b): a single append-only record of who did what, to which
resource, and when -- see migrations/0031_audit_log.sql. Called from
every require_permission(...)-gated write endpoint (app/api/staff_auth.py
and the doctor/department/appointment-type/etc. CRUD routers P1.a wired
RBAC into), plus break-glass grant/review.

Deliberately a single INSERT with no error handling of its own: it runs
inside the caller's existing transaction, on the same cursor, so a
failure here aborts the whole request exactly like any other write in
that handler would -- there's no scenario where "the update succeeded
but logging it didn't" is an acceptable outcome for an audit trail.
"""

import json


def record_audit_log(
    cur,
    *,
    hospital_id: int,
    staff_id: int | None,
    action: str,
    resource_type: str,
    resource_id: int | None = None,
    details: dict | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO audit_log (hospital_id, staff_id, action, resource_type, resource_id, details)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            hospital_id,
            staff_id,
            action,
            resource_type,
            resource_id,
            # default=str: details is assembled ad hoc at each of ~20
            # call sites from whatever a query just returned -- e.g.
            # consultation_fee comes back from psycopg as Decimal, which
            # json.dumps otherwise rejects outright. Falling back to
            # str() keeps this generic logging helper from being able to
            # crash the request it's supposed to be auditing, for any
            # DB-native type a future call site passes through.
            json.dumps(details, default=str) if details is not None else None,
        ),
    )
