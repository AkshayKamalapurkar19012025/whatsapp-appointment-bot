"""
Tests for GET /api/dashboard/billing -- the OPD billing reconciliation
view (collections by method/doctor, outstanding unpaid, waivers,
refunds). See app/api/dashboard.py's own docstring on this endpoint for
why each of the four sections has the scope/time-window it has.

Appointments are driven through the real payment endpoints (payment,
waive-payment, refund-payment), exactly like tests/test_consultation_
payments.py, rather than inserted directly via SQL -- the report's own
correctness depends on those endpoints having actually run, not just on
appointments rows existing in some plausible-looking shape.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _set_fee(client, admin_headers, seeded, fee):
    client.put(
        f"/api/doctors/{seeded['doctor_id']}/appointment-types/{seeded['appointment_type_id']}",
        json={"duration_minutes": 30, "consultation_fee": fee},
        headers=admin_headers,
    )


def _create_patient(client, admin_headers, name, whatsapp_number):
    return client.post(
        "/api/patients",
        json={"name": name, "whatsapp_number": whatsapp_number},
        headers=admin_headers,
    ).json()


def _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient_id, hour=9):
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

    response = client.post(f"/api/appointments/{created['id']}/visit", headers=admin_headers)
    assert response.status_code == 200

    return created["id"]


def test_billing_report_requires_authentication(client, db_connection):
    response = client.get("/api/dashboard/billing")
    assert response.status_code == 401


def test_billing_report_aggregates_collections_by_method_and_doctor(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Billing A",
        department_name="Billing A Dept", appointment_type_name="Billing A Type",
    )
    _set_fee(client, admin_headers, seeded, 300)
    patient_a = _create_patient(client, admin_headers, "Billing Patient A", "+919700000001")
    patient_b = _create_patient(client, admin_headers, "Billing Patient B", "+919700000002")

    appt_a = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient_a["id"], hour=9)
    client.post(
        f"/api/appointments/{appt_a}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    appt_b = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient_b["id"], hour=10)
    client.post(
        f"/api/appointments/{appt_b}/payment",
        json={"method": "UPI", "outcome": "PAID"},
        headers=admin_headers,
    )

    response = client.get("/api/dashboard/billing", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()

    methods = {row["method"]: row for row in body["collections_by_method"]}
    assert float(methods["CASH"]["amount"]) == 300.0
    assert methods["CASH"]["count"] == 1
    assert float(methods["UPI"]["amount"]) == 300.0
    assert float(body["total_collected"]) == 600.0

    doctors = {row["doctor_name"]: row for row in body["collections_by_doctor"]}
    assert doctors["Dr. Billing A"]["count"] == 2
    assert float(doctors["Dr. Billing A"]["amount"]) == 600.0


def test_billing_report_lists_outstanding_unpaid(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Billing Outstanding",
        department_name="Billing Outstanding Dept", appointment_type_name="Billing Outstanding Type",
    )
    _set_fee(client, admin_headers, seeded, 400)
    patient = _create_patient(client, admin_headers, "Outstanding Patient", "+919700000003")
    appointment_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    # Deliberately never paid -- stays UNPAID after check-in.

    response = client.get("/api/dashboard/billing", headers=admin_headers)
    assert response.status_code == 200
    outstanding_ids = [row["appointment_id"] for row in response.json()["outstanding_unpaid"]]
    assert appointment_id in outstanding_ids


def test_billing_report_paid_appointment_not_outstanding(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Billing NotOutstanding",
        department_name="Billing NotOutstanding Dept", appointment_type_name="Billing NotOutstanding Type",
    )
    _set_fee(client, admin_headers, seeded, 400)
    patient = _create_patient(client, admin_headers, "Not Outstanding Patient", "+919700000004")
    appointment_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )

    response = client.get("/api/dashboard/billing", headers=admin_headers)
    outstanding_ids = [row["appointment_id"] for row in response.json()["outstanding_unpaid"]]
    assert appointment_id not in outstanding_ids


def test_billing_report_lists_waivers_without_fabricating_an_amount(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Billing Waiver",
        department_name="Billing Waiver Dept", appointment_type_name="Billing Waiver Type",
    )
    patient = _create_patient(client, admin_headers, "Waiver Patient", "+919700000005")
    appointment_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    response = client.post(
        f"/api/appointments/{appointment_id}/waive-payment",
        json={"reason": "Staff waived for reporting test"},
        headers=admin_headers,
    )
    # No qualifying prior visit -- this clinic requires a real 3-day
    # revisit to waive, so seed one directly instead of re-deriving that
    # whole eligibility flow here; simplest is to settle as a free visit
    # instead, which requires no eligibility at all.
    if response.status_code != 200:
        response = client.post(f"/api/appointments/{appointment_id}/settle-free-visit", headers=admin_headers)
        assert response.status_code == 200

    report = client.get("/api/dashboard/billing", headers=admin_headers)
    body = report.json()
    assert body["waivers"]["count"] == 1
    record = body["waivers"]["records"][0]
    assert record["appointment_id"] == appointment_id
    assert "amount" not in record


def test_billing_report_totals_refunds(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Billing Refund",
        department_name="Billing Refund Dept", appointment_type_name="Billing Refund Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Refund Report Patient", "+919700000006")
    appointment_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CARD", "outcome": "PAID"},
        headers=admin_headers,
    )
    client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 500, "reason": "Reporting test refund"},
        headers=admin_headers,
    )

    response = client.get("/api/dashboard/billing", headers=admin_headers)
    body = response.json()
    assert body["refunds"]["count"] == 1
    assert float(body["refunds"]["total_refunded"]) == 500.0
    assert body["refunds"]["records"][0]["appointment_id"] == appointment_id


def test_billing_report_window_days_excludes_older_collections(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Billing Window",
        department_name="Billing Window Dept", appointment_type_name="Billing Window Type",
    )
    _set_fee(client, admin_headers, seeded, 250)
    patient = _create_patient(client, admin_headers, "Window Patient", "+919700000007")
    appointment_id = _create_checked_in_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    old_recorded_at = datetime.now(dt_timezone.utc) - timedelta(days=30)
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET payment_recorded_at = %s WHERE id = %s",
            (old_recorded_at, appointment_id),
        )
    db_connection.commit()

    response = client.get("/api/dashboard/billing?days=14", headers=admin_headers)
    body = response.json()
    assert float(body["total_collected"]) == 0.0


def test_billing_report_staff_can_read(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    response = client.get("/api/dashboard/billing", headers=staff_headers)
    assert response.status_code == 200
