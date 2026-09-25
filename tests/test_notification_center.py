"""
Tests for the staff notification center (master spec audit gap #5's
notification half, section 15): GET/POST /api/notifications, and the
three event emission points (check-in, lab result, prescription ready).
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _pending_appointment(client, db_connection, doctor_name: str) -> dict:
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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9199{abs(hash(doctor_name)) % 10**8:08d}"},
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
    return {"admin_headers": admin_headers, "seeded": seeded, "patient": patient, "appointment": created}


def test_empty_notification_center_initially(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/notifications", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"items": [], "unread_count": 0}


def test_checkin_emits_patient_arrived_notification(client, db_connection):
    ctx = _pending_appointment(client, db_connection, "Dr. Notif Checkin")
    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/confirm-and-checkin",
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200

    notifications = client.get("/api/notifications", headers=ctx["admin_headers"]).json()
    assert notifications["unread_count"] == 1
    assert notifications["items"][0]["kind"] == "PATIENT_ARRIVED"
    assert ctx["patient"]["name"] in notifications["items"][0]["message"]
    assert notifications["items"][0]["appointment_id"] == ctx["appointment"]["id"]


def test_lab_result_emits_notification_radiology_and_lab_only(client, db_connection):
    ctx = _pending_appointment(client, db_connection, "Dr. Notif Lab")
    appointment_id = ctx["appointment"]["id"]
    headers = ctx["admin_headers"]
    client.post(f"/api/appointments/{appointment_id}/confirm-and-checkin", headers=headers)

    lab_order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=headers,
    ).json()
    procedure_order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "PROCEDURE", "description": "Dressing change"},
        headers=headers,
    ).json()

    client.post(
        f"/api/appointments/{appointment_id}/orders/{lab_order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.5", "unit": "g/dL"}]},
        headers=headers,
    )
    client.post(
        f"/api/appointments/{appointment_id}/orders/{procedure_order['id']}/result",
        json={"items": [{"parameter": "Outcome", "result_value": "Done"}]},
        headers=headers,
    )

    notifications = client.get("/api/notifications", headers=headers).json()
    kinds = [n["kind"] for n in notifications["items"]]
    assert kinds.count("LAB_RESULT_AVAILABLE") == 1


def test_prescription_ready_fires_once_all_items_dispensed(client, db_connection):
    ctx = _pending_appointment(client, db_connection, "Dr. Notif Rx")
    appointment_id = ctx["appointment"]["id"]
    headers = ctx["admin_headers"]
    client.post(f"/api/appointments/{appointment_id}/confirm-and-checkin", headers=headers)

    # The add-item endpoint returns {"prescription": ..., "allergy_warning":
    # ...} (P0 allergy check) -- ["prescription"] is the whole prescription
    # with its full items list, not the single item just added, so the
    # newly added item is always last.
    client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Paracetamol", "quantity": 10},
        headers=headers,
    )
    prescription = client.post(
        f"/api/appointments/{appointment_id}/prescription/items",
        json={"medicine_name": "Ibuprofen", "quantity": 5},
        headers=headers,
    ).json()["prescription"]
    item1, item2 = prescription["items"]
    prescribe = client.post(f"/api/appointments/{appointment_id}/prescription/prescribe", headers=headers)
    assert prescribe.status_code == 200

    dispense1 = client.post(
        f"/api/pharmacy/items/{item1['id']}/dispense",
        json={"quantity": item1["quantity"]},
        headers=headers,
    )
    assert dispense1.status_code == 200

    # Only one of two items fully dispensed -- no PRESCRIPTION_READY yet.
    notifications = client.get("/api/notifications", headers=headers).json()
    assert "PRESCRIPTION_READY" not in [n["kind"] for n in notifications["items"]]

    dispense2 = client.post(
        f"/api/pharmacy/items/{item2['id']}/dispense",
        json={"quantity": item2["quantity"]},
        headers=headers,
    )
    assert dispense2.status_code == 200

    notifications = client.get("/api/notifications", headers=headers).json()
    ready = [n for n in notifications["items"] if n["kind"] == "PRESCRIPTION_READY"]
    assert len(ready) == 1
    assert ctx["patient"]["name"] in ready[0]["message"]


def test_mark_notification_read(client, db_connection):
    ctx = _pending_appointment(client, db_connection, "Dr. Notif Read")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm-and-checkin", headers=ctx["admin_headers"])

    notifications = client.get("/api/notifications", headers=ctx["admin_headers"]).json()
    notification_id = notifications["items"][0]["id"]
    assert notifications["unread_count"] == 1

    read = client.post(f"/api/notifications/{notification_id}/read", headers=ctx["admin_headers"])
    assert read.status_code == 200
    assert read.json()["read_at"] is not None

    after = client.get("/api/notifications", headers=ctx["admin_headers"]).json()
    assert after["unread_count"] == 0

    # Idempotent -- reading an already-read notification isn't an error.
    read_again = client.post(f"/api/notifications/{notification_id}/read", headers=ctx["admin_headers"])
    assert read_again.status_code == 200


def test_mark_notification_read_404_for_unknown_id(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post("/api/notifications/999999/read", headers=admin_headers)
    assert response.status_code == 404


def test_mark_all_read(client, db_connection):
    ctx = _pending_appointment(client, db_connection, "Dr. Notif ReadAll")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm-and-checkin", headers=ctx["admin_headers"])

    response = client.post("/api/notifications/read-all", headers=ctx["admin_headers"])
    assert response.status_code == 200
    assert response.json()["updated"] == 1

    after = client.get("/api/notifications", headers=ctx["admin_headers"]).json()
    assert after["unread_count"] == 0


def test_notifications_require_authentication(client, db_connection):
    response = client.get("/api/notifications")
    assert response.status_code == 401
