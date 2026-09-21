"""
Public, unauthenticated waiting-room display board -- a lobby TV or
tablet cycling through "Now Serving #X" per doctor. Deliberately the one
queue-reading endpoint in this app with no auth dependency: it's meant to
be left open in a public space, so the response is scoped to exactly
what's safe to show a room full of strangers -- a doctor's name and a
bare token number, nothing that identifies which patient that number
belongs to (no patient name, phone, or appointment id). Every other
queue detail (GET /doctors/{id}/queue and its patient list) stays behind
get_current_staff, unchanged.
"""

from fastapi import APIRouter

from app.db.connection import get_connection
from app.services.appointment_services import get_now_serving_token_service

router = APIRouter(prefix="/public", tags=["Public"])


@router.get("/queue-display")
def get_queue_display():
    """Every active doctor's current now-serving token, for a lobby
    display cycling through all of them at once. A doctor with nobody
    currently being served (queue empty, or everyone held) reports
    now_serving_token: null rather than being omitted -- the display
    still shows their name, just with no number lit up."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, timezone FROM doctors WHERE active = TRUE ORDER BY name"
            )
            doctors = cur.fetchall()

            return [
                {
                    "doctor_id": doctor_id,
                    "doctor_name": doctor_name,
                    "now_serving_token": get_now_serving_token_service(cur, doctor_id, doctor_tz),
                }
                for doctor_id, doctor_name, doctor_tz in doctors
            ]
