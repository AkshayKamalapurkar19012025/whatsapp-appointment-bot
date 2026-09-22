"""
Visit completion checklist (OPD/HIMS master spec section 43): "Complete
OPD Visit" existed only as a single status-flip action
(POST /appointments/{id}/complete) with no precondition summary shown to
staff before or after clicking it -- a visit could be marked completed
with no orders, no billing, and no payment, and nothing surfaced that
(docs/OPD_HIMS_MASTER_SPEC_AUDIT.md section 43).

Deliberately a read-only summary, not a gate: mark_completed_service
(app/services/appointment_services.py) is completely unchanged by this
-- staff can still complete a visit with every item below unchecked,
same as today, matching the spec's own wording ("checklist", not
"blocker"). Phase 11's Exception Engine already separately, continuously
flags "billing not started"/"payment pending" as ongoing alerts; this is
the different, complementary, point-in-time summary the spec also asks
for, shown right before/after the one action that closes out a visit.

Same "read-only aggregation over existing tables, no new migration"
shape as app/services/patient_timeline_service.py.
"""

from app.services.clinical_services import (
    get_appointment_status_and_doctor,
    get_encounter_id_for_appointment,
)


def get_visit_completion_checklist_service(cur, appointment_id: int) -> dict:
    get_appointment_status_and_doctor(cur, appointment_id)
    encounter_id = get_encounter_id_for_appointment(cur, appointment_id)

    cur.execute(
        "SELECT status, follow_up_date FROM consultations WHERE encounter_id = %s",
        (encounter_id,),
    )
    consultation_row = cur.fetchone()
    consultation_completed = consultation_row is not None and consultation_row[0] == "COMPLETED"
    follow_up_scheduled = consultation_row is not None and consultation_row[1] is not None

    cur.execute("SELECT EXISTS(SELECT 1 FROM orders WHERE encounter_id = %s)", (encounter_id,))
    (orders_created,) = cur.fetchone()

    cur.execute(
        """
        SELECT EXISTS(
            SELECT 1 FROM prescription_items pi
            JOIN prescriptions p ON p.id = pi.prescription_id
            WHERE p.encounter_id = %s
        )
        """,
        (encounter_id,),
    )
    (prescription_created,) = cur.fetchone()

    # "Billing completed" (an invoice with real charges on it) is
    # deliberately a different question from "payment completed" below
    # -- the spec lists them as two separate checklist lines, and this
    # app already keeps them as two different signals: charges/invoices
    # (Phase 9's newer bill/charge/payment model) versus
    # appointments.payment_status (the older, still-live field the
    # front-desk queue already gates "Mark completed" on -- see
    # AppointmentActions.tsx).
    cur.execute(
        """
        SELECT EXISTS(
            SELECT 1 FROM charges c
            JOIN invoices i ON i.id = c.invoice_id
            WHERE i.encounter_id = %s AND c.status = 'ACTIVE'
        )
        """,
        (encounter_id,),
    )
    (billing_completed,) = cur.fetchone()

    cur.execute("SELECT payment_status FROM appointments WHERE id = %s", (appointment_id,))
    (payment_status,) = cur.fetchone()
    payment_completed = payment_status in ("PAID", "WAIVED")

    return {
        "consultation_completed": consultation_completed,
        "orders_created": orders_created,
        "prescription_created": prescription_created,
        "billing_completed": billing_completed,
        "payment_completed": payment_completed,
        "follow_up_scheduled": follow_up_scheduled,
    }
