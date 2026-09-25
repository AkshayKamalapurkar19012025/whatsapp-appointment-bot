"""
Prescription + Pharmacy (OPD/HIMS master spec Phase 8), built on the
encounter foundation the same way consultations/orders were.

Write gates follow the same pattern settled across Phases 5-7:
- Writing to a DRAFT prescription (adding/removing items, signing it as
  PRESCRIBED) requires the appointment to be CHECKED_IN -- this is
  live-visit clinical documentation, same as a consultation.
- Cancelling a PRESCRIBED prescription is NOT gated on CHECKED_IN (same
  reasoning as order cancellation): correcting a mistake has no
  deadline. It IS blocked once any item has actually been dispensed --
  see PrescriptionNotCancellable.
- Dispensing is NOT gated on CHECKED_IN either. Unlike a lab result
  (which can come back days later), pharmacy dispensing is usually
  same-visit -- but the master spec's own Visit Completion checklist
  (section 43) only requires "Prescription created", not "dispensed",
  meaning a patient can still be standing at the pharmacy counter after
  front-desk has already marked the visit COMPLETED. Gating on
  CHECKED_IN would make that ordinary case impossible.
"""

from app.services.clinical_services import (
    get_appointment_status_and_doctor,
    get_encounter_id_for_appointment,
)
from app.services.exceptions import (
    EncounterClosed,
    ModuleUnavailable,
    PrescriptionNotFound,
    PrescriptionAlreadyPrescribed,
    PrescriptionEmpty,
    PrescriptionNotCancellable,
    PrescriptionItemNotFound,
    PrescriptionItemNotDispensable,
    DispenseQuantityExceedsRemaining,
    InsufficientStock,
    MedicineMismatch,
    PharmacyStockNotFound,
    DuplicateStockBatch,
)
from app.services.module_services import is_module_available

import psycopg

_PRESCRIPTION_COLUMNS = (
    "id", "encounter_id", "doctor_id", "status", "notes", "prescribed_at",
    "cancelled_by", "cancel_reason", "cancelled_at", "created_by",
    "created_at", "updated_at",
)

_ITEM_COLUMNS = (
    "id", "prescription_id", "medicine_name", "generic_name", "dosage",
    "route", "frequency", "duration", "quantity", "quantity_dispensed",
    "food_instructions", "special_instructions", "created_at", "updated_at",
)

_STOCK_COLUMNS = (
    "id", "medicine_name", "batch_number", "expiry_date", "quantity_on_hand",
    "unit_price", "active", "created_by", "created_at", "updated_at",
)

_DISPENSE_COLUMNS = (
    "id", "prescription_item_id", "pharmacy_stock_id", "quantity",
    "unit_price", "amount", "dispensed_by", "dispensed_at",
)


def _prescription_row_to_dict(row) -> dict:
    d = dict(zip(_PRESCRIPTION_COLUMNS, row))
    d["prescribed_at"] = d["prescribed_at"].isoformat() if d["prescribed_at"] else None
    d["cancelled_at"] = d["cancelled_at"].isoformat() if d["cancelled_at"] else None
    d["created_at"] = d["created_at"].isoformat()
    d["updated_at"] = d["updated_at"].isoformat()
    return d


def _item_row_to_dict(row) -> dict:
    d = dict(zip(_ITEM_COLUMNS, row))
    d["created_at"] = d["created_at"].isoformat()
    d["updated_at"] = d["updated_at"].isoformat()
    if d["quantity_dispensed"] == 0:
        d["dispense_status"] = "PENDING"
    elif d["quantity_dispensed"] < d["quantity"]:
        d["dispense_status"] = "PARTIALLY_DISPENSED"
    else:
        d["dispense_status"] = "DISPENSED"
    return d


def _stock_row_to_dict(row) -> dict:
    d = dict(zip(_STOCK_COLUMNS, row))
    d["expiry_date"] = d["expiry_date"].isoformat()
    d["created_at"] = d["created_at"].isoformat()
    d["updated_at"] = d["updated_at"].isoformat()
    return d


def _dispense_row_to_dict(row) -> dict:
    d = dict(zip(_DISPENSE_COLUMNS, row))
    d["dispensed_at"] = d["dispensed_at"].isoformat()
    return d


