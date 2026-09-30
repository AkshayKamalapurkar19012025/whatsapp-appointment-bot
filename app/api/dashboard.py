"""
Admin dashboard: summary stats + trend charts backing the Dashboard
landing page (frontend/src/admin/DashboardPanel.tsx). Both endpoints are
staff-readable (get_current_staff, not require_permission(...)): viewing
these counts is no more sensitive than viewing the appointments list,
which any STAFF session can already do via GET /appointments.

Appointments move through a real lifecycle (migrations/0011_appointment_
lifecycle_statuses.sql, extended by migrations/0015): PENDING ->
CONFIRMED -> CHECKED_IN -> COMPLETED, with PENDING -> REJECTED or
PENDING/CONFIRMED -> CANCELLED, or CONFIRMED -> NO_SHOW (manual
front-desk action, migrations/0015) as exits. /stats reports a count
for every one of the original six statuses plus the two time-based
cuts (today's / upcoming) already used before this lifecycle existed --
NO_SHOW is not yet broken out as its own stat here (deliberately out of
scope for the migration that introduced it; add a
no_show_appointments count the same way as the others above if/when
front-desk visibility into no-show counts is wanted).
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from app.api.staff_auth import get_current_staff
from app.db.connection import get_connection
from app.services.appointment_services import (
    EFFECTIVE_PAYMENT_JOIN_SQL,
    EFFECTIVE_PAYMENT_STATUS_SQL,
    EFFECTIVE_PAYMENT_AMOUNT_SQL,
    EFFECTIVE_PAYMENT_METHOD_SQL,
    EFFECTIVE_PAYMENT_RECORDED_AT_SQL,
)

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
)


@router.get("/stats")
def get_dashboard_stats(staff: dict = Depends(get_current_staff)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            # "Today"/"upcoming" are evaluated in each doctor's own local
            # timezone (joined in from doctors.timezone) rather than
            # server/UTC time -- the same per-doctor-local-day convention
            # GET /appointments already uses for its date_from/date_to
            # filters, just computed in SQL here since these are
            # aggregate counts rather than per-row display values. Both
            # only count PENDING/CONFIRMED -- an appointment that's
            # already Visited, Completed, Cancelled, or Rejected isn't
            # "today's" or "upcoming" in the sense a front-desk staffer
            # means by those words.
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE a.status IN ('PENDING', 'CONFIRMED')
                        AND (a.start_at AT TIME ZONE d.timezone)::date
                            = (NOW() AT TIME ZONE d.timezone)::date
                    ) AS today_appointments,
                    COUNT(*) FILTER (
                        WHERE a.status IN ('PENDING', 'CONFIRMED') AND a.start_at > NOW()
                    ) AS upcoming_appointments,
                    COUNT(*) AS total_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'PENDING') AS pending_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'CONFIRMED') AS confirmed_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'REJECTED') AS rejected_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'CANCELLED') AS cancelled_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'CHECKED_IN') AS visited_appointments,
                    COUNT(*) FILTER (WHERE a.status = 'COMPLETED') AS completed_appointments
                FROM appointments a
                JOIN doctors d ON d.id = a.doctor_id
                """
            )
            (
                today,
                upcoming,
                total,
                pending,
                confirmed,
                rejected,
                cancelled,
                visited,
                completed,
            ) = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM doctors WHERE active = TRUE")
            (total_doctors,) = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM patients")
            (total_patients,) = cur.fetchone()

    return {
        "today_appointments": today,
        "upcoming_appointments": upcoming,
        "total_appointments": total,
        "pending_appointments": pending,
        "confirmed_appointments": confirmed,
        "rejected_appointments": rejected,
        "cancelled_appointments": cancelled,
        "visited_appointments": visited,
        "completed_appointments": completed,
        "total_doctors": total_doctors,
        "total_patients": total_patients,
    }


