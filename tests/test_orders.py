"""
Tests for the order spine added in migrations/0030_orders.sql (OPD/HIMS
master spec Phase 6): GET/POST /api/appointments/{id}/orders and
POST .../orders/{order_id}/cancel.
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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9196{abs(hash(doctor_name)) % 10**8:08d}"},
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


# ---------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------


def test_creating_an_order_requires_checked_in(client, db_connection):
    ctx = _seed_pending_appointment(client, db_connection, "Dr. Orders NotCheckedIn")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 409


def test_creating_a_lab_order_succeeds(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Orders Lab")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/orders",
        json={
            "order_type": "LAB",
            "description": "CBC",
            "clinical_indication": "Fever, rule out infection",
            "priority": "URGENT",
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["order_type"] == "LAB"
    assert body["description"] == "CBC"
    assert body["priority"] == "URGENT"
    assert body["status"] == "ORDERED"
    assert body["ordering_doctor_id"] == ctx["seeded"]["doctor_id"]


def test_external_referral_requires_a_destination(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Orders ExternalNoDest")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/orders",
        json={"order_type": "EXTERNAL_REFERRAL", "description": "MRI Brain"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 422


def test_external_referral_with_destination_succeeds(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Orders ExternalWithDest")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/orders",
        json={
            "order_type": "EXTERNAL_REFERRAL",
            "description": "MRI Brain",
            "external_destination": "City Imaging Center",
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    assert response.json()["external_destination"] == "City Imaging Center"


# ---------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------


def test_listing_orders_returns_newest_first(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Orders List")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=admin_headers,
    )
    client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "RADIOLOGY", "description": "Chest X-ray"},
        headers=admin_headers,
    )

    response = client.get(f"/api/appointments/{appointment_id}/orders", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["description"] == "Chest X-ray"
    assert body[1]["description"] == "CBC"


def test_orders_remain_listable_after_visit_completed(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Orders AfterComplete")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=admin_headers,
    )
    client.post(f"/api/appointments/{appointment_id}/complete", headers=admin_headers)

    response = client.get(f"/api/appointments/{appointment_id}/orders", headers=admin_headers)
    assert response.status_code == 200
    assert len(response.json()) == 1

    late_order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "Repeat CBC"},
        headers=admin_headers,
    )
    assert late_order.status_code == 409


# ---------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------


def test_cancel_order_requires_a_reason(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Orders CancelNoReason")
    appointment_id = ctx["appointment"]["id"]
    order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=ctx["admin_headers"],
    ).json()

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/cancel",
        json={"reason": ""},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 422


def test_cancel_order_succeeds(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Orders Cancel")
    appointment_id = ctx["appointment"]["id"]
    order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=ctx["admin_headers"],
    ).json()

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/cancel",
        json={"reason": "Ordered by mistake"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED"
    assert body["cancel_reason"] == "Ordered by mistake"
    assert body["cancelled_at"] is not None


def test_cancel_order_after_visit_completed_still_works(client, db_connection):
    # Deliberately different from vitals/consultation: cancelling a
    # wrong order must still be possible after the visit itself has
    # closed out, since the order may not have reached the lab yet.
    ctx = _checked_in_context(client, db_connection, "Dr. Orders CancelAfterComplete")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=admin_headers,
    ).json()
    client.post(f"/api/appointments/{appointment_id}/complete", headers=admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/cancel",
        json={"reason": "No longer needed"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_cancel_already_cancelled_order_is_409(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Orders DoubleCancel")
    appointment_id = ctx["appointment"]["id"]
    order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=ctx["admin_headers"],
    ).json()

    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/cancel",
        json={"reason": "First cancel"},
        headers=ctx["admin_headers"],
    )
    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/cancel",
        json={"reason": "Second cancel"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 409


def test_cancel_order_requires_staff_auth(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Orders CancelAuth")
    appointment_id = ctx["appointment"]["id"]
    order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=ctx["admin_headers"],
    ).json()

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/cancel",
        json={"reason": "No auth"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------
# Worklist (GET /api/orders/worklist) -- the cross-patient view the Lab/
# Radiology Worklist screen is built on, unlike every other endpoint
# above which is scoped to one appointment's own encounter.
# ---------------------------------------------------------------------


def test_worklist_defaults_to_open_orders_across_patients(client, db_connection):
    ctx_a = _checked_in_context(client, db_connection, "Dr. Worklist A")
    ctx_b = _checked_in_context(client, db_connection, "Dr. Worklist B")

    order_a = client.post(
        f"/api/appointments/{ctx_a['appointment']['id']}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=ctx_a["admin_headers"],
    ).json()
    order_b = client.post(
        f"/api/appointments/{ctx_b['appointment']['id']}/orders",
        json={"order_type": "RADIOLOGY", "description": "Chest X-ray"},
        headers=ctx_b["admin_headers"],
    ).json()

    response = client.get("/api/orders/worklist", headers=ctx_a["admin_headers"])
    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert {order_a["id"], order_b["id"]} <= ids
    for row in response.json():
        if row["id"] == order_a["id"]:
            assert row["status"] == "ORDERED"
            assert row["appointment_id"] == ctx_a["appointment"]["id"]
            assert row["patient_name"] == ctx_a["patient"]["name"]
            assert row["order_type"] == "LAB"


def test_worklist_excludes_completed_and_cancelled_by_default(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Worklist Excl")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    completed = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "Will be completed"},
        headers=admin_headers,
    ).json()
    client.post(
        f"/api/appointments/{appointment_id}/orders/{completed['id']}/result",
        json={"items": [{"parameter": "Result", "result_value": "Normal"}]},
        headers=admin_headers,
    )

    cancelled = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "Will be cancelled"},
        headers=admin_headers,
    ).json()
    client.post(
        f"/api/appointments/{appointment_id}/orders/{cancelled['id']}/cancel",
        json={"reason": "Not needed"},
        headers=admin_headers,
    )

    still_open = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "Still open"},
        headers=admin_headers,
    ).json()

    response = client.get("/api/orders/worklist", headers=admin_headers)
    ids = {row["id"] for row in response.json()}
    assert completed["id"] not in ids
    assert cancelled["id"] not in ids
    assert still_open["id"] in ids

    completed_only = client.get(
        "/api/orders/worklist", params={"status": "COMPLETED"}, headers=admin_headers
    )
    assert completed["id"] in {row["id"] for row in completed_only.json()}


def test_worklist_filters_by_order_type(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Worklist Type")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    lab_order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=admin_headers,
    ).json()
    radiology_order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "RADIOLOGY", "description": "MRI"},
        headers=admin_headers,
    ).json()

    lab_only = client.get("/api/orders/worklist", params={"order_type": "LAB"}, headers=admin_headers)
    lab_ids = {row["id"] for row in lab_only.json()}
    assert lab_order["id"] in lab_ids
    assert radiology_order["id"] not in lab_ids


def test_worklist_orders_stat_before_routine(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Worklist Priority")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    routine = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "Routine CBC", "priority": "ROUTINE"},
        headers=admin_headers,
    ).json()
    stat = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "LAB", "description": "STAT Troponin", "priority": "STAT"},
        headers=admin_headers,
    ).json()

    response = client.get("/api/orders/worklist", headers=admin_headers)
    ids_in_order = [row["id"] for row in response.json()]
    assert ids_in_order.index(stat["id"]) < ids_in_order.index(routine["id"])


def test_worklist_requires_staff_auth(client, db_connection):
    response = client.get("/api/orders/worklist")
    assert response.status_code == 401


def test_worklist_is_isolated_by_hospital(client, db_connection):
    # Same "invisible against the single seeded hospital" risk this
    # session's earlier appointments.hospital_id fix (test_admin_
    # appointments.py) guarded against -- a fresh cross-patient query
    # is exactly where a hospital_id mistake would first show up.
    # Exercised against the service directly (no API to create a
    # second hospital/staff account yet, same rationale as that
    # earlier test).
    from app.services.order_services import list_worklist_orders_service

    ctx = _checked_in_context(client, db_connection, "Dr. Worklist Hospital")
    order = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=ctx["admin_headers"],
    ).json()

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT hospital_id FROM encounters WHERE id = %s", (order["encounter_id"],)
        )
        (real_hospital_id,) = cur.fetchone()

        own_hospital = list_worklist_orders_service(cur, real_hospital_id)
        assert order["id"] in {row["id"] for row in own_hospital}

        other_hospital = list_worklist_orders_service(cur, real_hospital_id + 1000000)
        assert order["id"] not in {row["id"] for row in other_hospital}
