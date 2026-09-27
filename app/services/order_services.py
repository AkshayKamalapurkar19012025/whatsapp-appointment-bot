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

Phase 7 resumed (migrations/0054_diagnostic_workflow.sql): LAB/RADIOLOGY
orders now go through a real sample-collection -> processing ->
result-entry -> verification -> release lifecycle
(record_sample_collection_service/reject_sample_service/
mark_order_in_progress_service/record_order_result_service/
verify_order_result_service/release_order_result_service) instead of
completing the instant a result is recorded. PROCEDURE/SERVICE/
EXTERNAL_REFERRAL orders are completely unaffected -- see
_is_diagnostic() and every function below that branches on it.
"""

from app.services.clinical_services import (
    get_appointment_status_and_doctor,
    get_encounter_id_for_appointment,
)
from app.services.exceptions import (
    DiagnosticActionNotSupportedForOrderType,
    EncounterClosed,
    ExternalReferralDestinationRequired,
    ModuleUnavailable,
    OrderNotCollectible,
    OrderNotFound,
    OrderNotCancellable,
    OrderNotReleasable,
    OrderNotResultable,
    OrderNotStartable,
    OrderNotVerifiable,
    SameStaffCannotVerifyOwnResult,
    SampleAlreadyRejected,
    SampleNotFound,
)
from app.services.module_services import is_module_available

_ORDER_COLUMNS = (
    "id", "encounter_id", "order_type", "description", "clinical_indication",
    "priority", "status", "external_destination", "result_text",
    "ordering_doctor_id", "created_by", "cancelled_by", "cancel_reason",
    "ordered_at", "completed_at", "cancelled_at",
    "result_entered_by", "result_entered_at", "verified_by", "verified_at",
    "released_by", "released_at",
)

_RESULT_COLUMNS = (
    "id", "order_id", "parameter", "result_value", "unit", "reference_range",
    "is_abnormal", "is_critical", "sequence", "recorded_by", "recorded_at",
)

_SAMPLE_COLUMNS = (
    "id", "order_id", "sample_code", "sample_type", "status", "notes",
    "collected_by", "collected_at", "rejected_by", "rejected_reason", "rejected_at",
)

# LAB/RADIOLOGY are the only two order_types with a sample/processing/
# verification/release lifecycle -- PROCEDURE/SERVICE/EXTERNAL_REFERRAL
# keep the original one-step ORDERED -> COMPLETED/CANCELLED behavior
# unchanged (migrations/0054's header explains why: no safe, evidence-
# backed case for inventing collection/verification steps for a
# procedure or an external referral).
_DIAGNOSTIC_ORDER_TYPES = ("LAB", "RADIOLOGY")


def _is_diagnostic(order_type: str) -> bool:
    return order_type in _DIAGNOSTIC_ORDER_TYPES


def _order_row_to_dict(row) -> dict:
    d = dict(zip(_ORDER_COLUMNS, row))
    d["ordered_at"] = d["ordered_at"].isoformat()
    d["completed_at"] = d["completed_at"].isoformat() if d["completed_at"] else None
    d["cancelled_at"] = d["cancelled_at"].isoformat() if d["cancelled_at"] else None
    d["result_entered_at"] = d["result_entered_at"].isoformat() if d["result_entered_at"] else None
    d["verified_at"] = d["verified_at"].isoformat() if d["verified_at"] else None
    d["released_at"] = d["released_at"].isoformat() if d["released_at"] else None
    return d


def _result_row_to_dict(row) -> dict:
    d = dict(zip(_RESULT_COLUMNS, row))
    d["recorded_at"] = d["recorded_at"].isoformat()
    return d


def _sample_row_to_dict(row) -> dict:
    d = dict(zip(_SAMPLE_COLUMNS, row))
    d["collected_at"] = d["collected_at"].isoformat()
    d["rejected_at"] = d["rejected_at"].isoformat() if d["rejected_at"] else None
    return d


def _lock_order(cur, appointment_id: int, order_id: int):
    """Shared row-lock helper for every diagnostic-lifecycle action
    below -- same FOR UPDATE pattern cancel_order_service/
    record_order_result_service already used, pulled out once these
    grew to six call sites needing the identical
    order_type/status-under-lock read."""
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)
    cur.execute(
        "SELECT order_type, status, result_entered_by FROM orders WHERE id = %s AND encounter_id = %s FOR UPDATE",
        (order_id, encounter_id),
    )
    row = cur.fetchone()
    if row is None:
        raise OrderNotFound()
    return row  # (order_type, status, result_entered_by)


def create_order_service(
    cur,
    appointment_id: int,
    *,
    staff_id: int,
    hospital_id: int,
    order_type: str,
    description: str,
    clinical_indication: str | None = None,
    priority: str = "ROUTINE",
    external_destination: str | None = None,
):
    # Master spec section 68's own worked example: Lab/Radiology
    # disabled routes a doctor through External Referral instead --
    # EXTERNAL_REFERRAL (and PROCEDURE/SERVICE) stay unaffected, only
    # LAB/RADIOLOGY themselves are gated.
    if order_type in ("LAB", "RADIOLOGY") and not is_module_available(cur, hospital_id, "LAB_RADIOLOGY"):
        raise ModuleUnavailable("LAB_RADIOLOGY")

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
    # missing `results`/`samples` key after an optimistic local update.
    order["results"] = []
    order["samples"] = []
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

    cur.execute(
        f"""
        SELECT {", ".join(_SAMPLE_COLUMNS)}
        FROM lab_samples
        WHERE order_id = ANY(%s)
        ORDER BY order_id, collected_at DESC
        """,
        (order_ids,),
    )

    samples_by_order_id: dict[int, list[dict]] = {}
    for row in cur.fetchall():
        sample = _sample_row_to_dict(row)
        samples_by_order_id.setdefault(sample["order_id"], []).append(sample)

    for order in orders:
        order["results"] = results_by_order_id.get(order["id"], [])
        order["samples"] = samples_by_order_id.get(order["id"], [])

    return orders


_WORKLIST_COLUMNS = (
    "id", "encounter_id", "appointment_id", "order_type", "description",
    "clinical_indication", "priority", "status", "external_destination",
    "ordering_doctor_id", "doctor_name", "ordered_at",
    "patient_id", "patient_name", "uhid",
    "latest_sample_id", "latest_sample_code", "latest_sample_type", "latest_sample_status",
)


def list_worklist_orders_service(cur, hospital_id: int, *, order_type: str | None = None, status: str | None = None):
    """
    Cross-patient worklist for lab/radiology techs (OPD/HIMS master
    spec Phase 6/7's originally-anticipated "future worklist" --
    orders_open_by_type_idx, migrations/0030_orders.sql, was added for
    exactly this and unused until now). Unlike list_orders_service
    (one appointment's own encounter), this spans every patient in the
    hospital -- the whole point of a shared worklist a tech works down,
    rather than hunting through each patient's own consultation.

    Defaults to open work (everything short of COMPLETED/CANCELLED --
    widened by migrations/0054 from the original ORDERED/IN_PROGRESS
    pair to also cover COLLECTED/RESULT_ENTERED/VERIFIED, since a
    result awaiting verification or a verified-but-unreleased report is
    still work the worklist needs to surface) when status isn't given;
    pass a specific status to review completed/cancelled orders
    instead. order_type narrows to one type (LAB or RADIOLOGY from the
    worklist screen, though any of the 5 order types works here).

    Relies on one appointment existing per encounter (true for every
    encounter this app creates via the check-in flow) to resolve each
    order back to the appointment_id the existing per-appointment
    result-entry endpoint needs -- nothing enforces that as a DB-level
    uniqueness constraint, so a JOIN here (rather than a scalar
    subquery) would silently duplicate a row if that ever stopped
    holding; worth revisiting if encounters ever gain multiple
    appointments.
    """
    where = ["e.hospital_id = %s"]
    params: list = [hospital_id]

    if status:
        where.append("o.status = %s")
        params.append(status)
    else:
        where.append("o.status NOT IN ('COMPLETED', 'CANCELLED')")

    if order_type:
        where.append("o.order_type = %s")
        params.append(order_type)

    cur.execute(
        f"""
        SELECT o.id, o.encounter_id, a.id, o.order_type, o.description,
               o.clinical_indication, o.priority, o.status, o.external_destination,
               o.ordering_doctor_id, d.name, o.ordered_at,
               p.id, p.name, p.uhid,
               ls.id, ls.sample_code, ls.sample_type, ls.status
        FROM orders o
        JOIN encounters e ON e.id = o.encounter_id
        JOIN appointments a ON a.encounter_id = e.id
        JOIN patients p ON p.id = e.patient_id
        JOIN doctors d ON d.id = o.ordering_doctor_id
        -- Latest collection attempt only (LATERAL, not a plain JOIN --
        -- an order can have several lab_samples rows across a reject/
        -- recollect history, migrations/0054), so the worklist shows
        -- the current sample, not a duplicated row per past attempt.
        LEFT JOIN LATERAL (
            SELECT id, sample_code, sample_type, status
            FROM lab_samples
            WHERE order_id = o.id
            ORDER BY collected_at DESC
            LIMIT 1
        ) ls ON TRUE
        WHERE {" AND ".join(where)}
        ORDER BY
            CASE o.priority WHEN 'STAT' THEN 0 WHEN 'URGENT' THEN 1 ELSE 2 END,
            o.ordered_at
        """,
        params,
    )
    rows = [dict(zip(_WORKLIST_COLUMNS, row)) for row in cur.fetchall()]
    for row in rows:
        row["ordered_at"] = row["ordered_at"].isoformat()
    return rows


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
    order["results"] = list_order_results_service(cur, order_id)
    order["samples"] = list_order_samples_service(cur, order_id)
    return order


def record_order_result_service(cur, appointment_id: int, order_id: int, *, staff_id: int, items: list[dict]):
    """Records one batch of result items against an order. `items` are
    plain dicts with `parameter`/`result_value` required and
    `unit`/`reference_range`/`is_abnormal`/`is_critical` optional --
    already validated non-empty and shaped by the API layer's Pydantic
    model (app/api/orders.py's OrderResultCreate), not re-validated
    here.

    Not gated on appointment status -- see this module's own docstring
    for why.

    PROCEDURE/SERVICE/EXTERNAL_REFERRAL (unchanged since migrations/
    0031): marks the order COMPLETED in the same transaction -- a
    result arrives as one complete, final batch, no separate
    verification step exists for these types. Allowed from ORDERED or
    IN_PROGRESS; refused (OrderNotResultable) once already COMPLETED or
    CANCELLED.

    LAB/RADIOLOGY (migrations/0054): moves the order to RESULT_ENTERED
    instead -- a drafted result/report awaiting verify_order_result_
    service + release_order_result_service before it reaches COMPLETED
    and becomes visible as released. Allowed from ORDERED, COLLECTED,
    or IN_PROGRESS (collection/processing are tracked when they happen,
    not hard-required first -- see record_sample_collection_service's
    own docstring); refused once already RESULT_ENTERED, VERIFIED,
    COMPLETED, or CANCELLED -- correcting a drafted-but-wrong result
    goes through cancelling the order and placing a fresh one, same as
    every other "too late to silently redo" case in this schema, not a
    second call here."""
    order_type, status, _ = _lock_order(cur, appointment_id, order_id)

    if _is_diagnostic(order_type):
        if status not in ("ORDERED", "COLLECTED", "IN_PROGRESS"):
            raise OrderNotResultable()
        new_status = "RESULT_ENTERED"
    else:
        if status in ("COMPLETED", "CANCELLED"):
            raise OrderNotResultable()
        new_status = "COMPLETED"

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

    if new_status == "RESULT_ENTERED":
        cur.execute(
            f"""
            UPDATE orders
            SET status = 'RESULT_ENTERED',
                result_entered_by = %s,
                result_entered_at = NOW(),
                updated_at = NOW()
            WHERE id = %s
            RETURNING {", ".join(_ORDER_COLUMNS)}
            """,
            (staff_id, order_id),
        )
    else:
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
    order["samples"] = list_order_samples_service(cur, order_id)
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


def list_order_samples_service(cur, order_id: int):
    cur.execute(
        f"""
        SELECT {", ".join(_SAMPLE_COLUMNS)}
        FROM lab_samples
        WHERE order_id = %s
        ORDER BY collected_at DESC
        """,
        (order_id,),
    )
    return [_sample_row_to_dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------
# Diagnostic lifecycle (OPD/HIMS master spec Phase 7, resumed):
# sample collection -> processing -> verification -> release.
# LAB/RADIOLOGY only -- see _is_diagnostic() and migrations/0054's
# header for why PROCEDURE/SERVICE/EXTERNAL_REFERRAL never reach any of
# these functions.
# ---------------------------------------------------------------------


def record_sample_collection_service(
    cur, appointment_id: int, order_id: int, *, staff_id: int, sample_type: str, notes: str | None = None
):
    """Collects a specimen for a LAB order: ORDERED -> COLLECTED. Not
    hard-required before record_order_result_service will accept a
    result -- collection is tracked when it happens (a real, persisted
    fact staff can see on the worklist), not enforced as a blocking
    gate, the same "optional, not faked" stance pharmacy_stock already
    takes on dispensing without a matching stock batch (migrations/
    0032). RADIOLOGY has no sample concept -- use
    mark_order_in_progress_service for "study performed" instead."""
    order_type, status, _ = _lock_order(cur, appointment_id, order_id)

    if order_type != "LAB":
        raise DiagnosticActionNotSupportedForOrderType()
    if status != "ORDERED":
        raise OrderNotCollectible()

    cur.execute(
        f"""
        INSERT INTO lab_samples (order_id, sample_type, notes, collected_by)
        VALUES (%s, %s, %s, %s)
        RETURNING {", ".join(_SAMPLE_COLUMNS)}
        """,
        (order_id, sample_type, notes, staff_id),
    )
    cur.fetchone()  # sample row itself is re-fetched below via list_order_samples_service

    cur.execute(
        f"""
        UPDATE orders SET status = 'COLLECTED', updated_at = NOW()
        WHERE id = %s
        RETURNING {", ".join(_ORDER_COLUMNS)}
        """,
        (order_id,),
    )
    order = _order_row_to_dict(cur.fetchone())
    order["results"] = list_order_results_service(cur, order_id)
    order["samples"] = list_order_samples_service(cur, order_id)
    return order


def reject_sample_service(
    cur, appointment_id: int, order_id: int, sample_id: int, *, staff_id: int, reason: str
):
    """Rejects a collected sample (wrong tube, hemolyzed, insufficient
    quantity, ...) and reverts the order to ORDERED so it can be
    recollected. Only valid while the order is still COLLECTED --
    once processing/result entry has started, the sample that produced
    them can no longer be un-collected from under them; cancel the
    order and place a fresh one instead, same as every other
    too-late-to-undo case in this schema."""
    order_type, status, _ = _lock_order(cur, appointment_id, order_id)

    if order_type != "LAB":
        raise DiagnosticActionNotSupportedForOrderType()
    if status != "COLLECTED":
        raise OrderNotCollectible()

    cur.execute(
        "SELECT status FROM lab_samples WHERE id = %s AND order_id = %s FOR UPDATE",
        (sample_id, order_id),
    )
    sample_row = cur.fetchone()
    if sample_row is None:
        raise SampleNotFound()
    if sample_row[0] == "REJECTED":
        raise SampleAlreadyRejected()

    cur.execute(
        """
        UPDATE lab_samples
        SET status = 'REJECTED', rejected_by = %s, rejected_reason = %s, rejected_at = NOW()
        WHERE id = %s
        """,
        (staff_id, reason, sample_id),
    )

    cur.execute(
        f"""
        UPDATE orders SET status = 'ORDERED', updated_at = NOW()
        WHERE id = %s
        RETURNING {", ".join(_ORDER_COLUMNS)}
        """,
        (order_id,),
    )
    order = _order_row_to_dict(cur.fetchone())
    order["results"] = list_order_results_service(cur, order_id)
    order["samples"] = list_order_samples_service(cur, order_id)
    return order


def mark_order_in_progress_service(cur, appointment_id: int, order_id: int, *, staff_id: int):
    """The transition the source-of-truth audit named explicitly:
    IN_PROGRESS existed in orders.status's CHECK constraint since
    migrations/0030 but no code path ever set it. LAB: processing has
    started on a collected sample (COLLECTED -> IN_PROGRESS).
    RADIOLOGY: the study/scan has been performed (ORDERED or COLLECTED
    -> IN_PROGRESS directly -- radiology has no sample-collection step,
    so this is its first real status transition after ordering)."""
    order_type, status, _ = _lock_order(cur, appointment_id, order_id)

    if not _is_diagnostic(order_type):
        raise DiagnosticActionNotSupportedForOrderType()

    allowed_from = ("COLLECTED",) if order_type == "LAB" else ("ORDERED", "COLLECTED")
    if status not in allowed_from:
        raise OrderNotStartable()

    cur.execute(
        f"""
        UPDATE orders SET status = 'IN_PROGRESS', updated_at = NOW()
        WHERE id = %s
        RETURNING {", ".join(_ORDER_COLUMNS)}
        """,
        (order_id,),
    )
    order = _order_row_to_dict(cur.fetchone())
    order["results"] = list_order_results_service(cur, order_id)
    order["samples"] = list_order_samples_service(cur, order_id)
    return order


def verify_order_result_service(
    cur, appointment_id: int, order_id: int, *, staff_id: int, staff_role: str
):
    """RESULT_ENTERED -> VERIFIED. The staff account that drafted the
    result (orders.result_entered_by) cannot also verify it unless
    staff_role is ADMIN -- there is no distinct pathologist/senior-lab-
    tech role in this app (confirmed by the source-of-truth audit), so
    this same-person block is the real, honestly-enforceable safeguard
    Phase 7 can back with evidence, rather than a role split invented
    with nothing behind it. ADMIN keeps its existing system-wide
    override standing (same precedent as every other ADMIN-can-do-
    anything gate in this codebase)."""
    order_type, status, result_entered_by = _lock_order(cur, appointment_id, order_id)

    if not _is_diagnostic(order_type):
        raise DiagnosticActionNotSupportedForOrderType()
    if status != "RESULT_ENTERED":
        raise OrderNotVerifiable()
    if staff_role != "ADMIN" and staff_id == result_entered_by:
        raise SameStaffCannotVerifyOwnResult()

    cur.execute(
        f"""
        UPDATE orders
        SET status = 'VERIFIED', verified_by = %s, verified_at = NOW(), updated_at = NOW()
        WHERE id = %s
        RETURNING {", ".join(_ORDER_COLUMNS)}
        """,
        (staff_id, order_id),
    )
    order = _order_row_to_dict(cur.fetchone())
    order["results"] = list_order_results_service(cur, order_id)
    order["samples"] = list_order_samples_service(cur, order_id)
    return order


def release_order_result_service(cur, appointment_id: int, order_id: int, *, staff_id: int):
    """VERIFIED -> COMPLETED (released). This is the point at which a
    LAB/RADIOLOGY result becomes visible as final/trustworthy rather
    than a preliminary draft -- see app/api/orders.py's release_order
    endpoint for the "lab result available" notification, which now
    fires here instead of at result-entry time, since a doctor
    shouldn't be notified about a result that hasn't been verified
    yet."""
    order_type, status, _ = _lock_order(cur, appointment_id, order_id)

    if not _is_diagnostic(order_type):
        raise DiagnosticActionNotSupportedForOrderType()
    if status != "VERIFIED":
        raise OrderNotReleasable()

    cur.execute(
        f"""
        UPDATE orders
        SET status = 'COMPLETED', completed_at = NOW(),
            released_by = %s, released_at = NOW(), updated_at = NOW()
        WHERE id = %s
        RETURNING {", ".join(_ORDER_COLUMNS)}
        """,
        (staff_id, order_id),
    )
    order = _order_row_to_dict(cur.fetchone())
    order["results"] = list_order_results_service(cur, order_id)
    order["samples"] = list_order_samples_service(cur, order_id)
    return order