@router.get("/trends")
def get_dashboard_trends(
    days: int = Query(default=14, ge=7, le=90),
    staff: dict = Depends(get_current_staff),
):
    """
    Two daily-count series for the Dashboard's "Appointment Trends" and
    "Patient Registration" charts: appointments created and patients
    registered per calendar day over the trailing `days` days (default
    14), oldest first, zero-filled for days with no activity.

    Both series bucket by created_at's UTC calendar date. Unlike /stats'
    today/upcoming counts, this deliberately does NOT convert to each
    doctor's local timezone -- a trend chart's day boundary being off by
    a few hours for some doctors doesn't change the shape of the line,
    and a single shared UTC axis is what lets the two series (patients
    have no per-row timezone at all) share one set of x-axis labels.
    """
    window_start = date.today() - timedelta(days=days - 1)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d::date, COUNT(a.id)
                FROM generate_series(%s::date, CURRENT_DATE, INTERVAL '1 day') AS d
                LEFT JOIN appointments a ON a.created_at::date = d::date
                GROUP BY d
                ORDER BY d
                """,
                (window_start,),
            )
            appointment_counts = cur.fetchall()

            cur.execute(
                """
                SELECT d::date, COUNT(p.id)
                FROM generate_series(%s::date, CURRENT_DATE, INTERVAL '1 day') AS d
                LEFT JOIN patients p ON p.created_at::date = d::date
                GROUP BY d
                ORDER BY d
                """,
                (window_start,),
            )
            patient_counts = cur.fetchall()

    return {
        "appointments": [{"date": day.isoformat(), "count": count} for day, count in appointment_counts],
        "patients": [{"date": day.isoformat(), "count": count} for day, count in patient_counts],
    }


def _ledger_b_collections_by_method(cur, window_start):
    """Ledger B's (invoices/charges/payments, migrations/0033) side of
    "money actually collected" -- lab/radiology/procedure/pharmacy/
    package payments, which Ledger A's own collections query would
    otherwise double-count for the consultation fee.

    Phase 10 (Billing Ledger Unification, migrations/0058_billing_
    ledger_unification.sql) made the consultation fee itself a real
    Ledger B charge/payment (linked back via appointments.
    consultation_payment_id), and Ledger A's own query above already
    reads through that link via EFFECTIVE_PAYMENT_AMOUNT_SQL -- so this
    function excludes exactly that one payment per appointment (there is
    no source_type column on payments themselves, only on charges, so
    the exclusion has to go via the link rather than a source_type
    filter). Without this exclusion every real consultation-fee payment
    would be counted twice: once here, once via Ledger A's join."""
    cur.execute(
        """
        SELECT method, COUNT(*), COALESCE(SUM(amount - refunded_amount), 0)
        FROM payments
        WHERE status = 'COMPLETED'
          AND recorded_at::date >= %s
          AND id NOT IN (SELECT consultation_payment_id FROM appointments WHERE consultation_payment_id IS NOT NULL)
        GROUP BY method
        ORDER BY method
        """,
        (window_start,),
    )
    return cur.fetchall()


def _ledger_b_collections_by_doctor(cur, window_start):
    """Same as above, attributed by encounters.doctor_id (every invoice
    belongs to exactly one encounter, and every encounter has a
    doctor_id directly -- migrations/0028_encounters.sql -- no need to
    go through appointments). Same consultation_payment_id exclusion as
    _ledger_b_collections_by_method, same reason."""
    cur.execute(
        """
        SELECT e.doctor_id, d.name, COUNT(*), COALESCE(SUM(pay.amount - pay.refunded_amount), 0)
        FROM payments pay
        JOIN invoices inv ON inv.id = pay.invoice_id
        JOIN encounters e ON e.id = inv.encounter_id
        JOIN doctors d ON d.id = e.doctor_id
        WHERE pay.status = 'COMPLETED'
          AND pay.recorded_at::date >= %s
          AND pay.id NOT IN (SELECT consultation_payment_id FROM appointments WHERE consultation_payment_id IS NOT NULL)
        GROUP BY e.doctor_id, d.name
        ORDER BY d.name
        """,
        (window_start,),
    )
    return cur.fetchall()


