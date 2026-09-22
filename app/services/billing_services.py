"""
Billing (OPD/HIMS master spec Phase 9): a new, encounter-scoped
invoice/charge/payment model (migrations/0033_billing_invoices.sql),
built to bill everything Phases 6-8 left unbilled -- lab/radiology/
procedure orders, pharmacy dispenses, and ad-hoc charges -- without
touching the existing appointments.consultation_fee/payment_status
mechanism (app/services/appointment_services.py's record_payment_
service/waive_consultation_fee_service) at all. See the migration's own
header for the full reasoning; in short, that existing mechanism is
directly wired to queue token issuance and is the most heavily-tested
payment path in the app, so it stays exactly as it is.

Unlike every other clinical write in this app (consultations, vitals,
orders, prescriptions -- all gated on the appointment being CHECKED_IN),
nothing here is gated on appointment status at all. Billing is an
administrative/financial function, not clinical documentation: a charge
can legitimately be added, or a payment recorded, before check-in (an
estimate), during the visit, or well after it closes (a pharmacy
dispense billed the next day, a late-arriving lab charge). The
CHECKED_IN gate the other services use exists to keep clinical
documentation scoped to the live visit; billing has no equivalent
constraint.
"""

from app.services.clinical_services import get_encounter_id_for_appointment
from app.services.exceptions import (
    InvoiceNotFound,
    InvoiceVoided,
    InvoiceNotVoidable,
    ChargeNotFound,
    ChargeAlreadyVoided,
    DuplicateCharge,
    InvalidChargeSource,
    PackageNotFound,
    PaymentNotFound,
    PaymentAlreadyVoided,
    PaymentExceedsBalance,
    PaymentRefundExceedsAmount,
    DuplicateTransactionId,
)

import psycopg

_INVOICE_COLUMNS = (
    "id", "encounter_id", "invoice_number", "discount_amount", "discount_reason",
    "tax_rate", "bill_type", "status", "voided_by", "void_reason", "voided_at",
    "created_by", "created_at", "updated_at",
)

_CHARGE_COLUMNS = (
    "id", "invoice_id", "description", "amount", "source_type",
    "source_order_id", "source_dispense_id", "source_package_id", "status", "voided_by",
    "void_reason", "voided_at", "created_by", "created_at", "updated_at",
)

_PAYMENT_COLUMNS = (
    "id", "invoice_id", "receipt_number", "amount", "method", "transaction_id",
    "status", "refunded_amount", "refund_reason", "refunded_by", "refunded_at",
    "voided_by", "void_reason", "voided_at", "recorded_by", "recorded_at",
)


def _invoice_row_to_dict(row) -> dict:
    d = dict(zip(_INVOICE_COLUMNS, row))
    d["voided_at"] = d["voided_at"].isoformat() if d["voided_at"] else None
    d["created_at"] = d["created_at"].isoformat()
    d["updated_at"] = d["updated_at"].isoformat()
    return d


def _charge_row_to_dict(row) -> dict:
    d = dict(zip(_CHARGE_COLUMNS, row))
    d["voided_at"] = d["voided_at"].isoformat() if d["voided_at"] else None
    d["created_at"] = d["created_at"].isoformat()
    d["updated_at"] = d["updated_at"].isoformat()
    return d


def _payment_row_to_dict(row) -> dict:
    d = dict(zip(_PAYMENT_COLUMNS, row))
    d["refunded_at"] = d["refunded_at"].isoformat() if d["refunded_at"] else None
    d["voided_at"] = d["voided_at"].isoformat() if d["voided_at"] else None
    d["recorded_at"] = d["recorded_at"].isoformat()
    return d


