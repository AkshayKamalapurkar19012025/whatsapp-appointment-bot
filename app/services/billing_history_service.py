"""
Billing/Payment History (OPD/HIMS master spec audit "subsequent gaps"
list, screens 29-30 of its screen inventory table): "Per-visit billing
exists; no dedicated cross-visit billing-history screen" / "Per-visit
payments list exists; no dedicated cross-visit payment-history screen."

Read-only, paginated, hospital-scoped listings across every invoice/
payment -- not a new billing model, just a browsable view over the
existing invoices/charges/payments tables (migrations/0033_billing_
invoices.sql). Reuses billing_services._compute_totals for the exact
same gross/discount/tax/net/paid/balance/payment_status math every
per-visit bill screen already uses, computed from one bulk aggregate
query per page (not one query per invoice -- master spec section 80's
own "avoid load entire table"/N+1 warning, same discipline
app/services/patient_timeline_service.py's own docstring already
follows).

payment_status is deliberately not a filter here: it's computed from
gross/discount/tax/paid at read time, not a stored column, so
filtering by it would mean computing every matching row before paging
(defeating the point of LIMIT/OFFSET) or an inconsistent total count.
A real payment_status filter would need it promoted to a stored,
indexed column -- out of scope for a read-only history view.
"""

from app.services.billing_services import _compute_totals

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def list_invoices_service(
    cur,
    *,
    hospital_id: int,
    patient_name: str | None = None,
    date_from=None,
    date_to=None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    where_clauses = ["e.hospital_id = %s"]
    params: list = [hospital_id]
    if patient_name and patient_name.strip():
        where_clauses.append("p.name ILIKE %s")
        params.append(f"%{patient_name.strip()}%")
    if date_from is not None:
        where_clauses.append("i.created_at >= %s")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("i.created_at <= %s")
        params.append(date_to)
    where_sql = " AND ".join(where_clauses)

    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM invoices i
        JOIN encounters e ON e.id = i.encounter_id
        JOIN patients p ON p.id = e.patient_id
        WHERE {where_sql}
        """,
        params,
    )
    (total,) = cur.fetchone()

    cur.execute(
        f"""
        SELECT i.id, i.invoice_number, i.status, i.discount_amount, i.tax_rate, i.created_at,
               p.id, p.name, p.uhid, d.name, a.id,
               COALESCE(SUM(c.amount) FILTER (WHERE c.status = 'ACTIVE'), 0),
               COALESCE(SUM(pay.amount - pay.refunded_amount) FILTER (WHERE pay.status = 'COMPLETED'), 0)
        FROM invoices i
        JOIN encounters e ON e.id = i.encounter_id
        JOIN patients p ON p.id = e.patient_id
        JOIN appointments a ON a.encounter_id = e.id
        JOIN doctors d ON d.id = a.doctor_id
        LEFT JOIN charges c ON c.invoice_id = i.id
        LEFT JOIN payments pay ON pay.invoice_id = i.id
        WHERE {where_sql}
        GROUP BY i.id, p.id, p.name, p.uhid, d.name, a.id
        ORDER BY i.created_at DESC
        LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    )
    items = []
    for row in cur.fetchall():
        (
            invoice_id, invoice_number, status, discount_amount, tax_rate, created_at,
            patient_id, patient_name_, patient_uhid, doctor_name, appointment_id,
            gross, paid_effective,
        ) = row
        totals = _compute_totals(gross, discount_amount, tax_rate, paid_effective)
        items.append(
            {
                "id": invoice_id,
                "invoice_number": invoice_number,
                "status": status,
                "created_at": created_at.isoformat(),
                "patient_id": patient_id,
                "patient_name": patient_name_,
                "patient_uhid": patient_uhid,
                "doctor_name": doctor_name,
                "appointment_id": appointment_id,
                **totals,
            }
        )

    return {"items": items, "total": total, "limit": limit, "offset": offset}


def list_payments_service(
    cur,
    *,
    hospital_id: int,
    patient_name: str | None = None,
    date_from=None,
    date_to=None,
    method: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    where_clauses = ["e.hospital_id = %s"]
    params: list = [hospital_id]
    if patient_name and patient_name.strip():
        where_clauses.append("p.name ILIKE %s")
        params.append(f"%{patient_name.strip()}%")
    if date_from is not None:
        where_clauses.append("pay.recorded_at >= %s")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("pay.recorded_at <= %s")
        params.append(date_to)
    if method:
        where_clauses.append("pay.method = %s")
        params.append(method)
    where_sql = " AND ".join(where_clauses)

    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM payments pay
        JOIN invoices i ON i.id = pay.invoice_id
        JOIN encounters e ON e.id = i.encounter_id
        JOIN patients p ON p.id = e.patient_id
        WHERE {where_sql}
        """,
        params,
    )
    (total,) = cur.fetchone()

    cur.execute(
        f"""
        SELECT pay.id, pay.receipt_number, pay.amount, pay.method, pay.status,
               pay.refunded_amount, pay.recorded_at,
               p.id, p.name, p.uhid, i.invoice_number, a.id
        FROM payments pay
        JOIN invoices i ON i.id = pay.invoice_id
        JOIN encounters e ON e.id = i.encounter_id
        JOIN patients p ON p.id = e.patient_id
        JOIN appointments a ON a.encounter_id = e.id
        WHERE {where_sql}
        ORDER BY pay.recorded_at DESC
        LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    )
    items = [
        {
            "id": r[0],
            "receipt_number": r[1],
            "amount": r[2],
            "method": r[3],
            "status": r[4],
            "refunded_amount": r[5],
            "recorded_at": r[6].isoformat(),
            "patient_id": r[7],
            "patient_name": r[8],
            "patient_uhid": r[9],
            "invoice_number": r[10],
            "appointment_id": r[11],
        }
        for r in cur.fetchall()
    ]

    return {"items": items, "total": total, "limit": limit, "offset": offset}
