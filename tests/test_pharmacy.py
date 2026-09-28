"""
Tests for prescription + pharmacy (OPD/HIMS master spec Phase 8,
migrations/0032_prescriptions_and_pharmacy.sql):
GET/POST/DELETE /api/appointments/{id}/prescription[/items[/{item_id}]],
POST .../prescription/prescribe, POST .../prescription/cancel,
GET /api/pharmacy/queue, GET/POST /api/pharmacy/stock,
POST /api/pharmacy/items/{item_id}/dispense.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _seed_pending_appointment(client, db_connection, doctor_name: str) -> dict:
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name=doctor_name,
        department_name=f"{doctor_name} Dept",
        appointment_type_name=f"{doctor_name} Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9194{abs(hash(doctor_name)) % 10**8:08d}"},
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

    return {
        "admin_headers": admin_headers,
        "seeded": seeded,
        "patient": patient,
        "appointment": created,
    }


def _checked_in_context(client, db_connection, doctor_name: str) -> dict:
    ctx = _seed_pending_appointment(client, db_connection, doctor_name)
    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/confirm-and-checkin",
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    return ctx


def _add_item(client, appointment_id, admin_headers, **overrides):
    payload = {"medicine_name": "Paracetamol", "quantity": 10, "dosage": "500mg", "frequency": "1-0-1"}
    payload.update(overrides)
    # ["prescription"] -- the add-item endpoint's response also carries a
    # P0 allergy_warning field (None for every existing test here, none
    # of which prescribe against a patient with a recorded allergy) --
    # see test_allergy_check.py for that behavior specifically.
    return client.post(
        f"/api/appointments/{appointment_id}/prescription/items", json=payload, headers=admin_headers
    ).json()["prescription"]


# ---------------------------------------------------------------------
# Prescription draft
# ---------------------------------------------------------------------


def test_get_prescription_requires_checked_in_to_create(client, db_connection):
    ctx = _seed_pending_appointment(client, db_connection, "Dr. Rx NotCheckedIn")
    response = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/prescription", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


def test_get_prescription_creates_a_draft_when_checked_in(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx Create")
    response = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/prescription", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DRAFT"
    assert body["items"] == []

    second = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/prescription", headers=ctx["admin_headers"]
    )
    assert second.json()["id"] == body["id"]


def test_add_item_requires_checked_in(client, db_connection):
    ctx = _seed_pending_appointment(client, db_connection, "Dr. Rx AddNotCheckedIn")
    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/prescription/items",
        json={"medicine_name": "Paracetamol", "quantity": 10},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 409


def test_add_and_remove_item(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx AddRemove")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    prescription = _add_item(client, appointment_id, admin_headers)
    assert len(prescription["items"]) == 1
    item = prescription["items"][0]
    assert item["medicine_name"] == "Paracetamol"
    assert item["dispense_status"] == "PENDING"

    response = client.delete(
        f"/api/appointments/{appointment_id}/prescription/items/{item['id']}", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_remove_nonexistent_item_is_404(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx RemoveMissing")
    appointment_id = ctx["appointment"]["id"]
    _add_item(client, appointment_id, ctx["admin_headers"])

    response = client.delete(
        f"/api/appointments/{appointment_id}/prescription/items/999999", headers=ctx["admin_headers"]
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------
# Prescribe (send to pharmacy)
# ---------------------------------------------------------------------


def test_prescribe_requires_at_least_one_item(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx PrescribeEmpty")
    appointment_id = ctx["appointment"]["id"]
    client.get(f"/api/appointments/{appointment_id}/prescription", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{appointment_id}/prescription/prescribe", headers=ctx["admin_headers"]
    )
    assert response.status_code == 422


def test_prescribe_succeeds_and_freezes_items(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx PrescribeOk")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_item(client, appointment_id, admin_headers)

    response = client.post(f"/api/appointments/{appointment_id}/prescription/prescribe", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "PRESCRIBED"

    # Item set is now frozen.
    add_after = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Ibuprofen", "quantity": 5},
        headers=admin_headers,
    )
    assert add_after.status_code == 409


# ---------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------


def test_cancel_requires_prescribed_status(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx CancelDraft")
    appointment_id = ctx["appointment"]["id"]
    _add_item(client, appointment_id, ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{appointment_id}/prescription/cancel",
        json={"reason": "Changed my mind"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 409


def test_cancel_succeeds_before_any_dispensing(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx CancelOk")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    _add_item(client, appointment_id, admin_headers)
    client.post(f"/api/appointments/{appointment_id}/prescription/prescribe", headers=admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/prescription/cancel",
        json={"reason": "Wrong patient"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_cancel_blocked_after_dispensing_started(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx CancelAfterDispense")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    prescription = _add_item(client, appointment_id, admin_headers)
    item_id = prescription["items"][0]["id"]
    client.post(f"/api/appointments/{appointment_id}/prescription/prescribe", headers=admin_headers)

    client.post(f"/api/pharmacy/items/{item_id}/dispense", json={"quantity": 2}, headers=admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/prescription/cancel",
        json={"reason": "Too late"},
        headers=admin_headers,
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------
# Dispensing -- not gated on CHECKED_IN, computed dispense_status
# ---------------------------------------------------------------------


def test_dispense_requires_prescribed(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx DispenseDraft")
    appointment_id = ctx["appointment"]["id"]
    prescription = _add_item(client, appointment_id, ctx["admin_headers"])
    item_id = prescription["items"][0]["id"]

    response = client.post(
        f"/api/pharmacy/items/{item_id}/dispense", json={"quantity": 5}, headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


def test_partial_then_full_dispense(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx DispensePartial")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    prescription = _add_item(client, appointment_id, admin_headers, quantity=10)
    item_id = prescription["items"][0]["id"]
    client.post(f"/api/appointments/{appointment_id}/prescription/prescribe", headers=admin_headers)

    first = client.post(f"/api/pharmacy/items/{item_id}/dispense", json={"quantity": 4}, headers=admin_headers)
    assert first.status_code == 200
    assert first.json()["dispense_status"] == "PARTIALLY_DISPENSED"
    assert first.json()["quantity_dispensed"] == 4

    second = client.post(f"/api/pharmacy/items/{item_id}/dispense", json={"quantity": 6}, headers=admin_headers)
    assert second.status_code == 200
    assert second.json()["dispense_status"] == "DISPENSED"
    assert second.json()["quantity_dispensed"] == 10


def test_dispense_more_than_remaining_is_422(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx DispenseOver")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    prescription = _add_item(client, appointment_id, admin_headers, quantity=5)
    item_id = prescription["items"][0]["id"]
    client.post(f"/api/appointments/{appointment_id}/prescription/prescribe", headers=admin_headers)

    response = client.post(f"/api/pharmacy/items/{item_id}/dispense", json={"quantity": 6}, headers=admin_headers)
    assert response.status_code == 422


def test_dispense_is_not_gated_on_checked_in(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx DispenseAfterVisitComplete")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    prescription = _add_item(client, appointment_id, admin_headers)
    item_id = prescription["items"][0]["id"]
    client.post(f"/api/appointments/{appointment_id}/prescription/prescribe", headers=admin_headers)

    client.post(f"/api/appointments/{appointment_id}/complete", headers=admin_headers)

    response = client.post(f"/api/pharmacy/items/{item_id}/dispense", json={"quantity": 3}, headers=admin_headers)
    assert response.status_code == 200


# ---------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------


def test_create_stock_requires_admin(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx StockStaffOnly")
    from tests.helpers import create_staff_and_get_headers

    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    response = client.post(
        "/api/pharmacy/stock",
        json={
            "medicine_name": "Paracetamol",
            "batch_number": "B-STAFF-1",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "quantity_on_hand": 100,
            "unit_price": 2.5,
        },
        headers=staff_headers,
    )
    assert response.status_code == 403


def test_create_and_dispense_against_stock(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx StockDispense")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    stock = client.post(
        "/api/pharmacy/stock",
        json={
            "medicine_name": "Paracetamol",
            "batch_number": "B-001",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "quantity_on_hand": 100,
            "unit_price": 2.5,
        },
        headers=admin_headers,
    ).json()

    prescription = _add_item(client, appointment_id, admin_headers, quantity=10)
    item_id = prescription["items"][0]["id"]
    client.post(f"/api/appointments/{appointment_id}/prescription/prescribe", headers=admin_headers)

    response = client.post(
        f"/api/pharmacy/items/{item_id}/dispense",
        json={"quantity": 10, "pharmacy_stock_id": stock["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dispense_status"] == "DISPENSED"
    assert body["dispense_record"]["amount"] == 25.0

    stock_after = client.get("/api/pharmacy/stock", headers=admin_headers).json()
    matched = next(s for s in stock_after if s["id"] == stock["id"])
    assert matched["quantity_on_hand"] == 90


def test_dispense_rejects_mismatched_medicine(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx StockMismatch")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    stock = client.post(
        "/api/pharmacy/stock",
        json={
            "medicine_name": "Ibuprofen",
            "batch_number": "B-002",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "quantity_on_hand": 50,
            "unit_price": 3,
        },
        headers=admin_headers,
    ).json()

    prescription = _add_item(client, appointment_id, admin_headers, medicine_name="Paracetamol", quantity=5)
    item_id = prescription["items"][0]["id"]
    client.post(f"/api/appointments/{appointment_id}/prescription/prescribe", headers=admin_headers)

    response = client.post(
        f"/api/pharmacy/items/{item_id}/dispense",
        json={"quantity": 5, "pharmacy_stock_id": stock["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_dispense_rejects_insufficient_stock(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx StockInsufficient")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    stock = client.post(
        "/api/pharmacy/stock",
        json={
            "medicine_name": "Paracetamol",
            "batch_number": "B-003",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "quantity_on_hand": 2,
            "unit_price": 2.5,
        },
        headers=admin_headers,
    ).json()

    prescription = _add_item(client, appointment_id, admin_headers, quantity=10)
    item_id = prescription["items"][0]["id"]
    client.post(f"/api/appointments/{appointment_id}/prescription/prescribe", headers=admin_headers)

    response = client.post(
        f"/api/pharmacy/items/{item_id}/dispense",
        json={"quantity": 10, "pharmacy_stock_id": stock["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_duplicate_stock_batch_is_409(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx StockDuplicate")
    admin_headers = ctx["admin_headers"]
    payload = {
        "medicine_name": "Paracetamol",
        "batch_number": "B-DUP",
        "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
        "quantity_on_hand": 10,
        "unit_price": 1,
    }
    client.post("/api/pharmacy/stock", json=payload, headers=admin_headers)
    response = client.post("/api/pharmacy/stock", json=payload, headers=admin_headers)
    assert response.status_code == 409


# ---------------------------------------------------------------------
# Pharmacy queue
# ---------------------------------------------------------------------


def test_pharmacy_queue_lists_prescribed_with_pending_items(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx Queue")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    prescription = _add_item(client, appointment_id, admin_headers, quantity=10)
    item_id = prescription["items"][0]["id"]
    client.post(f"/api/appointments/{appointment_id}/prescription/prescribe", headers=admin_headers)

    queue = client.get("/api/pharmacy/queue", headers=admin_headers).json()
    assert any(entry["prescription_id"] == prescription["id"] for entry in queue)
    entry = next(e for e in queue if e["prescription_id"] == prescription["id"])
    assert entry["patient_id"] == ctx["patient"]["id"]
    assert entry["appointment_id"] == appointment_id

    # Fully dispensing drops it off the queue.
    client.post(f"/api/pharmacy/items/{item_id}/dispense", json={"quantity": 10}, headers=admin_headers)
    queue_after = client.get("/api/pharmacy/queue", headers=admin_headers).json()
    assert not any(e["prescription_id"] == prescription["id"] for e in queue_after)


def test_draft_prescription_never_appears_in_queue(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Rx QueueDraft")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    prescription = _add_item(client, appointment_id, admin_headers)

    queue = client.get("/api/pharmacy/queue", headers=admin_headers).json()
    assert not any(e["prescription_id"] == prescription["id"] for e in queue)