def _compute_totals(gross, discount_amount, tax_rate, paid_effective):
    taxable = gross - discount_amount
    tax_amount = round(max(taxable, 0) * tax_rate / 100, 2)
    net_amount = taxable + tax_amount
    balance = net_amount - paid_effective

    if net_amount <= 0 or balance <= 0:
        payment_status = "PAID" if paid_effective > 0 or net_amount <= 0 else "UNPAID"
    elif paid_effective > 0:
        payment_status = "PARTIALLY_PAID"
    else:
        payment_status = "UNPAID"

    return {
        "gross_amount": gross,
        "discount_amount": discount_amount,
        "taxable_amount": taxable,
        "tax_amount": tax_amount,
        "net_amount": net_amount,
        "paid_amount": paid_effective,
        "balance": balance,
        "payment_status": payment_status,
    }


def _ensure_invoice(cur, appointment_id: int, staff_id: int) -> dict:
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(f"SELECT {', '.join(_INVOICE_COLUMNS)} FROM invoices WHERE encounter_id = %s", (encounter_id,))
    row = cur.fetchone()
    if row is not None:
        return _invoice_row_to_dict(row)

    cur.execute(
        f"""
        INSERT INTO invoices (encounter_id, created_by)
        VALUES (%s, %s)
        ON CONFLICT (encounter_id) DO NOTHING
        RETURNING {", ".join(_INVOICE_COLUMNS)}
        """,
        (encounter_id, staff_id),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute(f"SELECT {', '.join(_INVOICE_COLUMNS)} FROM invoices WHERE encounter_id = %s", (encounter_id,))
        row = cur.fetchone()

    return _invoice_row_to_dict(row)


def _get_invoice_for_appointment(cur, appointment_id: int, *, lock: bool = False) -> dict:
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)
    suffix = " FOR UPDATE" if lock else ""
    cur.execute(f"SELECT {', '.join(_INVOICE_COLUMNS)} FROM invoices WHERE encounter_id = %s{suffix}", (encounter_id,))
    row = cur.fetchone()
    if row is None:
        raise InvoiceNotFound()
    return _invoice_row_to_dict(row)


def get_or_create_invoice_service(cur, appointment_id: int, *, staff_id: int):
    return _ensure_invoice(cur, appointment_id, staff_id)


def get_invoice_summary_service(cur, appointment_id: int, *, staff_id: int):
    # staff_id is only actually used if this is the first call for this
    # encounter and a row has to be created (created_by is NOT NULL,
    # same attribution discipline as every other "ensure" function in
    # this codebase) -- an already-existing invoice's created_by is
    # left untouched.
    invoice = _ensure_invoice(cur, appointment_id, staff_id)

    cur.execute(
        f"SELECT {', '.join(_CHARGE_COLUMNS)} FROM charges WHERE invoice_id = %s ORDER BY created_at",
        (invoice["id"],),
    )
    charges = [_charge_row_to_dict(row) for row in cur.fetchall()]

    cur.execute(
        f"SELECT {', '.join(_PAYMENT_COLUMNS)} FROM payments WHERE invoice_id = %s ORDER BY recorded_at",
        (invoice["id"],),
    )
    payments = [_payment_row_to_dict(row) for row in cur.fetchall()]

    # sum()'s default start=0 (a plain int) mixes fine with the
    # Decimal amounts psycopg returns for NUMERIC columns -- Decimal's
    # __radd__ handles int 0 correctly, no need to seed the type.
    gross = sum(c["amount"] for c in charges if c["status"] == "ACTIVE")
    paid_effective = sum(p["amount"] - p["refunded_amount"] for p in payments if p["status"] == "COMPLETED")

    totals = _compute_totals(gross, invoice["discount_amount"], invoice["tax_rate"], paid_effective)

    return {**invoice, "charges": charges, "payments": payments, **totals}


