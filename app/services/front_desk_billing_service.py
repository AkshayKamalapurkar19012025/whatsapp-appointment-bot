"""
Front-desk consultation-fee actions that put a patient in the queue,
composed from the existing pieces and moved out of app/api/appointments.py
(POST /{id}/payment, /waive-payment, /settle-free-visit) so the AI agent's
tools perform exactly what those routes do -- the same service call, the
same audit_log row, the same queue-token notification -- with no second
implementation of any billing or queue rule.

A queue token is issued ONLY by these three actions (record_payment_service
PAID, waive_consultation_fee_service, settle_free_visit_service), never by
check-in; see generate_queue_token_service. That is why the agent has no
standalone "generate token" tool.
"""

from app.services.appointment_services import (
    record_payment_service,
    settle_free_visit_service,
    waive_consultation_fee_service,
)
from app.services.audit_log import record_audit_log
from app.services.notifications import KIND_QUEUE_TOKEN, send_mock_notification


def notify_queue_token(cur, appointment_id: int, token_number: int) -> None:
    """The one-time "you're in the queue, token N" message, sent only when
    a token was newly issued (a double-click must not re-notify)."""
    cur.execute(
        """
        SELECT p.whatsapp_number, p.name, d.name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        JOIN doctors d ON d.id = a.doctor_id
        WHERE a.id = %s
        """,
        (appointment_id,),
    )
    patient_number, patient_name, doctor_name = cur.fetchone()
    send_mock_notification(
        cur,
        patient_number,
        KIND_QUEUE_TOKEN,
        f"Hi {patient_name}, you're checked in successfully. "
        f"Queue Token: {token_number}. Status: Waiting for Doctor "
        f"({doctor_name}).",
    )


def front_desk_record_payment_service(
    cur, appointment_id: int, *, method: str, outcome: str, staff_id: int, hospital_id: int
) -> dict:
    result = record_payment_service(cur, appointment_id, method=method, outcome=outcome, staff_id=staff_id)
    record_audit_log(
        cur,
        hospital_id=hospital_id,
        staff_id=staff_id,
        action="bill.record_payment",
        resource_type="appointment",
        resource_id=appointment_id,
        details={"method": method, "outcome": outcome},
    )
    if result["token_just_issued"]:
        notify_queue_token(cur, appointment_id, result["token_number"])
    return result


def front_desk_waive_fee_service(
    cur, appointment_id: int, *, reason: str, staff_id: int, hospital_id: int
) -> dict:
    result = waive_consultation_fee_service(cur, appointment_id, reason=reason, staff_id=staff_id)
    record_audit_log(
        cur,
        hospital_id=hospital_id,
        staff_id=staff_id,
        action="appointment.waive_payment",
        resource_type="appointment",
        resource_id=appointment_id,
        details={"reason": reason},
    )
    if result["token_just_issued"]:
        notify_queue_token(cur, appointment_id, result["token_number"])
    return result


def front_desk_settle_free_visit_service(cur, appointment_id: int) -> dict:
    """No audit row: the route never wrote one (there is no discretion in
    this action -- the service refuses anything with a nonzero fee)."""
    result = settle_free_visit_service(cur, appointment_id)
    if result["token_just_issued"]:
        notify_queue_token(cur, appointment_id, result["token_number"])
    return result