def _ledger_b_outstanding(cur):
    """Ledger B's side of "what's owed right now" -- every OPEN invoice
    with a positive balance, regardless of whether its encounter is
    still open (an ongoing visit can already owe money for a dispensed
    prescription) or closed. Not time-scoped, matching Ledger A's own
    outstanding_unpaid semantics below. Same gross/discount/tax/paid
    formula app/services/exception_engine.py's _payment_pending uses,
    duplicated rather than imported -- that function additionally
    requires the encounter to be CLOSED (a different question: "is this
    overdue enough to flag as an exception" vs. this endpoint's "what's
    the current outstanding total"), so it isn't a drop-in reuse.

    Excludes source_type = 'CONSULTATION' charges from the gross sum
    (Ledger A's own outstanding_rows query above, via
    EFFECTIVE_PAYMENT_STATUS_SQL, already reports an unpaid consultation
    fee), and the linked consultation_payment_id from the paid sum, so
    a real consultation-fee charge/payment is never double-counted --
    same reasoning as the collections queries above."""
    cur.execute(
        """
        SELECT inv.encounter_id, p.name, d.name,
               COALESCE(gross.amount, 0) AS gross_amount,
               inv.discount_amount, inv.tax_rate,
               COALESCE(paid.amount, 0) AS paid_amount,
               inv.created_at
        FROM invoices inv
        JOIN encounters e ON e.id = inv.encounter_id
        JOIN patients p ON p.id = e.patient_id
        JOIN doctors d ON d.id = e.doctor_id
        LEFT JOIN LATERAL (
            SELECT SUM(amount) AS amount FROM charges
            WHERE invoice_id = inv.id AND status = 'ACTIVE' AND source_type <> 'CONSULTATION'
        ) gross ON TRUE
        LEFT JOIN LATERAL (
            SELECT SUM(amount - refunded_amount) AS amount FROM payments
            WHERE invoice_id = inv.id AND status = 'COMPLETED'
              AND id NOT IN (SELECT consultation_payment_id FROM appointments WHERE consultation_payment_id IS NOT NULL)
        ) paid ON TRUE
        WHERE inv.status = 'OPEN'
        """
    )
    rows = []
    for encounter_id, patient_name, doctor_name, gross, discount, tax_rate, paid, created_at in cur.fetchall():
        taxable = max(gross - discount, 0)
        net_amount = taxable + round(taxable * tax_rate / 100, 2)
        balance = net_amount - paid
        if balance <= 0:
            continue
        rows.append((encounter_id, patient_name, doctor_name, balance, created_at))
    return rows