def update_invoice_terms_service(
    cur,
    appointment_id: int,
    *,
    staff_id: int,
    discount_amount=None,
    discount_reason: str | None = None,
    tax_rate=None,
    bill_type: str | None = None,
):
    invoice = _ensure_invoice(cur, appointment_id, staff_id)

    if invoice["status"] == "VOID":
        raise InvoiceVoided()

    cur.execute(
        """
        UPDATE invoices
        SET discount_amount = COALESCE(%s, discount_amount),
            discount_reason = COALESCE(%s, discount_reason),
            tax_rate = COALESCE(%s, tax_rate),
            bill_type = COALESCE(%s, bill_type),
            updated_at = NOW()
        WHERE id = %s
        """,
        (discount_amount, discount_reason, tax_rate, bill_type, invoice["id"]),
    )

    return get_invoice_summary_service(cur, appointment_id, staff_id=staff_id)


def void_invoice_service(cur, appointment_id: int, *, staff_id: int, reason: str):
    invoice = _get_invoice_for_appointment(cur, appointment_id, lock=True)

    if invoice["status"] == "VOID":
        raise InvoiceVoided()

    cur.execute(
        "SELECT COUNT(*) FROM payments WHERE invoice_id = %s AND status = 'COMPLETED'",
        (invoice["id"],),
    )
    (payment_count,) = cur.fetchone()
    if payment_count > 0:
        raise InvoiceNotVoidable()

    cur.execute(
        """
        UPDATE invoices
        SET status = 'VOID', voided_by = %s, void_reason = %s, voided_at = NOW(), updated_at = NOW()
        WHERE id = %s
        """,
        (staff_id, reason, invoice["id"]),
    )

    return get_invoice_summary_service(cur, appointment_id, staff_id=staff_id)


# ---------------------------------------------------------------------
# Charges
# ---------------------------------------------------------------------


def list_unbilled_sources_service(cur, appointment_id: int):
    """Orders and pharmacy dispenses for this encounter that don't yet
    have a charge linked to them -- the "aggregate applicable charges"
    helper (master spec section 38), so billing staff can pick from a
    real list instead of retyping descriptions from memory."""
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        """
        SELECT o.id, o.order_type, o.description, o.priority
        FROM orders o
        LEFT JOIN charges c ON c.source_order_id = o.id
        WHERE o.encounter_id = %s
          AND o.status <> 'CANCELLED'
          AND c.id IS NULL
        ORDER BY o.ordered_at
        """,
        (encounter_id,),
    )
    unbilled_orders = [
        {"order_id": row[0], "order_type": row[1], "description": row[2], "priority": row[3]}
        for row in cur.fetchall()
    ]

    cur.execute(
        """
        SELECT pdr.id, pi.medicine_name, pdr.quantity, pdr.amount, pdr.dispensed_at
        FROM pharmacy_dispense_records pdr
        JOIN prescription_items pi ON pi.id = pdr.prescription_item_id
        JOIN prescriptions p ON p.id = pi.prescription_id
        LEFT JOIN charges c ON c.source_dispense_id = pdr.id
        WHERE p.encounter_id = %s
          AND c.id IS NULL
        ORDER BY pdr.dispensed_at
        """,
        (encounter_id,),
    )
    unbilled_dispenses = [
        {
            "dispense_id": row[0], "medicine_name": row[1], "quantity": row[2],
            "amount": row[3], "dispensed_at": row[4].isoformat(),
        }
        for row in cur.fetchall()
    ]

    return {"orders": unbilled_orders, "dispenses": unbilled_dispenses}


