"""
Full end-to-end journeys mapped directly to the patient arrival workflow
spec's own numbered test scenarios (Section 29). Every individual step
here is already covered by a more granular test elsewhere (Phases 1-5's
own test files -- test_queue_token_generation.py, test_patients.py,
test_consultation_payments.py, test_queue_tokens.py,
test_appointment_lifecycle.py); this file exists to prove the full
chain composes correctly end-to-end, the way front-desk staff would
actually drive it, not just that each step works in isolation.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient_id, hour=9):
    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient_id,
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T{hour:02d}:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    client.post(f"/api/appointments/{created['id']}/confirm", headers=admin_headers)

    anchor = datetime.now(dt_timezone.utc) - timedelta(days=1)
    past_start = anchor + timedelta(hours=hour)
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
            (past_start, past_start + timedelta(minutes=30), created["id"]),
        )
    db_connection.commit()

    return created["id"]


def _get_listed(client, admin_headers, appointment_id):
    listing = client.get("/api/appointments", headers=admin_headers).json()["items"]
    return next(a for a in listing if a["id"] == appointment_id)


def test_scenario_1_existing_patient_unpaid_full_flow(client, db_connection):
    """Confirmed -> Arrived -> existing patient identified -> registration
    verified -> unpaid -> payment -> paid -> token generated -> waiting
    -> visible in the doctor queue."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Scenario One",
        department_name="Scenario One Dept", appointment_type_name="Scenario One Type",
    )
    client.put(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{seeded['appointment_type_id']}",
        json={"duration_minutes": 30, "consultation_fee": 500},
        headers=admin_headers,
    )
    # "Existing patient" -- created once, reused, never duplicated.
    patient = client.post(
        "/api/patients",
        json={"name": "Scenario One Patient", "whatsapp_number": "+919500000001"},
        headers=admin_headers,
    ).json()

    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])

    visit = client.post(f"/api/appointments/{appointment_id}/visit", headers=admin_headers)
    assert visit.json()["status"] == "CHECKED_IN"
    assert visit.json()["token_number"] is None  # not yet in the queue

    charge = client.get(f"/api/appointments/{appointment_id}/charge", headers=admin_headers)
    assert float(charge.json()["consultation_fee"]) == 500.0

    payment = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "UPI", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert payment.json()["payment_status"] == "PAID"
    assert payment.json()["token_number"] == 1

    queue = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=admin_headers).json()
    assert queue["now_serving"]["patient_name"] == "Scenario One Patient"
    assert queue["now_serving"]["token_number"] == 1

    # No duplicate patient record was created for this same person.
    patients = client.get("/api/patients", headers=admin_headers).json()
    assert sum(1 for p in patients if p["whatsapp_number"] == "+919500000001") == 1


def test_scenario_3_new_patient_full_flow(client, db_connection):
    """Confirmed -> Arrived -> register a genuinely new patient ->
    patient ID created -> encounter (the appointment itself) already
    exists -> payment -> token -> waiting."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Scenario Three",
        department_name="Scenario Three Dept", appointment_type_name="Scenario Three Type",
    )
    client.put(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{seeded['appointment_type_id']}",
        json={"duration_minutes": 30, "consultation_fee": 300},
        headers=admin_headers,
    )

    new_patient = client.post(
        "/api/patients",
        json={"name": "Scenario Three New Patient", "whatsapp_number": "+919500000003"},
        headers=admin_headers,
    )
    assert new_patient.status_code == 200
    patient_id = new_patient.json()["id"]

    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient_id)
    client.post(f"/api/appointments/{appointment_id}/visit", headers=admin_headers)
    payment = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )

    assert payment.json()["payment_status"] == "PAID"
    assert payment.json()["token_number"] == 1

    listed = _get_listed(client, admin_headers, appointment_id)
    assert listed["patient_id"] == patient_id
    assert listed["status"] == "CHECKED_IN"


def test_scenario_5_waived_free_visit_full_flow(client, db_connection):
    """Arrived -> registered -> fee waived with a reason (3-day-revisit
    rule satisfied) -> queue token generated -- no money collected."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Scenario Five",
        department_name="Scenario Five Dept", appointment_type_name="Scenario Five Type",
    )
    client.put(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{seeded['appointment_type_id']}",
        json={"duration_minutes": 30, "consultation_fee": 500},
        headers=admin_headers,
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Scenario Five Patient", "whatsapp_number": "+919500000005"},
        headers=admin_headers,
    ).json()

    prior_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=9)
    client.post(f"/api/appointments/{prior_id}/visit", headers=admin_headers)
    client.post(f"/api/appointments/{prior_id}/complete", headers=admin_headers)

    followup_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=10)
    client.post(f"/api/appointments/{followup_id}/visit", headers=admin_headers)

    waive = client.post(
        f"/api/appointments/{followup_id}/waive-payment",
        json={"reason": "Follow-up visit within 3 days, staff approved"},
        headers=admin_headers,
    )
    assert waive.status_code == 200
    assert waive.json()["payment_status"] == "WAIVED"
    assert float(waive.json()["payment_amount"]) == 0.0
    assert waive.json()["token_number"] == 1

    queue = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=admin_headers).json()
    assert queue["now_serving"]["patient_name"] == "Scenario Five Patient"


def test_scenario_4_payment_failure_keeps_patient_out_of_queue_until_retry(client, db_connection):
    """Arrived -> registered -> payment failed -> no queue token, not in
    the doctor's queue -> staff retries -> succeeds -> now in the queue."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Scenario Four",
        department_name="Scenario Four Dept", appointment_type_name="Scenario Four Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Scenario Four Patient", "whatsapp_number": "+919500000004"},
        headers=admin_headers,
    ).json()
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    client.post(f"/api/appointments/{appointment_id}/visit", headers=admin_headers)

    failed = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CARD", "outcome": "FAILED"},
        headers=admin_headers,
    )
    assert failed.json()["payment_status"] == "FAILED"
    assert failed.json()["token_number"] is None

    queue_before = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=admin_headers).json()
    all_names_before = (
        ([queue_before["now_serving"]["patient_name"]] if queue_before["now_serving"] else [])
        + [w["patient_name"] for w in queue_before["waiting"]]
    )
    assert "Scenario Four Patient" not in all_names_before

    retried = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert retried.json()["payment_status"] == "PAID"
    assert retried.json()["token_number"] == 1

    queue_after = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=admin_headers).json()
    assert queue_after["now_serving"]["patient_name"] == "Scenario Four Patient"


def test_scenario_8_refresh_persists_payment_and_token_state(client, db_connection):
    """A browser refresh is just a fresh GET -- the state it reads back
    must show PAID and the same token, and must not expose any way to
    pay a second time from that fresh read."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Scenario Eight",
        department_name="Scenario Eight Dept", appointment_type_name="Scenario Eight Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Scenario Eight Patient", "whatsapp_number": "+919500000008"},
        headers=admin_headers,
    ).json()
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    client.post(f"/api/appointments/{appointment_id}/visit", headers=admin_headers)
    client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "UPI", "outcome": "PAID"},
        headers=admin_headers,
    )

    # Simulate a browser refresh: a brand new GET, no state carried over
    # client-side.
    refreshed = _get_listed(client, admin_headers, appointment_id)
    assert refreshed["payment_status"] == "PAID"
    assert refreshed["token_number"] == 1

    # A second payment attempt after "refresh" must still be a no-op,
    # not a second charge.
    replay = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert replay.json()["payment_method"] == "UPI"  # unchanged
    assert replay.json()["token_number"] == 1