def _items_for_prescription(cur, prescription_id: int) -> list[dict]:
    cur.execute(
        f"SELECT {', '.join(_ITEM_COLUMNS)} FROM prescription_items WHERE prescription_id = %s ORDER BY id",
        (prescription_id,),
    )
    return [_item_row_to_dict(row) for row in cur.fetchall()]


def _full_prescription_dict(cur, prescription_id: int) -> dict:
    cur.execute(
        f"SELECT {', '.join(_PRESCRIPTION_COLUMNS)} FROM prescriptions WHERE id = %s",
        (prescription_id,),
    )
    prescription = _prescription_row_to_dict(cur.fetchone())
    prescription["items"] = _items_for_prescription(cur, prescription_id)
    return prescription


def _ensure_prescription(cur, appointment_id: int, staff_id: int) -> dict:
    """Returns {id, status, appointment_status, doctor_id} for the
    encounter's prescription, creating a blank DRAFT one if none exists
    yet (requires CHECKED_IN to create -- see module docstring)."""
    appointment = get_appointment_status_and_doctor(cur, appointment_id)
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute("SELECT id, status FROM prescriptions WHERE encounter_id = %s", (encounter_id,))
    row = cur.fetchone()
    if row is not None:
        return {
            "id": row[0], "status": row[1],
            "appointment_status": appointment["status"], "doctor_id": appointment["doctor_id"],
        }

    if appointment["status"] != "CHECKED_IN":
        raise EncounterClosed()

    cur.execute(
        """
        INSERT INTO prescriptions (encounter_id, doctor_id, created_by)
        VALUES (%s, %s, %s)
        ON CONFLICT (encounter_id) DO NOTHING
        RETURNING id, status
        """,
        (encounter_id, appointment["doctor_id"], staff_id),
    )
    row = cur.fetchone()
    if row is None:
        # Lost a race with a concurrent create for the same encounter.
        cur.execute("SELECT id, status FROM prescriptions WHERE encounter_id = %s", (encounter_id,))
        row = cur.fetchone()

    return {
        "id": row[0], "status": row[1],
        "appointment_status": appointment["status"], "doctor_id": appointment["doctor_id"],
    }


