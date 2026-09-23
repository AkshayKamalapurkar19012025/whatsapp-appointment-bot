"""
Tests for GET /api/analytics/waiting-time (master spec audit
"subsequent gaps" list, screen 33): historical wait-time trend, from
appointments.visited_at (check-in) to consultations.started_at (doctor
opens the consultation), bucketed by day and by doctor -- the
counterpart to AppointmentsPanel.tsx's own live "currently waiting"
snapshot.
"""

from datetime import date, datetime, timedelta, timezone

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _checked_in_appointment(client, db_connection, headers, doctor_name: str, patient_name: str) -> dict:
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name=doctor_name,
        department_name=f"{doctor_name} Dept",
        appointment_type_name=f"{doctor_name} Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": patient_name, "whatsapp_number": f"+9197{abs(hash(patient_name)) % 10**8:08d}"},
        headers=headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T09:00:00+05:30",
        },
        headers=headers,
    ).json()
    response = client.post(f"/api/appointments/{created['id']}/confirm-and-checkin", headers=headers)
    assert response.status_code == 200
    return {"doctor_id": seeded["doctor_id"], "patient": patient, "appointment": created}


def _backdate_visited_at(db_connection, appointment_id: int, minutes_ago: int) -> None:
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET visited_at = %s WHERE id = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago), appointment_id),
        )
    db_connection.commit()


def _start_consultation(client, headers, appointment_id: int) -> None:
    response = client.get(f"/api/appointments/{appointment_id}/consultation", headers=headers)
    assert response.status_code == 200


def test_waiting_time_analytics_computes_average_from_visited_at_to_consultation_start(client, db_connection):
    headers = create_admin_and_get_headers(db_connection)
    ctx = _checked_in_appointment(client, db_connection, headers, "Dr. Wait Time", "Wait Time Patient")
    appointment_id = ctx["appointment"]["id"]

    _backdate_visited_at(db_connection, appointment_id, minutes_ago=10)
    _start_consultation(client, headers, appointment_id)

    response = client.get("/api/analytics/waiting-time", params={"doctor_id": ctx["doctor_id"]}, headers=headers)
    assert response.status_code == 200
    body = response.json()

    assert body["overall"]["count"] == 1
    assert 8 <= body["overall"]["avg_wait_minutes"] <= 12

    today_bucket = next(b for b in body["by_day"] if b["date"] == date.today().isoformat())
    assert today_bucket["count"] == 1
    assert 8 <= today_bucket["avg_wait_minutes"] <= 12

    doctor_bucket = next(b for b in body["by_doctor"] if b["doctor_id"] == ctx["doctor_id"])
    assert doctor_bucket["doctor_name"] == "Dr. Wait Time"
    assert doctor_bucket["count"] == 1


def test_waiting_time_analytics_filters_by_doctor(client, db_connection):
    headers = create_admin_and_get_headers(db_connection)
    ctx_a = _checked_in_appointment(client, db_connection, headers, "Dr. Wait Filter A", "Wait Filter Patient A")
    ctx_b = _checked_in_appointment(client, db_connection, headers, "Dr. Wait Filter B", "Wait Filter Patient B")

    _backdate_visited_at(db_connection, ctx_a["appointment"]["id"], minutes_ago=5)
    _start_consultation(client, headers, ctx_a["appointment"]["id"])
    _backdate_visited_at(db_connection, ctx_b["appointment"]["id"], minutes_ago=20)
    _start_consultation(client, headers, ctx_b["appointment"]["id"])

    response = client.get("/api/analytics/waiting-time", params={"doctor_id": ctx_a["doctor_id"]}, headers=headers)
    body = response.json()
    doctor_ids = {b["doctor_id"] for b in body["by_doctor"]}
    assert doctor_ids == {ctx_a["doctor_id"]}


def test_waiting_time_analytics_excludes_patients_not_yet_seen(client, db_connection):
    headers = create_admin_and_get_headers(db_connection)
    ctx = _checked_in_appointment(client, db_connection, headers, "Dr. Wait Not Seen", "Wait Not Seen Patient")

    response = client.get("/api/analytics/waiting-time", params={"doctor_id": ctx["doctor_id"]}, headers=headers)
    body = response.json()
    assert body["overall"]["count"] == 0
    assert body["overall"]["avg_wait_minutes"] == 0


def test_waiting_time_analytics_requires_authentication(client, db_connection):
    response = client.get("/api/analytics/waiting-time")
    assert response.status_code == 401
