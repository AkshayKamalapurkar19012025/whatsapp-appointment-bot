"""
Tests for Phase 3 of the patient arrival workflow: consultation charge
lookup, payment recording, and the 7-day-revisit waiver rule
(app/services/appointment_services.py's get_consultation_charge_service,
record_payment_service, waive_consultation_fee_service, and their
app/api/appointments.py endpoints).
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


def _check_in(client, admin_headers, appointment_id):
    response = client.post(f"/api/appointments/{appointment_id}/visit", headers=admin_headers)
    assert response.status_code == 200
    return response.json()


def _set_visited_at_days_ago(db_connection, appointment_id, days_ago):
    """Backdates visited_at to a fixed, safe mid-morning IST hour (far
    from midnight in every timezone this suite uses) so day-boundary
    arithmetic in the waiver eligibility check isn't sensitive to
    exactly when the test happened to run."""
    anchor = datetime.now(dt_timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0)
    visited_at = anchor - timedelta(days=days_ago)
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET visited_at = %s WHERE id = %s",
            (visited_at, appointment_id),
        )
    db_connection.commit()


def _create_patient(client, admin_headers, name, whatsapp_number):
    return client.post(
        "/api/patients",
        json={"name": name, "whatsapp_number": whatsapp_number},
        headers=admin_headers,
    ).json()


# ---------------------------------------------------------------------
# Consultation charge lookup
# ---------------------------------------------------------------------


def test_get_charge_returns_configured_fee(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Fee A",
        department_name="Fee A Dept", appointment_type_name="Fee A Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Fee Patient A", "+919600000001")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])

    response = client.get(f"/api/appointments/{appointment_id}/charge", headers=admin_headers)
    assert response.status_code == 200
    assert float(response.json()["consultation_fee"]) == 500.0


def test_get_charge_defaults_to_zero_when_not_configured(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Fee B",
        department_name="Fee B Dept", appointment_type_name="Fee B Type",
    )
    patient = _create_patient(client, admin_headers, "Fee Patient B", "+919600000002")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])

    response = client.get(f"/api/appointments/{appointment_id}/charge", headers=admin_headers)
    assert response.status_code == 200
    assert float(response.json()["consultation_fee"]) == 0.0


# ---------------------------------------------------------------------
# Payment recording
# ---------------------------------------------------------------------


def test_record_payment_success(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Pay A",
        department_name="Pay A Dept", appointment_type_name="Pay A Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Pay Patient A", "+919600000003")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "UPI", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["payment_status"] == "PAID"
    assert body["payment_method"] == "UPI"
    assert float(body["payment_amount"]) == 500.0
    assert body["payment_recorded_at"] is not None


def test_record_payment_is_idempotent_on_double_click(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Pay B",
        department_name="Pay B Dept", appointment_type_name="Pay B Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Pay Patient B", "+919600000004")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    first = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    second = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "UPI", "outcome": "PAID"},
        headers=admin_headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    # The second call must not overwrite the first -- method/amount stay
    # exactly what was first recorded, no double charge.
    assert second.json()["payment_method"] == "CASH"
    assert first.json()["payment_recorded_at"] == second.json()["payment_recorded_at"]


def test_record_payment_failed_then_retry_succeeds(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Pay C",
        department_name="Pay C Dept", appointment_type_name="Pay C Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Pay Patient C", "+919600000005")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    failed = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CARD", "outcome": "FAILED"},
        headers=admin_headers,
    )
    assert failed.status_code == 200
    assert failed.json()["payment_status"] == "FAILED"

    retried = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert retried.status_code == 200
    assert retried.json()["payment_status"] == "PAID"
    assert retried.json()["payment_method"] == "CASH"


def test_record_payment_rejected_when_not_checked_in(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Pay D",
        department_name="Pay D Dept", appointment_type_name="Pay D Type",
    )
    patient = _create_patient(client, admin_headers, "Pay Patient D", "+919600000006")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    # Deliberately not checked in -- still CONFIRMED.

    response = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_record_payment_conflict_when_already_waived(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Pay E",
        department_name="Pay E Dept", appointment_type_name="Pay E Type",
    )
    patient = _create_patient(client, admin_headers, "Pay Patient E", "+919600000007")

    prior = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=9)
    _check_in(client, admin_headers, prior)
    client.post(f"/api/appointments/{prior}/complete", headers=admin_headers)

    current = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=10)
    _check_in(client, admin_headers, current)
    waive = client.post(
        f"/api/appointments/{current}/waive-payment",
        json={"reason": "Follow-up within 7 days"},
        headers=admin_headers,
    )
    assert waive.status_code == 200

    payment_attempt = client.post(
        f"/api/appointments/{current}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert payment_attempt.status_code == 409


# ---------------------------------------------------------------------
# Waiver: role gating
# ---------------------------------------------------------------------


def test_waive_payment_requires_admin_role(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waive Role",
        department_name="Waive Role Dept", appointment_type_name="Waive Role Type",
    )
    patient = _create_patient(client, admin_headers, "Waive Role Patient", "+919600000008")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/waive-payment",
        json={"reason": "Trying as plain staff"},
        headers=staff_headers,
    )
    assert response.status_code == 403


def test_waive_payment_requires_nonempty_reason(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waive Reason",
        department_name="Waive Reason Dept", appointment_type_name="Waive Reason Type",
    )
    patient = _create_patient(client, admin_headers, "Waive Reason Patient", "+919600000009")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/waive-payment",
        json={"reason": "   "},
        headers=admin_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------
# Waiver: 7-day-revisit-with-same-doctor eligibility
# ---------------------------------------------------------------------


def test_waive_rejected_with_no_prior_visit(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waive NoHistory",
        department_name="Waive NoHistory Dept", appointment_type_name="Waive NoHistory Type",
    )
    patient = _create_patient(client, admin_headers, "Waive NoHistory Patient", "+919600000010")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/waive-payment",
        json={"reason": "First ever visit"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_waive_accepted_at_exactly_seven_days(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waive Seven",
        department_name="Waive Seven Dept", appointment_type_name="Waive Seven Type",
    )
    patient = _create_patient(client, admin_headers, "Waive Seven Patient", "+919600000011")

    prior_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=9)
    _check_in(client, admin_headers, prior_id)
    client.post(f"/api/appointments/{prior_id}/complete", headers=admin_headers)
    _set_visited_at_days_ago(db_connection, prior_id, 7)

    current_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=10)
    _check_in(client, admin_headers, current_id)
    _set_visited_at_days_ago(db_connection, current_id, 0)

    response = client.post(
        f"/api/appointments/{current_id}/waive-payment",
        json={"reason": "Follow-up exactly 7 days later"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["payment_status"] == "WAIVED"
    assert float(response.json()["payment_amount"]) == 0.0


def test_waive_rejected_at_eight_days(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waive Eight",
        department_name="Waive Eight Dept", appointment_type_name="Waive Eight Type",
    )
    patient = _create_patient(client, admin_headers, "Waive Eight Patient", "+919600000012")

    prior_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=9)
    _check_in(client, admin_headers, prior_id)
    client.post(f"/api/appointments/{prior_id}/complete", headers=admin_headers)
    _set_visited_at_days_ago(db_connection, prior_id, 8)

    current_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=10)
    _check_in(client, admin_headers, current_id)
    _set_visited_at_days_ago(db_connection, current_id, 0)

    response = client.post(
        f"/api/appointments/{current_id}/waive-payment",
        json={"reason": "Follow-up 8 days later"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_waive_rejected_when_prior_visit_is_different_doctor(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded_a = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waive DiffA",
        department_name="Waive DiffA Dept", appointment_type_name="Waive DiffA Type",
    )
    seeded_b = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waive DiffB",
        department_name="Waive DiffB Dept", appointment_type_name="Waive DiffB Type",
    )
    patient = _create_patient(client, admin_headers, "Waive Diff Patient", "+919600000013")

    prior_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded_a, patient["id"], hour=9)
    _check_in(client, admin_headers, prior_id)
    client.post(f"/api/appointments/{prior_id}/complete", headers=admin_headers)
    _set_visited_at_days_ago(db_connection, prior_id, 2)

    current_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded_b, patient["id"], hour=10)
    _check_in(client, admin_headers, current_id)
    _set_visited_at_days_ago(db_connection, current_id, 0)

    response = client.post(
        f"/api/appointments/{current_id}/waive-payment",
        json={"reason": "Different doctor this time"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_waive_rejected_when_prior_visit_not_completed(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waive NotDone",
        department_name="Waive NotDone Dept", appointment_type_name="Waive NotDone Type",
    )
    patient = _create_patient(client, admin_headers, "Waive NotDone Patient", "+919600000014")

    prior_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=9)
    _check_in(client, admin_headers, prior_id)
    # Deliberately not completed -- still CHECKED_IN.
    _set_visited_at_days_ago(db_connection, prior_id, 2)

    current_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=10)
    _check_in(client, admin_headers, current_id)
    _set_visited_at_days_ago(db_connection, current_id, 0)

    response = client.post(
        f"/api/appointments/{current_id}/waive-payment",
        json={"reason": "Prior visit never completed"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_waive_is_idempotent(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waive Idempotent",
        department_name="Waive Idempotent Dept", appointment_type_name="Waive Idempotent Type",
    )
    patient = _create_patient(client, admin_headers, "Waive Idempotent Patient", "+919600000015")

    prior_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=9)
    _check_in(client, admin_headers, prior_id)
    client.post(f"/api/appointments/{prior_id}/complete", headers=admin_headers)
    _set_visited_at_days_ago(db_connection, prior_id, 3)

    current_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=10)
    _check_in(client, admin_headers, current_id)
    _set_visited_at_days_ago(db_connection, current_id, 0)

    first = client.post(
        f"/api/appointments/{current_id}/waive-payment",
        json={"reason": "First reason"},
        headers=admin_headers,
    )
    second = client.post(
        f"/api/appointments/{current_id}/waive-payment",
        json={"reason": "Different reason on replay"},
        headers=admin_headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    # The replay must not overwrite the original reason.
    assert second.json()["waive_reason"] == "First reason"
