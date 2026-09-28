"""
Patient 360 / unified timeline (OPD/HIMS master spec Phase 10, section
44): "patient history exists only as a list of past appointments, not a
cross-domain event timeline" (docs/OPD_HIMS_P0_AUDIT.md section 5) -- this
is the fill for that gap.

Deliberately a read-only aggregation over Phases 3/5-9's existing tables
(encounters, vitals, consultations, orders/order_results,
prescriptions/prescription_items/pharmacy_dispense_records,
invoices/charges/payments) -- no new business data, no new migration.
Shaped by visit (one entry per encounter, most recent first, each
carrying everything that happened during it), not as one flat
interleaved event list: a real clinical/billing history reads visit by
visit, and every one of the tables aggregated here is itself scoped to
exactly one encounter, so grouping by encounter is the structure the
data already has, not an invented one.

Bulk-fetches each table with `encounter_id = ANY(%s)` (one query per
table, five total) rather than looping per-encounter -- a patient with
years of visits could otherwise mean dozens of round trips for what is,
structurally, a handful of IN-list queries.
"""

from app.services.exceptions import PatientNotFound

_VITALS_COLUMNS = (
    "id", "encounter_id", "recorded_by", "bp_systolic", "bp_diastolic",
    "pulse", "temperature_celsius", "spo2", "respiratory_rate",
    "weight_kg", "height_cm", "bmi", "pain_score", "chief_complaint",
    "priority", "nursing_notes", "recorded_at",
)

_CONSULTATION_COLUMNS = (
    "id", "encounter_id", "doctor_id", "status", "chief_complaint",
    "history_notes", "examination_notes", "diagnosis", "clinical_notes",
    "follow_up_date", "follow_up_reason", "started_at", "completed_at",
)

_ORDER_COLUMNS = (
    "id", "encounter_id", "order_type", "description", "clinical_indication",
    "priority", "status", "external_destination", "result_text",
    "ordering_doctor_id", "cancel_reason", "ordered_at", "completed_at",
    "cancelled_at",
)

_ORDER_RESULT_COLUMNS = (
    "id", "order_id", "parameter", "result_value", "unit",
    "unit_system", "unit_code",
    "reference_range", "is_abnormal", "is_critical", "sequence", "recorded_at",
)

_PRESCRIPTION_COLUMNS = (
    "id", "encounter_id", "doctor_id", "status", "notes",
    "prescribed_at", "cancel_reason", "cancelled_at",
)

_PRESCRIPTION_ITEM_COLUMNS = (
    "id", "prescription_id", "medicine_name", "generic_name", "dosage",
    "route", "frequency", "duration", "quantity", "quantity_dispensed",
    "food_instructions", "special_instructions",
)

_DISPENSE_COLUMNS = (
    "id", "prescription_item_id", "quantity", "unit_price", "amount", "dispensed_at",
)

_INVOICE_COLUMNS = (
    "id", "encounter_id", "invoice_number", "discount_amount",
    "tax_rate", "bill_type", "status", "created_at",
)

_CHARGE_COLUMNS = (
    "id", "invoice_id", "description", "amount", "source_type", "status", "created_at",
)

_PAYMENT_COLUMNS = (
    "id", "invoice_id", "receipt_number", "amount", "method", "status",
    "refunded_amount", "recorded_at",
)


def _iso(value):
    return value.isoformat() if value is not None else None


def _row_to_dict(row, columns, iso_fields):
    d = dict(zip(columns, row))
    for f in iso_fields:
        d[f] = _iso(d[f])
    return d


def _group_by(rows, columns, key: str, iso_fields):
    grouped: dict = {}
    for row in rows:
        d = _row_to_dict(row, columns, iso_fields)
        grouped.setdefault(d[key], []).append(d)
    return grouped


