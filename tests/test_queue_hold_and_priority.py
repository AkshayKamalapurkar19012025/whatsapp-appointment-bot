"""
Tests for the queue hold/recall/priority actions (migrations/0035):
POST /api/appointments/{id}/queue/hold, /recall, /priority. All three
only operate on a ticketed (token_number IS NOT NULL) CHECKED_IN
appointment -- see app/services/appointment_services.py's
hold_queue_entry_service/recall_queue_entry_service/set_priority_service.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _set_start_at(db_connection, appointment_id: int, start_at: datetime, end_at: datetime) -> None:
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
            (start_at, end_at, appointment_id),
        )
    db_connection.commit()


def _ticketed_appointment(client, db_connection, admin_headers, seeded, patient_name, phone_suffix, hour=9):
    """Confirmed, started, checked-in, and paid -- a real, ticketed queue
    entry, the only state hold/recall/priority accept. Same shape as
    test_queue_tokens.py's own _schedule_and_confirm + _pay, duplicated
    rather than imported (this test suite's own convention -- see e.g.
    test_appointment_lifecycle.py/test_queue_tokens.py each keeping
    their own small fixtures rather than sharing across files)."""
    patient = client.post(
        "/api/patients",
        json={"name": patient_name, "whatsapp_number": f"+9199{phone_suffix:08d}"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T{hour:02d}:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    appointment_id = created["id"]
    client.post(f"/api/appointments/{appointment_id}/confirm", headers=admin_headers)

    anchor = datetime.now(dt_timezone.utc) - timedelta(days=1)
    past_start = anchor + timedelta(hours=hour)
    _set_start_at(db_connection, appointment_id, past_start, past_start + timedelta(minutes=30))

    client.post(f"/api/appointments/{appointment_id}/visit", headers=admin_headers)
    client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )

    return appointment_id


def _seed(client, db_connection, name: str) -> dict:
    return seed_basic_doctor(
        client, db_connection, doctor_name=name, department_name=f"{name} Dept", appointment_type_name=f"{name} Type"
    )


# ---------------------------------------------------------------------
# Hold / recall
# ---------------------------------------------------------------------


def test_hold_requires_staff_auth(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Hold Auth")
    appointment_id = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Hold Auth Patient", 1)
    response = client.post(f"/api/appointments/{appointment_id}/queue/hold")
    assert response.status_code == 401


def test_hold_removes_entry_from_now_serving_and_waiting(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Hold Basic")
    a1 = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Hold Basic P1", 2, hour=9)
    a2 = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Hold Basic P2", 3, hour=10)

    response = client.post(f"/api/appointments/{a1}/queue/hold", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["token_number"] is not None

    queue = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=admin_headers).json()
    held_ids = {e["appointment_id"] for e in queue["held"]}
    live_ids = {e["appointment_id"] for e in queue["waiting"]}
    if queue["now_serving"]:
        live_ids.add(queue["now_serving"]["appointment_id"])

    assert a1 in held_ids
    assert a1 not in live_ids
    # a2 is now the only live entry, so it becomes now_serving.
    assert queue["now_serving"]["appointment_id"] == a2


def test_hold_is_idempotent(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Hold Twice")
    a1 = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Hold Twice Patient", 4)

    first = client.post(f"/api/appointments/{a1}/queue/hold", headers=admin_headers)
    second = client.post(f"/api/appointments/{a1}/queue/hold", headers=admin_headers)
    assert first.status_code == 200
    assert second.status_code == 200


def test_hold_nonexistent_appointment_is_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post("/api/appointments/999999999/queue/hold", headers=admin_headers)
    assert response.status_code == 404


def test_hold_unticketed_checked_in_appointment_is_409(client, db_connection):
    # Checked in but unpaid -- CHECKED_IN with token_number still NULL,
    # i.e. not actually a live queue entry yet (see generate_queue_
    # token_service's payment/waiver gate).
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Hold Unticketed")
    patient = client.post(
        "/api/patients",
        json={"name": "Hold Unticketed Patient", "whatsapp_number": "+919900000005"},
        headers=admin_headers,
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
        headers=admin_headers,
    ).json()
    client.post(f"/api/appointments/{created['id']}/confirm", headers=admin_headers)
    anchor = datetime.now(dt_timezone.utc) - timedelta(days=1)
    _set_start_at(db_connection, created["id"], anchor, anchor + timedelta(minutes=30))
    client.post(f"/api/appointments/{created['id']}/visit", headers=admin_headers)

    response = client.post(f"/api/appointments/{created['id']}/queue/hold", headers=admin_headers)
    assert response.status_code == 409


def test_recall_restores_original_token_position(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Recall Position")
    a1 = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Recall Position P1", 6, hour=9)
    a2 = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Recall Position P2", 7, hour=10)

    client.post(f"/api/appointments/{a1}/queue/hold", headers=admin_headers)
    recall = client.post(f"/api/appointments/{a1}/queue/recall", headers=admin_headers)
    assert recall.status_code == 200

    queue = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=admin_headers).json()
    assert queue["held"] == []
    # a1 has the lower token number (checked in first), so it's back to
    # being now_serving, not pushed behind a2.
    assert queue["now_serving"]["appointment_id"] == a1
    assert [e["appointment_id"] for e in queue["waiting"]] == [a2]


def test_recall_without_hold_is_409(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Recall Unheld")
    a1 = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Recall Unheld Patient", 8)

    response = client.post(f"/api/appointments/{a1}/queue/recall", headers=admin_headers)
    assert response.status_code == 409


# ---------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------


def test_priority_requires_a_reason(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Priority Reason")
    a1 = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Priority Reason Patient", 9)

    response = client.post(
        f"/api/appointments/{a1}/queue/priority",
        json={"is_priority": True},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_priority_jumps_ahead_of_earlier_token_without_renumbering(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Priority Jump")
    a1 = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Priority Jump P1", 10, hour=9)
    a2 = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Priority Jump P2", 11, hour=10)

    # a1 checked in first, so it's currently now_serving.
    queue_before = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=admin_headers).json()
    assert queue_before["now_serving"]["appointment_id"] == a1
    a1_token = queue_before["now_serving"]["token_number"]

    response = client.post(
        f"/api/appointments/{a2}/queue/priority",
        json={"is_priority": True, "reason": "Doctor requested"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["is_priority"] is True

    queue_after = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=admin_headers).json()
    assert queue_after["now_serving"]["appointment_id"] == a2
    assert queue_after["waiting"][0]["appointment_id"] == a1
    # a1's own token number is unchanged by a2's priority flag.
    assert queue_after["waiting"][0]["token_number"] == a1_token


def test_priority_off_does_not_require_a_reason(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Priority Off")
    a1 = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Priority Off Patient", 12)
    client.post(
        f"/api/appointments/{a1}/queue/priority",
        json={"is_priority": True, "reason": "Senior citizen"},
        headers=admin_headers,
    )

    response = client.post(
        f"/api/appointments/{a1}/queue/priority",
        json={"is_priority": False},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["is_priority"] is False


def test_priority_on_unticketed_appointment_is_409(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Priority Unticketed")
    patient = client.post(
        "/api/patients",
        json={"name": "Priority Unticketed Patient", "whatsapp_number": "+919900000013"},
        headers=admin_headers,
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
        headers=admin_headers,
    ).json()

    response = client.post(
        f"/api/appointments/{created['id']}/queue/priority",
        json={"is_priority": True, "reason": "Emergency"},
        headers=admin_headers,
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------
# Public display board
# ---------------------------------------------------------------------


def test_queue_display_requires_no_auth_and_omits_patient_data(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Display Board")
    _ticketed_appointment(client, db_connection, admin_headers, seeded, "Display Board Patient", 14)

    response = client.get("/api/public/queue-display")
    assert response.status_code == 200

    body = response.text
    assert "Display Board Patient" not in body

    entries = {row["doctor_id"]: row for row in response.json()}
    assert entries[seeded["doctor_id"]]["doctor_name"] == "Dr. Display Board"
    assert entries[seeded["doctor_id"]]["now_serving_token"] is not None
    assert set(entries[seeded["doctor_id"]].keys()) == {"doctor_id", "doctor_name", "now_serving_token"}


def test_queue_display_reflects_hold(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = _seed(client, db_connection, "Dr. Display Hold")
    a1 = _ticketed_appointment(client, db_connection, admin_headers, seeded, "Display Hold Patient", 15)

    client.post(f"/api/appointments/{a1}/queue/hold", headers=admin_headers)

    entries = {row["doctor_id"]: row for row in client.get("/api/public/queue-display").json()}
    assert entries[seeded["doctor_id"]]["now_serving_token"] is None