def add_charge_service(
    cur,
    appointment_id: int,
    *,
    staff_id: int,
    description: str,
    amount,
    source_type: str = "OTHER",
    source_order_id: int | None = None,
    source_dispense_id: int | None = None,
    source_package_id: int | None = None,
):
    invoice = _ensure_invoice(cur, appointment_id, staff_id)

    if invoice["status"] == "VOID":
        raise InvoiceVoided()

    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    if source_order_id is not None:
        cur.execute("SELECT encounter_id FROM orders WHERE id = %s", (source_order_id,))
        row = cur.fetchone()
        if row is None or row[0] != encounter_id:
            raise InvalidChargeSource()

    if source_dispense_id is not None:
        cur.execute(
            """
            SELECT p.encounter_id
            FROM pharmacy_dispense_records pdr
            JOIN prescription_items pi ON pi.id = pdr.prescription_item_id
            JOIN prescriptions p ON p.id = pi.prescription_id
            WHERE pdr.id = %s
            """,
            (source_dispense_id,),
        )
        row = cur.fetchone()
        if row is None or row[0] != encounter_id:
            raise InvalidChargeSource()

    if source_package_id is not None:
        # Scoped by the encounter's own hospital, not the requesting
        # staff's -- add_charge_service has no staff hospital_id in
        # scope, and the encounter's is the one that actually matters
        # here (billing another hospital's package to this visit would
        # be the real bug, regardless of who's billing it).
        cur.execute(
            """
            SELECT p.id
            FROM packages p
            JOIN encounters e ON e.hospital_id = p.hospital_id
            WHERE p.id = %s AND e.id = %s AND p.active = TRUE
            """,
            (source_package_id, encounter_id),
        )
        if cur.fetchone() is None:
            raise PackageNotFound()

    try:
        cur.execute(
            f"""
            INSERT INTO charges (
                invoice_id, description, amount, source_type, source_order_id,
                source_dispense_id, source_package_id, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {", ".join(_CHARGE_COLUMNS)}
            """,
            (
                invoice["id"], description, amount, source_type, source_order_id,
                source_dispense_id, source_package_id, staff_id,
            ),
        )
    except psycopg.errors.UniqueViolation:
        raise DuplicateCharge()

    cur.fetchone()
    return get_invoice_summary_service(cur, appointment_id, staff_id=staff_id)


def void_charge_service(cur, appointment_id: int, charge_id: int, *, staff_id: int, reason: str):
    invoice = _get_invoice_for_appointment(cur, appointment_id)

    cur.execute(
        "SELECT status FROM charges WHERE id = %s AND invoice_id = %s FOR UPDATE",
        (charge_id, invoice["id"]),
    )
    row = cur.fetchone()
    if row is None:
        raise ChargeNotFound()
    if row[0] == "VOIDED":
        raise ChargeAlreadyVoided()

    cur.execute(
        """
        UPDATE charges
        SET status = 'VOIDED', voided_by = %s, void_reason = %s, voided_at = NOW(), updated_at = NOW()
        WHERE id = %s
        """,
        (staff_id, reason, charge_id),
    )

    return get_invoice_summary_service(cur, appointment_id, staff_id=staff_id)


# ---------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------


def record_invoice_payment_service(
    cur,
    appointment_id: int,
    *,
    staff_id: int,
    amount,
    method: str,
    transaction_id: str | None = None,
):
    invoice = _get_invoice_for_appointment(cur, appointment_id, lock=True)

    if invoice["status"] == "VOID":
        raise InvoiceVoided()

    summary = get_invoice_summary_service(cur, appointment_id, staff_id=staff_id)
    if amount > summary["balance"]:
        raise PaymentExceedsBalance()

    try:
        cur.execute(
            f"""
            INSERT INTO payments (invoice_id, amount, method, transaction_id, recorded_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING {", ".join(_PAYMENT_COLUMNS)}
            """,
            (invoice["id"], amount, method, transaction_id, staff_id),
        )
    except psycopg.errors.UniqueViolation:
        raise DuplicateTransactionId()

    cur.fetchone()
    return get_invoice_summary_service(cur, appointment_id, staff_id=staff_id)


def void_invoice_payment_service(cur, appointment_id: int, payment_id: int, *, staff_id: int, reason: str):
    invoice = _get_invoice_for_appointment(cur, appointment_id)

    cur.execute(
        "SELECT status FROM payments WHERE id = %s AND invoice_id = %s FOR UPDATE",
        (payment_id, invoice["id"]),
    )
    row = cur.fetchone()
    if row is None:
        raise PaymentNotFound()
    if row[0] == "VOIDED":
        raise PaymentAlreadyVoided()

    cur.execute(
        """
        UPDATE payments
        SET status = 'VOIDED', voided_by = %s, void_reason = %s, voided_at = NOW()
        WHERE id = %s
        """,
        (staff_id, reason, payment_id),
    )

    return get_invoice_summary_service(cur, appointment_id, staff_id=staff_id)