def get_patient_timeline_service(cur, patient_id: int, *, hospital_id: int):
    """Follows a merge pointer to the surviving patient, same convention
    as app/services/uhid.py's resolve_patient_by_uhid -- a retired
    patient's own encounters were already moved onto the survivor by
    patient_merge.py, so their own timeline would otherwise read as
    empty even though the visits still exist, just under a different id."""
    cur.execute(
        "SELECT id, name, uhid, merged_into_id FROM patients WHERE id = %s AND hospital_id = %s",
        (patient_id, hospital_id),
    )
    row = cur.fetchone()
    if row is None:
        raise PatientNotFound()

    resolved_id, name, uhid, merged_into_id = row
    redirected_from = None
    if merged_into_id is not None:
        redirected_from = {"patient_id": resolved_id, "uhid": uhid}
        cur.execute("SELECT id, name, uhid FROM patients WHERE id = %s", (merged_into_id,))
        resolved_id, name, uhid = cur.fetchone()

    cur.execute(
        """
        SELECT
            e.id, e.status, e.started_at, e.closed_at,
            e.doctor_id, d.name,
            a.id, a.token_number, a.status, at.name
        FROM encounters e
        JOIN doctors d ON d.id = e.doctor_id
        LEFT JOIN appointments a ON a.encounter_id = e.id
        LEFT JOIN appointment_types at ON at.id = a.appointment_type_id
        WHERE e.patient_id = %s
        ORDER BY e.started_at DESC
        """,
        (resolved_id,),
    )
    encounter_rows = cur.fetchall()
    encounter_ids = [row[0] for row in encounter_rows]

    if not encounter_ids:
        return {
            "patient_id": resolved_id,
            "patient_name": name,
            "patient_uhid": uhid,
            "redirected_from": redirected_from,
            "visits": [],
        }

    cur.execute(
        f"SELECT {', '.join(_VITALS_COLUMNS)} FROM vitals WHERE encounter_id = ANY(%s) ORDER BY recorded_at",
        (encounter_ids,),
    )
    vitals_by_encounter = _group_by(cur.fetchall(), _VITALS_COLUMNS, "encounter_id", ("recorded_at",))

    cur.execute(
        f"SELECT {', '.join(_CONSULTATION_COLUMNS)} FROM consultations WHERE encounter_id = ANY(%s)",
        (encounter_ids,),
    )
    consultation_by_encounter = {
        d["encounter_id"]: d
        for d in (
            _row_to_dict(row, _CONSULTATION_COLUMNS, ("follow_up_date", "started_at", "completed_at"))
            for row in cur.fetchall()
        )
    }

    cur.execute(
        f"SELECT {', '.join(_ORDER_COLUMNS)} FROM orders WHERE encounter_id = ANY(%s) ORDER BY ordered_at",
        (encounter_ids,),
    )
    order_rows = cur.fetchall()
    orders_by_encounter = _group_by(
        order_rows, _ORDER_COLUMNS, "encounter_id", ("ordered_at", "completed_at", "cancelled_at")
    )
    order_ids = [row[0] for row in order_rows]

    results_by_order: dict = {}
    if order_ids:
        cur.execute(
            f"SELECT {', '.join(_ORDER_RESULT_COLUMNS)} FROM order_results "
            "WHERE order_id = ANY(%s) ORDER BY sequence, recorded_at",
            (order_ids,),
        )
        results_by_order = _group_by(cur.fetchall(), _ORDER_RESULT_COLUMNS, "order_id", ("recorded_at",))

    for orders in orders_by_encounter.values():
        for order in orders:
            order["results"] = results_by_order.get(order["id"], [])

    cur.execute(
        f"SELECT {', '.join(_PRESCRIPTION_COLUMNS)} FROM prescriptions WHERE encounter_id = ANY(%s)",
        (encounter_ids,),
    )
    prescription_rows = cur.fetchall()
    prescription_by_encounter = {
        d["encounter_id"]: d
        for d in (
            _row_to_dict(row, _PRESCRIPTION_COLUMNS, ("prescribed_at", "cancelled_at"))
            for row in prescription_rows
        )
    }
    prescription_ids = [row[0] for row in prescription_rows]

    items_by_prescription: dict = {}
    item_ids: list = []
    if prescription_ids:
        cur.execute(
            f"SELECT {', '.join(_PRESCRIPTION_ITEM_COLUMNS)} FROM prescription_items "
            "WHERE prescription_id = ANY(%s) ORDER BY id",
            (prescription_ids,),
        )
        item_rows = cur.fetchall()
        items_by_prescription = _group_by(item_rows, _PRESCRIPTION_ITEM_COLUMNS, "prescription_id", ())
        item_ids = [row[0] for row in item_rows]

    dispenses_by_item: dict = {}
    if item_ids:
        cur.execute(
            f"SELECT {', '.join(_DISPENSE_COLUMNS)} FROM pharmacy_dispense_records "
            "WHERE prescription_item_id = ANY(%s) ORDER BY dispensed_at",
            (item_ids,),
        )
        dispenses_by_item = _group_by(
            cur.fetchall(), _DISPENSE_COLUMNS, "prescription_item_id", ("dispensed_at",)
        )

    for items in items_by_prescription.values():
        for item in items:
            item["dispenses"] = dispenses_by_item.get(item["id"], [])

    for prescription in prescription_by_encounter.values():
        prescription["items"] = items_by_prescription.get(prescription["id"], [])

    cur.execute(
        f"SELECT {', '.join(_INVOICE_COLUMNS)} FROM invoices WHERE encounter_id = ANY(%s)",
        (encounter_ids,),
    )
    invoice_rows = cur.fetchall()
    invoice_by_encounter = {
        d["encounter_id"]: d
        for d in (_row_to_dict(row, _INVOICE_COLUMNS, ("created_at",)) for row in invoice_rows)
    }
    invoice_ids = [row[0] for row in invoice_rows]

    charges_by_invoice: dict = {}
    payments_by_invoice: dict = {}
    if invoice_ids:
        cur.execute(
            f"SELECT {', '.join(_CHARGE_COLUMNS)} FROM charges "
            "WHERE invoice_id = ANY(%s) AND status = 'ACTIVE' ORDER BY created_at",
            (invoice_ids,),
        )
        charges_by_invoice = _group_by(cur.fetchall(), _CHARGE_COLUMNS, "invoice_id", ("created_at",))

        cur.execute(
            f"SELECT {', '.join(_PAYMENT_COLUMNS)} FROM payments "
            "WHERE invoice_id = ANY(%s) AND status = 'COMPLETED' ORDER BY recorded_at",
            (invoice_ids,),
        )
        payments_by_invoice = _group_by(cur.fetchall(), _PAYMENT_COLUMNS, "invoice_id", ("recorded_at",))

    for invoice in invoice_by_encounter.values():
        invoice["charges"] = charges_by_invoice.get(invoice["id"], [])
        invoice["payments"] = payments_by_invoice.get(invoice["id"], [])

    visits = []
    for (
        encounter_id, status, started_at, closed_at, doctor_id, doctor_name,
        appointment_id, token_number, appointment_status, appointment_type_name,
    ) in encounter_rows:
        visits.append({
            "encounter_id": encounter_id,
            "status": status,
            "started_at": _iso(started_at),
            "closed_at": _iso(closed_at),
            "doctor_id": doctor_id,
            "doctor_name": doctor_name,
            "appointment_id": appointment_id,
            "token_number": token_number,
            "appointment_status": appointment_status,
            "appointment_type_name": appointment_type_name,
            "vitals": vitals_by_encounter.get(encounter_id, []),
            "consultation": consultation_by_encounter.get(encounter_id),
            "orders": orders_by_encounter.get(encounter_id, []),
            "prescription": prescription_by_encounter.get(encounter_id),
            "invoice": invoice_by_encounter.get(encounter_id),
        })

    return {
        "patient_id": resolved_id,
        "patient_name": name,
        "patient_uhid": uhid,
        "redirected_from": redirected_from,
        "visits": visits,
    }