@router.get("/billing")
def get_billing_report(
    days: int = Query(default=14, ge=1, le=90),
    staff: dict = Depends(get_current_staff),
):
    """
    OPD billing reconciliation view backing the real, shipped "Billing"
    panel (frontend/src/admin/BillingPanel.tsx -- see its own header
    comment: wired up after this endpoint was built, this docstring's
    older "no frontend for this yet" claim was stale).

    This endpoint and the Exception Engine's PAYMENT_PENDING check
    (app/services/exception_engine.py) both close the billing-ledger
    split (docs/architecture/BILLING_LEDGERS.md,
    docs/architecture/BILLING_LEDGER_UNIFICATION.md) by combining
    Ledger A (appointments, via EFFECTIVE_PAYMENT_*_SQL -- which itself
    reads through to the real Ledger B consultation-fee charge/payment
    when one exists, migrations/0058_billing_ledger_unification.sql)
    and Ledger B (invoices/charges/payments, migrations/0033, every
    non-consultation charge) at *read* time -- Phase 9's "Option C".
    Neither write path is touched by this endpoint.

    Every Ledger B query below excludes the consultation fee's own
    charge/payment (source_type = 'CONSULTATION', or the specific
    payment id linked via appointments.consultation_payment_id):
    Ledger A's own query already counts it via EFFECTIVE_PAYMENT_*_SQL,
    so counting it again here would double it into this report. (An
    earlier iteration of this endpoint also combined against a
    separate ADR-009 mirror of the consultation fee into Ledger B --
    migrations/0058_consultation_fee_ledger_mirror.sql -- superseded by
    this real Ledger B write path and removed; that migration's now-
    unused schema is left in place, immutable once applied, but nothing
    reads or writes it any more.)

    Four independent pieces, not one combined query -- each answers a
    different front-desk question and has a different natural time
    scope:
      * collections: money actually collected (PAID/COMPLETED on either
        ledger), bucketed by payment_method and by doctor, over the
        trailing `days` days (recorded-at-scoped -- when it was
        collected, not when the appointment was scheduled).
      * outstanding: what's currently owed right now, on either ledger --
        every CHECKED_IN appointment still UNPAID/FAILED on Ledger A,
        plus every OPEN invoice with a positive balance on Ledger B
        (excluding the consultation-fee charge, per above).
        Deliberately NOT time-scoped by `days`: "who owes money today"
        means everyone outstanding, not just the ones from this window.
      * waivers: Ledger A only -- a waiver is never given a Ledger B
        charge at all (migrations/0059_billing_ledger_backfill.sql's
        own reasoning: charges.amount has CHECK (amount > 0), and
        nothing was ever billed for a waived fee), so there's nothing
        to combine here. No dollar total -- waive_consultation_fee_service
        and settle_free_visit_service both record payment_amount = 0
        for a waived visit (see their docstrings), so there is no real
        "amount waived" number to report; fabricating one from
        doctor_appointment_types.consultation_fee's *current* price
        would misrepresent what was actually waived at the time.
      * refunds: Ledger A only, same reason as waivers -- count + total
        refund_amount for the trailing `days` days (refund_amount is
        actually recorded, unlike waivers, so a real total is
        reportable here).
    """
    window_start = date.today() - timedelta(days=days - 1)

    with get_connection() as conn:
        with conn.cursor() as cur:
            # Phase 10 (Billing Ledger Unification): payment_status/
            # payment_amount/payment_method/payment_recorded_at are no
            # longer written for a payment recorded after this phase
            # (see app/services/appointment_services.py's EFFECTIVE_
            # PAYMENT_*_SQL and record_payment_service's own docstring)
            # -- every query below that reasons about PAID/UNPAID/FAILED
            # or sums payment_amount uses the effective-payment fragment
            # instead of the bare column, so a new payment shows up here
            # exactly as a legacy one always did. WAIVED/REFUNDED below
            # are untouched: waivers never move to ledger 2 at all, and
            # a refund still mirrors payment_status/refund_* onto these
            # same legacy columns (see record_refund_service).
            cur.execute(
                f"""
                SELECT {EFFECTIVE_PAYMENT_METHOD_SQL}, COUNT(*), COALESCE(SUM({EFFECTIVE_PAYMENT_AMOUNT_SQL}), 0)
                FROM appointments a
                {EFFECTIVE_PAYMENT_JOIN_SQL}
                WHERE {EFFECTIVE_PAYMENT_STATUS_SQL} = 'PAID'
                  AND {EFFECTIVE_PAYMENT_RECORDED_AT_SQL}::date >= %s
                GROUP BY 1
                ORDER BY 1
                """,
                (window_start,),
            )
            ledger_a_by_method = cur.fetchall()
            ledger_b_by_method = _ledger_b_collections_by_method(cur, window_start)

            cur.execute(
                f"""
                SELECT d.id, d.name, COUNT(*), COALESCE(SUM({EFFECTIVE_PAYMENT_AMOUNT_SQL}), 0)
                FROM appointments a
                JOIN doctors d ON d.id = a.doctor_id
                {EFFECTIVE_PAYMENT_JOIN_SQL}
                WHERE {EFFECTIVE_PAYMENT_STATUS_SQL} = 'PAID'
                  AND {EFFECTIVE_PAYMENT_RECORDED_AT_SQL}::date >= %s
                GROUP BY d.id, d.name
                ORDER BY d.name
                """,
                (window_start,),
            )
            ledger_a_by_doctor = cur.fetchall()
            ledger_b_by_doctor = _ledger_b_collections_by_doctor(cur, window_start)

            cur.execute(
                f"""
                SELECT a.id, p.name, d.name, {EFFECTIVE_PAYMENT_STATUS_SQL}, a.visited_at
                FROM appointments a
                JOIN patients p ON p.id = a.patient_id
                JOIN doctors d ON d.id = a.doctor_id
                {EFFECTIVE_PAYMENT_JOIN_SQL}
                WHERE a.status = 'CHECKED_IN'
                  AND {EFFECTIVE_PAYMENT_STATUS_SQL} IN ('UNPAID', 'FAILED')
                ORDER BY a.visited_at
                """
            )
            outstanding_rows = cur.fetchall()
            ledger_b_outstanding_rows = _ledger_b_outstanding(cur)

            # Deliberately NOT rewritten to Ledger B, unlike collections/
            # outstanding/refunds above: a waiver (real fee or a
            # genuinely free $0 visit) never gets a Ledger B charge at
            # all -- charges.amount has CHECK (amount > 0), and
            # migrations/0059_billing_ledger_backfill.sql's own
            # reasoning is the same going forward: nothing was ever
            # billed for a waived fee, so there is nothing to bill. A
            # Ledger-B-only query would silently miss every waiver.
            # appointments.payment_status = 'WAIVED' already correctly
            # captures both waiver paths (a real fee waived, and a free
            # visit settled) in one place.
            cur.execute(
                """
                SELECT a.id, p.name, d.name, a.waive_reason, a.payment_recorded_at
                FROM appointments a
                JOIN patients p ON p.id = a.patient_id
                JOIN doctors d ON d.id = a.doctor_id
                WHERE a.payment_status = 'WAIVED'
                  AND a.payment_recorded_at::date >= %s
                ORDER BY a.payment_recorded_at DESC
                """,
                (window_start,),
            )
            waiver_rows = cur.fetchall()

            cur.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(refund_amount), 0)
                FROM appointments
                WHERE payment_status = 'REFUNDED'
                  AND refunded_at::date >= %s
                """,
                (window_start,),
            )
            refund_count, refund_total = cur.fetchone()

            cur.execute(
                """
                SELECT a.id, p.name, d.name, a.refund_amount, a.refund_reason, a.refunded_at
                FROM appointments a
                JOIN patients p ON p.id = a.patient_id
                JOIN doctors d ON d.id = a.doctor_id
                WHERE a.payment_status = 'REFUNDED'
                  AND a.refunded_at::date >= %s
                ORDER BY a.refunded_at DESC
                """,
                (window_start,),
            )
            refund_rows = cur.fetchall()

    # Merge each Ledger A / Ledger B pair by their shared key (method,
    # or doctor_id) -- a method/doctor that only collected on one
    # ledger in this window still appears once, with the other side's
    # contribution simply 0, rather than two separate rows.
    by_method: dict[str, dict] = {}
    for method, count, amount in ledger_a_by_method:
        by_method[method] = {"count": count, "amount": amount}
    for method, count, amount in ledger_b_by_method:
        entry = by_method.setdefault(method, {"count": 0, "amount": 0})
        entry["count"] += count
        entry["amount"] += amount

    by_doctor: dict[int, dict] = {}
    for doctor_id, doctor_name, count, amount in ledger_a_by_doctor:
        by_doctor[doctor_id] = {"doctor_name": doctor_name, "count": count, "amount": amount}
    for doctor_id, doctor_name, count, amount in ledger_b_by_doctor:
        entry = by_doctor.setdefault(doctor_id, {"doctor_name": doctor_name, "count": 0, "amount": 0})
        entry["count"] += count
        entry["amount"] += amount

    ledger_a_total = sum(amount for _, _, amount in ledger_a_by_method)
    ledger_b_total = sum(amount for _, _, amount in ledger_b_by_method)

    combined_outstanding = [
        {
            "encounter_id": None,
            "appointment_id": row[0],
            "patient_name": row[1],
            "doctor_name": row[2],
            "source": "CONSULTATION_FEE",
            "payment_status": row[3],
            "balance": None,  # Ledger A never stored a partial-payment amount -- UNPAID/FAILED owes the full consultation_fee, looked up separately (get_appointment_charge) if the exact figure is needed.
            "since": row[4].isoformat() if row[4] else None,
        }
        for row in outstanding_rows
    ] + [
        {
            "encounter_id": encounter_id,
            "appointment_id": None,
            "patient_name": patient_name,
            "doctor_name": doctor_name,
            "source": "ITEMIZED_BILL",
            "payment_status": None,
            "balance": float(balance),
            "since": created_at.isoformat() if created_at else None,
        }
        for encounter_id, patient_name, doctor_name, balance, created_at in ledger_b_outstanding_rows
    ]

    return {
        "window_days": days,
        "collections_by_method": [
            {"method": method, "count": v["count"], "amount": v["amount"]}
            for method, v in sorted(by_method.items(), key=lambda kv: kv[0] or "")
        ],
        "collections_by_doctor": [
            {"doctor_id": doctor_id, "doctor_name": v["doctor_name"], "count": v["count"], "amount": v["amount"]}
            for doctor_id, v in sorted(by_doctor.items(), key=lambda kv: kv[1]["doctor_name"])
        ],
        "total_collected": ledger_a_total + ledger_b_total,
        # Phase 9, Option C: the two ledgers stay individually visible
        # here even though the totals/breakdowns above are combined --
        # see this endpoint's own docstring.
        "ledger_breakdown": {
            "consultation_fee": ledger_a_total,
            "itemized_billing": ledger_b_total,
        },
        "outstanding_unpaid": combined_outstanding,
        "waivers": {
            "count": len(waiver_rows),
            "records": [
                {
                    "appointment_id": row[0],
                    "patient_name": row[1],
                    "doctor_name": row[2],
                    "reason": row[3],
                    "waived_at": row[4].isoformat() if row[4] else None,
                }
                for row in waiver_rows
            ],
        },
        "refunds": {
            "count": refund_count,
            "total_refunded": refund_total,
            "records": [
                {
                    "appointment_id": row[0],
                    "patient_name": row[1],
                    "doctor_name": row[2],
                    "refund_amount": row[3],
                    "refund_reason": row[4],
                    "refunded_at": row[5].isoformat() if row[5] else None,
                }
                for row in refund_rows
            ],
        },
    }