def refund_invoice_payment_service(
    cur, appointment_id: int, payment_id: int, *, staff_id: int, amount, reason: str
):
    invoice = _get_invoice_for_appointment(cur, appointment_id)

    cur.execute(
        "SELECT status, amount, refunded_amount FROM payments WHERE id = %s AND invoice_id = %s FOR UPDATE",
        (payment_id, invoice["id"]),
    )
    row = cur.fetchone()
    if row is None:
        raise PaymentNotFound()

    status, payment_amount, already_refunded = row
    if status == "VOIDED":
        raise PaymentAlreadyVoided()

    if amount > (payment_amount - already_refunded):
        raise PaymentRefundExceedsAmount()

    cur.execute(
        """
        UPDATE payments
        SET refunded_amount = refunded_amount + %s, refund_reason = %s,
            refunded_by = %s, refunded_at = NOW()
        WHERE id = %s
        """,
        (amount, reason, staff_id, payment_id),
    )

    return get_invoice_summary_service(cur, appointment_id, staff_id=staff_id)


# ---------------------------------------------------------------------
# Receipt (master spec section 42)
# ---------------------------------------------------------------------


def get_payment_receipt_service(cur, appointment_id: int, payment_id: int):
    """One payment's receipt: the invoice's own Gross/Discount/Tax/Net
    (the whole bill's context, so the receipt shows what this payment
    was made against) alongside this specific payment's own Method/
    Transaction ID/Amount (what was actually collected in this
    transaction) -- not the invoice's cumulative paid-to-date, which a
    multi-payment bill's second receipt would otherwise misreport as
    "how much this transaction paid."
    """
    invoice = _get_invoice_for_appointment(cur, appointment_id)

    cur.execute(
        f"SELECT {', '.join(_PAYMENT_COLUMNS)} FROM payments WHERE id = %s AND invoice_id = %s",
        (payment_id, invoice["id"]),
    )
    row = cur.fetchone()
    if row is None:
        raise PaymentNotFound()
    payment = _payment_row_to_dict(row)

    cur.execute(
        f"SELECT {', '.join(_CHARGE_COLUMNS)} FROM charges WHERE invoice_id = %s AND status = 'ACTIVE' ORDER BY created_at",
        (invoice["id"],),
    )
    services = [
        {"description": c["description"], "amount": c["amount"]}
        for c in (_charge_row_to_dict(r) for r in cur.fetchall())
    ]
    gross = sum(s["amount"] for s in services)
    totals = _compute_totals(gross, invoice["discount_amount"], invoice["tax_rate"], paid_effective=0)

    cur.execute(
        """
        SELECT h.name, p.name, p.uhid, s.username
        FROM encounters e
        JOIN hospitals h ON h.id = e.hospital_id
        JOIN patients p ON p.id = e.patient_id
        JOIN staff s ON s.id = %s
        WHERE e.id = %s
        """,
        (payment["recorded_by"], invoice["encounter_id"]),
    )
    hospital_name, patient_name, patient_uhid, cashier = cur.fetchone()

    return {
        "hospital_name": hospital_name,
        "receipt_number": payment["receipt_number"],
        "patient_name": patient_name,
        "patient_uhid": patient_uhid,
        "encounter_id": invoice["encounter_id"],
        "invoice_number": invoice["invoice_number"],
        "services": services,
        "gross_amount": totals["gross_amount"],
        "discount_amount": totals["discount_amount"],
        "tax_amount": totals["tax_amount"],
        "net_amount": totals["net_amount"],
        "payment_amount": payment["amount"] - payment["refunded_amount"],
        "payment_method": payment["method"],
        "transaction_id": payment["transaction_id"],
        "payment_status": payment["status"],
        "cashier": cashier,
        "recorded_at": payment["recorded_at"],
    }