def _get_prescription_for_appointment(cur, appointment_id: int) -> dict:
    """For actions on an already-existing prescription (remove item,
    prescribe, cancel) -- never creates one."""
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)
    cur.execute(
        "SELECT id, status FROM prescriptions WHERE encounter_id = %s FOR UPDATE",
        (encounter_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise PrescriptionNotFound()
    return {"id": row[0], "status": row[1]}


def get_or_create_prescription_service(cur, appointment_id: int, *, staff_id: int):
    ensured = _ensure_prescription(cur, appointment_id, staff_id)
    return _full_prescription_dict(cur, ensured["id"])


def add_prescription_item_service(
    cur,
    appointment_id: int,
    *,
    staff_id: int,
    hospital_id: int,
    medicine_name: str,
    quantity: int,
    generic_name: str | None = None,
    dosage: str | None = None,
    route: str | None = None,
    frequency: str | None = None,
    duration: str | None = None,
    food_instructions: str | None = None,
    special_instructions: str | None = None,
):
    if not is_module_available(cur, hospital_id, "PHARMACY"):
        raise ModuleUnavailable("PHARMACY")

    ensured = _ensure_prescription(cur, appointment_id, staff_id)

    if ensured["status"] != "DRAFT":
        raise PrescriptionAlreadyPrescribed()

    if ensured["appointment_status"] != "CHECKED_IN":
        raise EncounterClosed()

    cur.execute(
        """
        INSERT INTO prescription_items (
            prescription_id, medicine_name, generic_name, dosage, route,
            frequency, duration, quantity, food_instructions, special_instructions
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            ensured["id"], medicine_name, generic_name, dosage, route,
            frequency, duration, quantity, food_instructions, special_instructions,
        ),
    )

    return _full_prescription_dict(cur, ensured["id"])


def remove_prescription_item_service(cur, appointment_id: int, item_id: int, *, staff_id: int):
    prescription = _get_prescription_for_appointment(cur, appointment_id)

    if prescription["status"] != "DRAFT":
        raise PrescriptionAlreadyPrescribed()

    cur.execute(
        "DELETE FROM prescription_items WHERE id = %s AND prescription_id = %s",
        (item_id, prescription["id"]),
    )
    if cur.rowcount == 0:
        raise PrescriptionItemNotFound()

    return _full_prescription_dict(cur, prescription["id"])


def prescribe_service(cur, appointment_id: int, *, staff_id: int, hospital_id: int):
    """Signs off the DRAFT prescription and sends it to pharmacy --
    requires at least one item (PrescriptionEmpty otherwise)."""
    if not is_module_available(cur, hospital_id, "PHARMACY"):
        raise ModuleUnavailable("PHARMACY")

    appointment = get_appointment_status_and_doctor(cur, appointment_id)
    prescription = _get_prescription_for_appointment(cur, appointment_id)

    if prescription["status"] != "DRAFT":
        raise PrescriptionAlreadyPrescribed()

    if appointment["status"] != "CHECKED_IN":
        raise EncounterClosed()

    cur.execute("SELECT COUNT(*) FROM prescription_items WHERE prescription_id = %s", (prescription["id"],))
    (item_count,) = cur.fetchone()
    if item_count == 0:
        raise PrescriptionEmpty()

    cur.execute(
        """
        UPDATE prescriptions
        SET status = 'PRESCRIBED', prescribed_at = NOW(), updated_at = NOW()
        WHERE id = %s
        """,
        (prescription["id"],),
    )

    return _full_prescription_dict(cur, prescription["id"])


def cancel_prescription_service(cur, appointment_id: int, *, staff_id: int, reason: str):
    """Cancels a PRESCRIBED prescription -- refused
    (PrescriptionNotCancellable) once any item has already been
    dispensed; medicine already handed over can't be un-prescribed."""
    prescription = _get_prescription_for_appointment(cur, appointment_id)

    if prescription["status"] != "PRESCRIBED":
        raise PrescriptionNotCancellable()

    cur.execute(
        "SELECT COALESCE(SUM(quantity_dispensed), 0) FROM prescription_items WHERE prescription_id = %s",
        (prescription["id"],),
    )
    (dispensed_total,) = cur.fetchone()
    if dispensed_total > 0:
        raise PrescriptionNotCancellable()

    cur.execute(
        """
        UPDATE prescriptions
        SET status = 'CANCELLED', cancelled_by = %s, cancel_reason = %s,
            cancelled_at = NOW(), updated_at = NOW()
        WHERE id = %s
        """,
        (staff_id, reason, prescription["id"]),
    )

    return _full_prescription_dict(cur, prescription["id"])


# ---------------------------------------------------------------------
# Pharmacy: cross-patient queue, stock, dispensing
# ---------------------------------------------------------------------


def list_pharmacy_queue_service(cur):
    """Every PRESCRIBED prescription with at least one item not yet
    fully dispensed -- the pharmacy's work queue. A prescription drops
    off this list naturally once every item reaches DISPENSED; there is
    no separate Preparing/Ready sub-state (no real workflow behind
    those yet -- same discipline Phase 7 applied to the lab worklist)."""
    cur.execute(
        """
        SELECT DISTINCT
            p.id, p.prescribed_at, p.doctor_id, d.name,
            pat.id, pat.name, pat.uhid, a.id
        FROM prescriptions p
        JOIN prescription_items pi ON pi.prescription_id = p.id
        JOIN encounters e ON e.id = p.encounter_id
        JOIN patients pat ON pat.id = e.patient_id
        JOIN appointments a ON a.encounter_id = e.id
        JOIN doctors d ON d.id = p.doctor_id
        WHERE p.status = 'PRESCRIBED'
          AND pi.quantity_dispensed < pi.quantity
        ORDER BY p.prescribed_at
        """,
    )
    rows = cur.fetchall()

    queue = []
    for row in rows:
        prescription_id = row[0]
        queue.append({
            "prescription_id": prescription_id,
            "prescribed_at": row[1].isoformat(),
            "doctor_id": row[2],
            "doctor_name": row[3],
            "patient_id": row[4],
            "patient_name": row[5],
            "patient_uhid": row[6],
            "appointment_id": row[7],
            "items": _items_for_prescription(cur, prescription_id),
        })
    return queue


def list_pharmacy_stock_service(cur, medicine_name: str | None = None):
    if medicine_name:
        cur.execute(
            f"""
            SELECT {", ".join(_STOCK_COLUMNS)} FROM pharmacy_stock
            WHERE active AND medicine_name ILIKE %s
            ORDER BY medicine_name, expiry_date
            """,
            (f"%{medicine_name}%",),
        )
    else:
        cur.execute(
            f"SELECT {', '.join(_STOCK_COLUMNS)} FROM pharmacy_stock WHERE active ORDER BY medicine_name, expiry_date",
        )
    return [_stock_row_to_dict(row) for row in cur.fetchall()]


def create_pharmacy_stock_service(
    cur,
    *,
    staff_id: int,
    medicine_name: str,
    batch_number: str,
    expiry_date,
    quantity_on_hand: int,
    unit_price=0,
):
    try:
        cur.execute(
            f"""
            INSERT INTO pharmacy_stock (
                medicine_name, batch_number, expiry_date, quantity_on_hand, unit_price, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {", ".join(_STOCK_COLUMNS)}
            """,
            (medicine_name, batch_number, expiry_date, quantity_on_hand, unit_price, staff_id),
        )
    except psycopg.errors.UniqueViolation:
        raise DuplicateStockBatch()

    return _stock_row_to_dict(cur.fetchone())


def record_dispense_service(
    cur,
    prescription_item_id: int,
    *,
    staff_id: int,
    hospital_id: int,
    quantity: int,
    pharmacy_stock_id: int | None = None,
    unit_price=None,
):
    if not is_module_available(cur, hospital_id, "PHARMACY"):
        raise ModuleUnavailable("PHARMACY")

    cur.execute(
        """
        SELECT id, prescription_id, medicine_name, quantity, quantity_dispensed
        FROM prescription_items
        WHERE id = %s
        FOR UPDATE
        """,
        (prescription_item_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise PrescriptionItemNotFound()

    item_id, prescription_id, medicine_name, item_quantity, quantity_dispensed = row

    cur.execute("SELECT status FROM prescriptions WHERE id = %s", (prescription_id,))
    (prescription_status,) = cur.fetchone()
    if prescription_status != "PRESCRIBED":
        raise PrescriptionItemNotDispensable()

    remaining = item_quantity - quantity_dispensed
    if quantity > remaining:
        raise DispenseQuantityExceedsRemaining()

    resolved_unit_price = unit_price if unit_price is not None else 0

    if pharmacy_stock_id is not None:
        cur.execute(
            "SELECT id, medicine_name, quantity_on_hand, unit_price FROM pharmacy_stock WHERE id = %s FOR UPDATE",
            (pharmacy_stock_id,),
        )
        stock_row = cur.fetchone()
        if stock_row is None:
            raise PharmacyStockNotFound()

        stock_id, stock_medicine_name, quantity_on_hand, stock_unit_price = stock_row

        if stock_medicine_name.strip().lower() != medicine_name.strip().lower():
            raise MedicineMismatch()

        if quantity_on_hand < quantity:
            raise InsufficientStock()

        cur.execute(
            "UPDATE pharmacy_stock SET quantity_on_hand = quantity_on_hand - %s, updated_at = NOW() WHERE id = %s",
            (quantity, stock_id),
        )
        resolved_unit_price = stock_unit_price

    amount = resolved_unit_price * quantity

    cur.execute(
        f"""
        INSERT INTO pharmacy_dispense_records (
            prescription_item_id, pharmacy_stock_id, quantity, unit_price, amount, dispensed_by
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING {", ".join(_DISPENSE_COLUMNS)}
        """,
        (item_id, pharmacy_stock_id, quantity, resolved_unit_price, amount, staff_id),
    )
    dispense_record = _dispense_row_to_dict(cur.fetchone())

    cur.execute(
        f"""
        UPDATE prescription_items
        SET quantity_dispensed = quantity_dispensed + %s, updated_at = NOW()
        WHERE id = %s
        RETURNING {", ".join(_ITEM_COLUMNS)}
        """,
        (quantity, item_id),
    )
    item = _item_row_to_dict(cur.fetchone())
    item["dispense_record"] = dispense_record
    return item
