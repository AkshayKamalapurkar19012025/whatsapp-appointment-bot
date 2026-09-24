"""
Staff notification center (OPD/HIMS master spec section 15) -- see
migrations/0044_notification_center.sql for why this is hospital-wide
and a separate table from the patient-facing mock SMS/WhatsApp outbox.

create_notification is called from the three call sites that actually
emit an event this phase covers -- a patient's check-in
(app/api/appointments.py's /visit and /confirm-and-checkin), a lab/
radiology result being recorded (app/api/orders.py), and a prescription
becoming fully dispensed (app/api/pharmacy.py) -- right alongside each
one's existing patient-facing send_mock_notification call, the same
"fire a notification right after the state change, in the same
transaction" placement, just to a different audience.
"""

_NOTIFICATION_COLUMNS = ("id", "hospital_id", "kind", "message", "appointment_id", "read_at", "created_at")


def _row_to_dict(row) -> dict:
    d = dict(zip(_NOTIFICATION_COLUMNS, row))
    d["read_at"] = d["read_at"].isoformat() if d["read_at"] else None
    d["created_at"] = d["created_at"].isoformat()
    return d


def create_notification(cur, *, hospital_id: int, kind: str, message: str, appointment_id: int | None = None) -> None:
    cur.execute(
        "INSERT INTO notifications (hospital_id, kind, message, appointment_id) VALUES (%s, %s, %s, %s)",
        (hospital_id, kind, message, appointment_id),
    )


def list_notifications_service(cur, *, hospital_id: int, limit: int = 30) -> dict:
    cur.execute(
        f"""
        SELECT {', '.join(_NOTIFICATION_COLUMNS)} FROM notifications
        WHERE hospital_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (hospital_id, limit),
    )
    items = [_row_to_dict(row) for row in cur.fetchall()]

    cur.execute(
        "SELECT COUNT(*) FROM notifications WHERE hospital_id = %s AND read_at IS NULL",
        (hospital_id,),
    )
    (unread_count,) = cur.fetchone()

    return {"items": items, "unread_count": unread_count}


def mark_notification_read_service(cur, notification_id: int, *, hospital_id: int) -> dict | None:
    cur.execute(
        f"""
        UPDATE notifications
        SET read_at = NOW()
        WHERE id = %s AND hospital_id = %s AND read_at IS NULL
        RETURNING {', '.join(_NOTIFICATION_COLUMNS)}
        """,
        (notification_id, hospital_id),
    )
    row = cur.fetchone()
    if row is not None:
        return _row_to_dict(row)

    # Idempotent on an already-read notification (a double-click, or a
    # second browser tab) -- return its current state rather than a
    # 404, same "clicking Mark Read twice isn't an error" shape as
    # break_glass review/staff account (de)activation elsewhere in this
    # codebase.
    cur.execute(
        f"SELECT {', '.join(_NOTIFICATION_COLUMNS)} FROM notifications WHERE id = %s AND hospital_id = %s",
        (notification_id, hospital_id),
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def mark_all_notifications_read_service(cur, *, hospital_id: int) -> int:
    cur.execute(
        "UPDATE notifications SET read_at = NOW() WHERE hospital_id = %s AND read_at IS NULL",
        (hospital_id,),
    )
    return cur.rowcount
