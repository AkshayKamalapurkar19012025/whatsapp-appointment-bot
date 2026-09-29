"""
Front-desk check-in, composed from the existing pieces and moved out of
app/api/appointments.py's POST /appointments/{id}/visit so the AI agent's
appointment.check_in tool performs exactly what that route does -- the
same state transition (mark_visited_service, unchanged) and the same two
notifications -- with no second implementation of check-in.

Deliberately does NOT generate a queue token: since the patient-arrival
workflow's Phase 4 a token is issued only when the consultation fee is
paid or waived (see mark_visited_service's docstring), so a checked-in
appointment legitimately has token_number NULL here.
"""

from app.services.appointment_services import mark_visited_service
from app.services.notification_center_service import create_notification
from app.services.notifications import KIND_CHECK_IN, send_mock_notification


def front_desk_check_in_service(cur, appointment_id: int, *, hospital_id: int) -> dict:
    result = mark_visited_service(cur, appointment_id)

    # Staff-initiated check-in notification (migrations/0012). No token
    # number here -- see the module docstring. Not a duplicate of
    # anything: unlike a WhatsApp-driven action, the patient isn't
    # mid-chat with the bot when staff check them in at the front desk
    # (see notifications.py's KIND_CHECK_IN note).
    cur.execute(
        """
        SELECT p.whatsapp_number, p.name, d.name
        FROM patients p, doctors d
        WHERE p.id = %s AND d.id = %s
        """,
        (result["patient_id"], result["doctor_id"]),
    )
    patient_number, patient_name, doctor_name = cur.fetchone()
    send_mock_notification(
        cur,
        patient_number,
        KIND_CHECK_IN,
        f"Hi {patient_name}, you're checked in with {doctor_name}. "
        f"Please complete registration and payment at the front desk.",
    )
    create_notification(
        cur,
        hospital_id=hospital_id,
        kind="PATIENT_ARRIVED",
        message=f"{patient_name} has arrived for {doctor_name}",
        appointment_id=appointment_id,
    )

    return result
