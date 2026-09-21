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

Recording a result (OPD/HIMS master spec Phase 7, migrations/
0031_order_results.sql) is likewise NOT gated on appointment status --
a third, independent point on this same spectrum. A lab/radiology
result routinely comes back hours or days after the patient has gone
home (the master spec's own Patient 360 timeline example shows a result
released well after check-in), so requiring CHECKED_IN here would make
recording a real, everyday result impossible. Across all three actions
the actual rule is the same one, just applied to what each action
protects: gate on CHECKED_IN when the visit itself is what must still be
open (documentation, placing a new order); don't gate when the action
is correcting or fulfilling something that can legitimately happen after
the visit ends (cancelling a mistaken order, attaching a result).
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
    OrderNotResultable,
)

_ORDER_COLUMNS = (
    "id", "encounter_id", "order_type", "description", "clinical_indication",
    "priority", "status", "external_destination", "result_text",
    "ordering_doctor_id", "created_by", "cancelled_by", "cancel_reason",
    "ordered_at", "completed_at", "cancelled_at",
)

_RESULT_COLUMNS = (
    "id", "order_id", "parameter", "result_value", "unit", "reference_range",
    "is_abnormal", "is_critical", "sequence", "recorded_by", "recorded_at",
)


def _order_row_to_dict(row) -> dict:
    d = dict(zip(_ORDER_COLUMNS, row))
    d["ordered_at"] = d["ordered_at"].isoformat()
    d["completed_at"] = d["completed_at"].isoformat() if d["completed_at"] else None
    d["cancelled_at"] = d["cancelled_at"].isoformat() if d["cancelled_at"] else None
    return d


def _result_row_to_dict(row) -> dict:
    d = dict(zip(_RESULT_COLUMNS, row))
    d["recorded_at"] = d["recorded_at"].isoformat()
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

    order = _order_row_to_dict(cur.fetchone())
    # Always present, always empty for a just-created order -- keeps
    # every order dict this module returns (create/cancel/list/result)
    # the same shape, so the frontend never has to special-case a
    # missing `results` key after an optimistic local update.
    order["results"] = []
    return order


def list_orders_service(cur, appointment_id: int):
    """Every order for this appointment's encounter, newest first, each
    with its `results` embedded (empty list if none recorded yet) --
    one round trip is enough for a consultation screen to show both the
    order and its result inline (master spec section 34: a doctor sees
    results "from inside consultation", not by navigating elsewhere).
    Never gated on status -- viewing order/result history is always
    allowed, including after the visit closes."""
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
    orders = [_order_row_to_dict(row) for row in cur.fetchall()]

    if not orders:
        return orders

    order_ids = [o["id"] for o in orders]
    cur.execute(
        f"""
        SELECT {", ".join(_RESULT_COLUMNS)}
        FROM order_results
        WHERE order_id = ANY(%s)
        ORDER BY order_id, sequence
        """,
        (order_ids,),
    )

    results_by_order_id: dict[int, list[dict]] = {}
    for row in cur.fetchall():
        result = _result_row_to_dict(row)
        results_by_order_id.setdefault(result["order_id"], []).append(result)

    for order in orders:
        order["results"] = results_by_order_id.get(order["id"], [])

    return orders


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

    # Always empty here too -- cancellation is only reachable from
    # ORDERED/IN_PROGRESS (OrderNotCancellable above blocks COMPLETED),
    # and results are only ever attached at the moment an order becomes
    # COMPLETED (record_order_result_service), so a cancelled order can
    # never have had results to preserve. Same shape-consistency
    # reasoning as create_order_service's own `results = []`.
    order = _order_row_to_dict(cur.fetchone())
    order["results"] = []
    return order


def record_order_result_service(cur, appointment_id: int, order_id: int, *, staff_id: int, items: list[dict]):
    """Records one batch of result items against an order and marks it
    COMPLETED, in the same transaction -- a lab panel/radiology report
    arrives as one complete result, not built up field-by-field over
    several calls (no partial-result state exists in this schema; see
    migrations/0031's header on why there's no amendment workflow
    either). `items` are plain dicts with `parameter`/`result_value`
    required and `unit`/`reference_range`/`is_abnormal`/`is_critical`
    optional -- already validated non-empty and shaped by the API
    layer's Pydantic model (app/api/orders.py's OrderResultCreate), not
    re-validated here.

    Not gated on appointment status -- see this module's own docstring
    for why. Allowed from ORDERED or IN_PROGRESS; refused (
    OrderNotResultable) once the order is already COMPLETED or
    CANCELLED."""
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        "SELECT status FROM orders WHERE id = %s AND encounter_id = %s FOR UPDATE",
        (order_id, encounter_id),
    )
    row = cur.fetchone()
    if row is None:
        raise OrderNotFound()

    if row[0] in ("COMPLETED", "CANCELLED"):
        raise OrderNotResultable()

    for sequence, item in enumerate(items):
        cur.execute(
            """
            INSERT INTO order_results (
                order_id, parameter, result_value, unit, reference_range,
                is_abnormal, is_critical, sequence, recorded_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order_id, item["parameter"], item["result_value"],
                item.get("unit"), item.get("reference_range"),
                item.get("is_abnormal", False), item.get("is_critical", False),
                sequence, staff_id,
            ),
        )

    cur.execute(
        f"""
        UPDATE orders
        SET status = 'COMPLETED',
            completed_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
        RETURNING {", ".join(_ORDER_COLUMNS)}
        """,
        (order_id,),
    )

    order = _order_row_to_dict(cur.fetchone())
    order["results"] = list_order_results_service(cur, order_id)
    return order


def list_order_results_service(cur, order_id: int):
    cur.execute(
        f"""
        SELECT {", ".join(_RESULT_COLUMNS)}
        FROM order_results
        WHERE order_id = %s
        ORDER BY sequence
        """,
        (order_id,),
    )
    return [_result_row_to_dict(row) for row in cur.fetchall()]
