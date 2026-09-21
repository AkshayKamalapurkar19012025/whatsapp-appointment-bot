"""
The order spine (OPD/HIMS master spec Phase 6): lab/radiology/procedure/
service orders and external referrals, all one `orders` table
(migrations/0030_orders.sql) keyed to the encounter behind an
appointment -- see that migration's header for why this is one table,
not one per order type.

Creation is gated on the appointment being CHECKED_IN, the same
CHECKED_IN-not-encounters.status discipline app/services/
clinical_services.py's module docstring explains in full -- an order is
part of the same live-visit "PLAN" a consultation is written during.
Cancellation is deliberately NOT gated on appointment status: staff need
to be able to cancel an order that turns out to be wrong even after the
visit itself has closed out (e.g. before it reaches a lab for
collection), so the only thing that blocks a cancel is the order's own
status already being COMPLETED or CANCELLED.
"""

from app.services.clinical_services import (
    get_appointment_status_and_doctor,
    get_encounter_id_for_appointment,
)
from app.services.exceptions import (
    EncounterClosed,
    ExternalReferralDestinationRequired,
    OrderNotFound,
    OrderNotCancellable,
)

_ORDER_COLUMNS = (
    "id", "encounter_id", "order_type", "description", "clinical_indication",
    "priority", "status", "external_destination", "result_text",
    "ordering_doctor_id", "created_by", "cancelled_by", "cancel_reason",
    "ordered_at", "completed_at", "cancelled_at",
)


def _order_row_to_dict(row) -> dict:
    d = dict(zip(_ORDER_COLUMNS, row))
    d["ordered_at"] = d["ordered_at"].isoformat()
    d["completed_at"] = d["completed_at"].isoformat() if d["completed_at"] else None
    d["cancelled_at"] = d["cancelled_at"].isoformat() if d["cancelled_at"] else None
    return d


def create_order_service(
    cur,
    appointment_id: int,
    *,
    staff_id: int,
    order_type: str,
    description: str,
    clinical_indication: str | None = None,
    priority: str = "ROUTINE",
    external_destination: str | None = None,
):
    appointment = get_appointment_status_and_doctor(cur, appointment_id)
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    if appointment["status"] != "CHECKED_IN":
        raise EncounterClosed()

    if order_type == "EXTERNAL_REFERRAL" and not (external_destination and external_destination.strip()):
        raise ExternalReferralDestinationRequired()

    cur.execute(
        f"""
        INSERT INTO orders (
            encounter_id, order_type, description, clinical_indication,
            priority, external_destination, ordering_doctor_id, created_by
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {", ".join(_ORDER_COLUMNS)}
        """,
        (
            encounter_id, order_type, description, clinical_indication,
            priority, external_destination, appointment["doctor_id"], staff_id,
        ),
    )

    return _order_row_to_dict(cur.fetchone())


def list_orders_service(cur, appointment_id: int):
    """Every order for this appointment's encounter, newest first.
    Never gated on status -- viewing order history is always allowed,
    including after the visit closes."""
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        f"""
        SELECT {", ".join(_ORDER_COLUMNS)}
        FROM orders
        WHERE encounter_id = %s
        ORDER BY ordered_at DESC
        """,
        (encounter_id,),
    )

    return [_order_row_to_dict(row) for row in cur.fetchall()]


def cancel_order_service(cur, appointment_id: int, order_id: int, *, staff_id: int, reason: str):
    """Cancels one order -- requires a reason (master spec section 90:
    a dangerous/destructive action needs a stated reason, not a silent
    click), same pattern as set_priority_service's required reason in
    app/services/appointment_services.py. Scoped to this appointment's
    encounter so an order_id belonging to a different patient's visit
    can't be cancelled through this appointment's URL."""
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        "SELECT status FROM orders WHERE id = %s AND encounter_id = %s FOR UPDATE",
        (order_id, encounter_id),
    )
    row = cur.fetchone()
    if row is None:
        raise OrderNotFound()

    if row[0] in ("COMPLETED", "CANCELLED"):
        raise OrderNotCancellable()

    cur.execute(
        f"""
        UPDATE orders
        SET status = 'CANCELLED',
            cancelled_by = %s,
            cancel_reason = %s,
            cancelled_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
        RETURNING {", ".join(_ORDER_COLUMNS)}
        """,
        (staff_id, reason, order_id),
    )

    return _order_row_to_dict(cur.fetchone())
