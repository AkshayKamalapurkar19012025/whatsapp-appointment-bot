"""
Tests for GET /api/exceptions (OPD/HIMS master spec Phase 11, sections
46-47) -- app/services/exception_engine.py's live-computed operational
alerts. See that module's docstring for the full design rationale.

Every exception type here is threshold-based (something has been true
for N minutes), so each test creates the real state through the normal
API flow, then backdates the one timestamp the threshold reads directly
via SQL (the same pattern tests/test_queue_tokens.py and others already
use for time-dependent assertions) rather than waiting in real time.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _seed_checked_in_patient(client, db_connection, label: str) -> dict:
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name=f"Dr. {label}",
        department_name=f"{label} Dept",
        appointment_type_name=f"{label} Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": f"{label} Patient", "whatsapp_number": f"+9195{abs(hash(label)) % 10**8:08d}"},
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
    checkin = client.post(f"/api/appointments/{created['id']}/confirm-and-checkin", headers=admin_headers)
    assert checkin.status_code == 200

    return {"admin_headers": admin_headers, "patient": patient, "appointment": created}


def test_exceptions_requires_authentication(client):
    response = client.get("/api/exceptions")
    assert response.status_code == 401


def test_exceptions_empty_when_nothing_overdue(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/exceptions", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"exceptions": [], "count": 0}


def test_waiting_for_triage_exception(client, db_connection):
    ctx = _seed_checked_in_patient(client, db_connection, "Triage Wait")
    appointment_id = ctx["appointment"]["id"]

    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET visited_at = NOW() - INTERVAL '31 minutes' WHERE id = %s",
            (appointment_id,),
        )
    db_connection.commit()

    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    assert response.status_code == 200
    body = response.json()
    matches = [e for e in body["exceptions"] if e["type"] == "WAITING_FOR_TRIAGE"]
    assert len(matches) == 1
    assert matches[0]["appointment_id"] == appointment_id
    assert matches[0]["patient_name"] == ctx["patient"]["name"]
    assert matches[0]["age_minutes"] >= 31
    assert matches[0]["current_status"] == "OPEN"

    # Recording vitals resolves it -- the exception is derived, not stored.
    client.post(
        f"/api/appointments/{appointment_id}/vitals",
        json={"bp_systolic": 120, "bp_diastolic": 80},
        headers=ctx["admin_headers"],
    )
    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    matches = [e for e in response.json()["exceptions"] if e["type"] == "WAITING_FOR_TRIAGE"]
    assert matches == []


def test_waiting_for_triage_not_flagged_before_threshold(client, db_connection):
    ctx = _seed_checked_in_patient(client, db_connection, "Triage Fresh")
    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    matches = [e for e in response.json()["exceptions"] if e["type"] == "WAITING_FOR_TRIAGE"]
    assert matches == []


def test_waiting_for_doctor_exception(client, db_connection):
    ctx = _seed_checked_in_patient(client, db_connection, "Doctor Wait")
    appointment_id = ctx["appointment"]["id"]

    vitals_resp = client.post(
        f"/api/appointments/{appointment_id}/vitals",
        json={"bp_systolic": 118, "bp_diastolic": 76},
        headers=ctx["admin_headers"],
    )
    assert vitals_resp.status_code == 200

    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE vitals SET recorded_at = NOW() - INTERVAL '31 minutes' "
            "WHERE encounter_id = (SELECT encounter_id FROM appointments WHERE id = %s)",
            (appointment_id,),
        )
    db_connection.commit()

    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    matches = [e for e in response.json()["exceptions"] if e["type"] == "WAITING_FOR_DOCTOR"]
    assert len(matches) == 1
    assert matches[0]["appointment_id"] == appointment_id

    # Starting the consultation resolves it.
    client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={"chief_complaint": "Check"},
        headers=ctx["admin_headers"],
    )
    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    matches = [e for e in response.json()["exceptions"] if e["type"] == "WAITING_FOR_DOCTOR"]
    assert matches == []


def test_order_pending_exception_uses_priority_threshold(client, db_connection):
    ctx = _seed_checked_in_patient(client, db_connection, "Order Wait")
    appointment_id = ctx["appointment"]["id"]

    stat_order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "Troponin", "priority": "STAT"},
        headers=ctx["admin_headers"],
    ).json()
    routine_order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "Lipid Panel", "priority": "ROUTINE"},
        headers=ctx["admin_headers"],
    ).json()

    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE orders SET ordered_at = NOW() - INTERVAL '31 minutes' WHERE id = %s",
            (stat_order["id"],),
        )
        cur.execute(
            "UPDATE orders SET ordered_at = NOW() - INTERVAL '31 minutes' WHERE id = %s",
            (routine_order["id"],),
        )
    db_connection.commit()

    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    order_exceptions = {e["order_id"]: e for e in response.json()["exceptions"] if e["type"] == "ORDER_PENDING"}

    # STAT is well past its 30-minute threshold -- flagged.
    assert stat_order["id"] in order_exceptions
    # ROUTINE's threshold is 180 minutes -- 31 minutes isn't overdue yet.
    assert routine_order["id"] not in order_exceptions

    # Recording a result resolves the STAT one.
    client.post(
        f"/api/appointments/{appointment_id}/orders/{stat_order['id']}/result",
        json={"items": [{"parameter": "Troponin", "result_value": "0.01", "unit": "ng/mL"}]},
        headers=ctx["admin_headers"],
    )
    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    order_exceptions = {e["order_id"] for e in response.json()["exceptions"] if e["type"] == "ORDER_PENDING"}
    assert stat_order["id"] not in order_exceptions


def test_prescription_not_dispensed_exception(client, db_connection):
    ctx = _seed_checked_in_patient(client, db_connection, "Rx Wait")
    appointment_id = ctx["appointment"]["id"]

    client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Amoxicillin", "quantity": 10},
        headers=ctx["admin_headers"],
    )
    prescribe_resp = client.post(
        f"/api/appointments/{appointment_id}/prescription/prescribe", headers=ctx["admin_headers"]
    )
    assert prescribe_resp.status_code == 200
    prescription_id = prescribe_resp.json()["id"]

    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE prescriptions SET prescribed_at = NOW() - INTERVAL '121 minutes' WHERE id = %s",
            (prescription_id,),
        )
    db_connection.commit()

    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    matches = [e for e in response.json()["exceptions"] if e["type"] == "PRESCRIPTION_NOT_DISPENSED"]
    assert len(matches) == 1
    assert matches[0]["prescription_id"] == prescription_id


def test_billing_not_started_exception(client, db_connection):
    ctx = _seed_checked_in_patient(client, db_connection, "Bill Not Started")
    appointment_id = ctx["appointment"]["id"]

    client.put(
        f"/api/appointments/{appointment_id}/consultation",
        json={"chief_complaint": "Routine check", "diagnosis": "Healthy"},
        headers=ctx["admin_headers"],
    )
    complete_resp = client.post(
        f"/api/appointments/{appointment_id}/consultation/complete", headers=ctx["admin_headers"]
    )
    assert complete_resp.status_code == 200

    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE consultations SET completed_at = NOW() - INTERVAL '16 minutes' "
            "WHERE encounter_id = (SELECT encounter_id FROM appointments WHERE id = %s)",
            (appointment_id,),
        )
    db_connection.commit()

    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    matches = [e for e in response.json()["exceptions"] if e["type"] == "BILLING_NOT_STARTED"]
    assert len(matches) == 1

    # Raising a charge resolves it.
    client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Consultation fee", "amount": 300},
        headers=ctx["admin_headers"],
    )
    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    matches = [e for e in response.json()["exceptions"] if e["type"] == "BILLING_NOT_STARTED"]
    assert matches == []


def test_payment_pending_exception(client, db_connection):
    ctx = _seed_checked_in_patient(client, db_connection, "Payment Wait")
    appointment_id = ctx["appointment"]["id"]

    client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Consultation fee", "amount": 500},
        headers=ctx["admin_headers"],
    )
    complete_resp = client.post(f"/api/appointments/{appointment_id}/complete", headers=ctx["admin_headers"])
    assert complete_resp.status_code == 200

    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE encounters SET closed_at = NOW() - INTERVAL '11 minutes' "
            "WHERE id = (SELECT encounter_id FROM appointments WHERE id = %s)",
            (appointment_id,),
        )
    db_connection.commit()

    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    matches = [e for e in response.json()["exceptions"] if e["type"] == "PAYMENT_PENDING"]
    assert len(matches) == 1
    assert matches[0]["balance"] == 500.0

    # Paying it off resolves it.
    client.post(
        f"/api/appointments/{appointment_id}/bill/payments",
        json={"amount": 500, "method": "CASH"},
        headers=ctx["admin_headers"],
    )
    response = client.get("/api/exceptions", headers=ctx["admin_headers"])
    matches = [e for e in response.json()["exceptions"] if e["type"] == "PAYMENT_PENDING"]
    assert matches == []


def test_exceptions_sorted_most_overdue_first(client, db_connection):
    ctx_a = _seed_checked_in_patient(client, db_connection, "Sort Newer")
    ctx_b = _seed_checked_in_patient(client, db_connection, "Sort Older")

    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET visited_at = NOW() - INTERVAL '35 minutes' WHERE id = %s",
            (ctx_a["appointment"]["id"],),
        )
        cur.execute(
            "UPDATE appointments SET visited_at = NOW() - INTERVAL '90 minutes' WHERE id = %s",
            (ctx_b["appointment"]["id"],),
        )
    db_connection.commit()

    response = client.get("/api/exceptions", headers=ctx_a["admin_headers"])
    exceptions = response.json()["exceptions"]
    ids_in_order = [e["appointment_id"] for e in exceptions if e["type"] == "WAITING_FOR_TRIAGE"]
    assert ids_in_order.index(ctx_b["appointment"]["id"]) < ids_in_order.index(ctx_a["appointment"]["id"])
