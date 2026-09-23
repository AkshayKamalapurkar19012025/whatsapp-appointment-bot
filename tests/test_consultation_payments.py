"""
Tests for Phase 3 of the patient arrival workflow: consultation charge
lookup, payment recording, and the 3-day-revisit waiver rule
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
    # Phase 4: successful payment is the queue-entry trigger.
    assert body["token_number"] == 1
    assert body["token_just_issued"] is True
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
    # Only one token ever issued -- the replay must not renumber or
    # re-notify.
    assert first.json()["token_number"] == 1
    assert second.json()["token_number"] == 1
    assert first.json()["token_just_issued"] is True
    assert second.json()["token_just_issued"] is False


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
    # A failed attempt must never issue a token -- the patient stays
    # outside the queue.
    assert failed.json()["token_number"] is None

    retried = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert retried.status_code == 200
    assert retried.json()["payment_status"] == "PAID"
    assert retried.json()["token_number"] == 1
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
        json={"reason": "Follow-up within 3 days"},
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
# Waiver: 3-day-revisit-with-same-doctor eligibility
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


def test_waive_accepted_at_exactly_three_days(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waive Three",
        department_name="Waive Three Dept", appointment_type_name="Waive Three Type",
    )
    patient = _create_patient(client, admin_headers, "Waive Three Patient", "+919600000011")

    prior_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=9)
    _check_in(client, admin_headers, prior_id)
    client.post(f"/api/appointments/{prior_id}/complete", headers=admin_headers)
    _set_visited_at_days_ago(db_connection, prior_id, 3)

    current_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=10)
    _check_in(client, admin_headers, current_id)
    _set_visited_at_days_ago(db_connection, current_id, 0)

    response = client.post(
        f"/api/appointments/{current_id}/waive-payment",
        json={"reason": "Follow-up exactly 3 days later"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["payment_status"] == "WAIVED"
    assert float(response.json()["payment_amount"]) == 0.0
    # Waiver is a queue-entry trigger too, same as a successful payment.
    assert response.json()["token_number"] == 1
    assert response.json()["token_just_issued"] is True


def test_waive_rejected_at_four_days(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Waive Four",
        department_name="Waive Four Dept", appointment_type_name="Waive Four Type",
    )
    patient = _create_patient(client, admin_headers, "Waive Four Patient", "+919600000012")

    prior_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=9)
    _check_in(client, admin_headers, prior_id)
    client.post(f"/api/appointments/{prior_id}/complete", headers=admin_headers)
    _set_visited_at_days_ago(db_connection, prior_id, 4)

    current_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"], hour=10)
    _check_in(client, admin_headers, current_id)
    _set_visited_at_days_ago(db_connection, current_id, 0)

    response = client.post(
        f"/api/appointments/{current_id}/waive-payment",
        json={"reason": "Follow-up 4 days later"},
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
    # Nor renumber the token or re-notify.
    assert first.json()["token_number"] == 1
    assert second.json()["token_number"] == 1
    assert first.json()["token_just_issued"] is True
    assert second.json()["token_just_issued"] is False


# ---------------------------------------------------------------------
# Settle free visit (settle_free_visit_service, POST .../settle-free-visit)
#
# The OPD front-desk flow's "Payment Required? No" branch -- distinct
# from waive-payment above, which is the ADMIN-only, 3-day-revisit
# waiver for a REAL configured fee. This is for a visit with no fee
# configured at all (consultation_fee == 0): seed_basic_doctor's
# default, unless a test calls _set_fee, matching every other test in
# this file -- these are the only tests in the suite that rely on that
# zero-fee default meaning "nothing to collect" rather than "not yet
# configured".
# ---------------------------------------------------------------------


def test_settle_free_visit_generates_token_without_payment(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Free Visit",
        department_name="Free Visit Dept", appointment_type_name="Free Visit Type",
    )
    patient = _create_patient(client, admin_headers, "Free Visit Patient", "+919600000016")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    response = client.post(f"/api/appointments/{appointment_id}/settle-free-visit", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["payment_status"] == "WAIVED"
    assert body["payment_method"] is None
    assert body["token_number"] == 1
    assert body["token_just_issued"] is True
    assert body["waive_reason"] == "No consultation fee configured for this visit"


def test_settle_free_visit_accepts_plain_staff_role(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Free Visit Staff",
        department_name="Free Visit Staff Dept", appointment_type_name="Free Visit Staff Type",
    )
    patient = _create_patient(client, admin_headers, "Free Visit Staff Patient", "+919600000017")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    response = client.post(f"/api/appointments/{appointment_id}/settle-free-visit", headers=staff_headers)
    assert response.status_code == 200


def test_settle_free_visit_is_idempotent(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Free Visit Idempotent",
        department_name="Free Visit Idempotent Dept", appointment_type_name="Free Visit Idempotent Type",
    )
    patient = _create_patient(client, admin_headers, "Free Visit Idempotent Patient", "+919600000018")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    first = client.post(f"/api/appointments/{appointment_id}/settle-free-visit", headers=admin_headers)
    second = client.post(f"/api/appointments/{appointment_id}/settle-free-visit", headers=admin_headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["token_number"] == second.json()["token_number"] == 1
    assert first.json()["token_just_issued"] is True
    assert second.json()["token_just_issued"] is False


def test_settle_free_visit_rejected_when_fee_is_configured(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Not Free",
        department_name="Not Free Dept", appointment_type_name="Not Free Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Not Free Patient", "+919600000019")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    response = client.post(f"/api/appointments/{appointment_id}/settle-free-visit", headers=admin_headers)
    assert response.status_code == 409

    # Must not have silently issued a token or changed payment_status.
    charge_check = client.get(f"/api/appointments/{appointment_id}/charge", headers=admin_headers)
    assert float(charge_check.json()["consultation_fee"]) == 500.0


def test_settle_free_visit_rejected_when_already_paid(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Already Paid Free",
        department_name="Already Paid Free Dept", appointment_type_name="Already Paid Free Type",
    )
    patient = _create_patient(client, admin_headers, "Already Paid Free Patient", "+919600000020")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    paid = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert paid.status_code == 200

    response = client.post(f"/api/appointments/{appointment_id}/settle-free-visit", headers=admin_headers)
    assert response.status_code == 409


def test_settle_free_visit_rejected_when_not_checked_in(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Free Not Checked In",
        department_name="Free Not Checked In Dept", appointment_type_name="Free Not Checked In Type",
    )
    patient = _create_patient(client, admin_headers, "Free Not Checked In Patient", "+919600000021")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    # Deliberately not checked in yet.

    response = client.post(f"/api/appointments/{appointment_id}/settle-free-visit", headers=admin_headers)
    assert response.status_code == 409


def test_settle_free_visit_requires_authentication(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Free No Auth",
        department_name="Free No Auth Dept", appointment_type_name="Free No Auth Type",
    )
    patient = _create_patient(client, admin_headers, "Free No Auth Patient", "+919600000022")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    response = client.post(f"/api/appointments/{appointment_id}/settle-free-visit")
    assert response.status_code == 401


# ---------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------


def _pay(client, admin_headers, appointment_id, method="CASH"):
    response = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": method, "outcome": "PAID"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    return response.json()


def test_refund_full_amount_succeeds(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Refund A",
        department_name="Refund A Dept", appointment_type_name="Refund A Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Refund Patient A", "+919600000023")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    _pay(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 500, "reason": "Patient double-charged at front desk"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["payment_status"] == "REFUNDED"
    assert float(body["refund_amount"]) == 500.0
    assert body["refund_reason"] == "Patient double-charged at front desk"
    assert body["refunded_at"] is not None
    # The original payment record is preserved, not overwritten.
    assert float(body["payment_amount"]) == 500.0


def test_refund_partial_amount_succeeds(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Refund B",
        department_name="Refund B Dept", appointment_type_name="Refund B Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Refund Patient B", "+919600000024")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    _pay(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 200, "reason": "Partial goodwill refund"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert float(response.json()["refund_amount"]) == 200.0


def test_refund_rejected_when_not_paid(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Refund NoPay",
        department_name="Refund NoPay Dept", appointment_type_name="Refund NoPay Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Refund NoPay Patient", "+919600000025")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    # Deliberately never paid.

    response = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 500, "reason": "Never actually paid"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_refund_rejected_when_already_refunded(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Refund Twice",
        department_name="Refund Twice Dept", appointment_type_name="Refund Twice Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Refund Twice Patient", "+919600000026")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    _pay(client, admin_headers, appointment_id)

    first = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 500, "reason": "First refund"},
        headers=admin_headers,
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 500, "reason": "Second refund attempt"},
        headers=admin_headers,
    )
    assert second.status_code == 409


def test_refund_rejected_when_amount_exceeds_payment(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Refund TooMuch",
        department_name="Refund TooMuch Dept", appointment_type_name="Refund TooMuch Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Refund TooMuch Patient", "+919600000027")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    _pay(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 501, "reason": "Trying to refund more than was paid"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_refund_requires_admin_role(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Refund Role",
        department_name="Refund Role Dept", appointment_type_name="Refund Role Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Refund Role Patient", "+919600000028")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    _pay(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 500, "reason": "Trying as plain staff"},
        headers=staff_headers,
    )
    assert response.status_code == 403


def test_refund_requires_positive_amount(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Refund Zero",
        department_name="Refund Zero Dept", appointment_type_name="Refund Zero Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Refund Zero Patient", "+919600000029")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    _pay(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 0, "reason": "Zero amount refund"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_refund_requires_nonempty_reason(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Refund Reason",
        department_name="Refund Reason Dept", appointment_type_name="Refund Reason Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Refund Reason Patient", "+919600000030")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    _pay(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 500, "reason": "   "},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_refund_allowed_after_visit_completed(client, db_connection):
    """A refund is a back-office correction, not a queue-entry action --
    unlike payment/waive/settle-free-visit, it must still work once the
    appointment has moved past CHECKED_IN to COMPLETED (e.g. a billing
    error noticed after the patient has already seen the doctor)."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Refund AfterComplete",
        department_name="Refund AfterComplete Dept", appointment_type_name="Refund AfterComplete Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Refund AfterComplete Patient", "+919600000031")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    _pay(client, admin_headers, appointment_id)

    completed = client.post(f"/api/appointments/{appointment_id}/complete", headers=admin_headers)
    assert completed.status_code == 200

    response = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 500, "reason": "Billing error found after visit"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["payment_status"] == "REFUNDED"


def test_refund_requires_authentication(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Refund NoAuth",
        department_name="Refund NoAuth Dept", appointment_type_name="Refund NoAuth Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Refund NoAuth Patient", "+919600000032")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    _pay(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 500, "reason": "No auth header"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------
# Itemized invoicing: ad-hoc line items on top of the consultation fee
# ---------------------------------------------------------------------


def test_invoice_defaults_to_just_the_consultation_fee(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Invoice A",
        department_name="Invoice A Dept", appointment_type_name="Invoice A Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Invoice Patient A", "+919600000033")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    response = client.get(f"/api/appointments/{appointment_id}/invoice", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["line_items"] == []
    assert float(body["consultation_fee"]) == 500.0
    assert float(body["extra_charges_total"]) == 0.0
    assert float(body["total_due"]) == 500.0
    assert body["invoice_number"].startswith("INV-")


def test_add_line_item_increases_total_due_and_amount_charged(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Invoice B",
        department_name="Invoice B Dept", appointment_type_name="Invoice B Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Invoice Patient B", "+919600000034")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    added = client.post(
        f"/api/appointments/{appointment_id}/invoice/line-items",
        json={"description": "Dressing charge", "amount": 150},
        headers=admin_headers,
    )
    assert added.status_code == 200
    assert len(added.json()["line_items"]) == 1
    assert float(added.json()["total_due"]) == 650.0

    invoice = client.get(f"/api/appointments/{appointment_id}/invoice", headers=admin_headers).json()
    assert float(invoice["extra_charges_total"]) == 150.0
    assert float(invoice["total_due"]) == 650.0

    payment = client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )
    assert payment.status_code == 200
    assert float(payment.json()["payment_amount"]) == 650.0


def test_multiple_line_items_sum_into_total_due(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Invoice C",
        department_name="Invoice C Dept", appointment_type_name="Invoice C Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Invoice Patient C", "+919600000035")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    client.post(
        f"/api/appointments/{appointment_id}/invoice/line-items",
        json={"description": "Dressing charge", "amount": 150},
        headers=admin_headers,
    )
    client.post(
        f"/api/appointments/{appointment_id}/invoice/line-items",
        json={"description": "Injection charge", "amount": 75},
        headers=admin_headers,
    )

    invoice = client.get(f"/api/appointments/{appointment_id}/invoice", headers=admin_headers).json()
    assert len(invoice["line_items"]) == 2
    assert float(invoice["extra_charges_total"]) == 225.0
    assert float(invoice["total_due"]) == 725.0


def test_line_item_rejected_after_payment(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Invoice Frozen",
        department_name="Invoice Frozen Dept", appointment_type_name="Invoice Frozen Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Invoice Frozen Patient", "+919600000036")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    client.post(
        f"/api/appointments/{appointment_id}/payment",
        json={"method": "CASH", "outcome": "PAID"},
        headers=admin_headers,
    )

    response = client.post(
        f"/api/appointments/{appointment_id}/invoice/line-items",
        json={"description": "Too late charge", "amount": 100},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_line_item_requires_admin_role(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Invoice Role",
        department_name="Invoice Role Dept", appointment_type_name="Invoice Role Type",
    )
    patient = _create_patient(client, admin_headers, "Invoice Role Patient", "+919600000037")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/invoice/line-items",
        json={"description": "Trying as plain staff", "amount": 100},
        headers=staff_headers,
    )
    assert response.status_code == 403


def test_line_item_requires_positive_amount(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Invoice Zero",
        department_name="Invoice Zero Dept", appointment_type_name="Invoice Zero Type",
    )
    patient = _create_patient(client, admin_headers, "Invoice Zero Patient", "+919600000038")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)

    response = client.post(
        f"/api/appointments/{appointment_id}/invoice/line-items",
        json={"description": "Zero charge", "amount": 0},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_settle_free_visit_rejected_when_line_item_added(client, db_connection):
    """A visit with a real ad-hoc charge on it isn't free just because
    the base consultation_fee is 0."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Invoice NotFree",
        department_name="Invoice NotFree Dept", appointment_type_name="Invoice NotFree Type",
    )
    # No fee configured -- consultation_fee defaults to 0.
    patient = _create_patient(client, admin_headers, "Invoice NotFree Patient", "+919600000039")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    client.post(
        f"/api/appointments/{appointment_id}/invoice/line-items",
        json={"description": "Dressing charge", "amount": 150},
        headers=admin_headers,
    )

    response = client.post(f"/api/appointments/{appointment_id}/settle-free-visit", headers=admin_headers)
    assert response.status_code == 409


def test_admin_listing_includes_invoice_number_and_refund_fields(client, db_connection):
    """GET /appointments (the admin listing AppointmentDetailsModal reads
    from) must carry invoice_number always, and the refund columns once
    a refund is recorded -- these are looked up separately from GET
    .../invoice and GET .../charge, so the listing needs its own copy
    rather than the frontend having to fetch a second endpoint per row
    just to show a refund reason on reopen."""
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Listing Refund",
        department_name="Listing Refund Dept", appointment_type_name="Listing Refund Type",
    )
    _set_fee(client, admin_headers, seeded, 500)
    patient = _create_patient(client, admin_headers, "Listing Refund Patient", "+919600000040")
    appointment_id = _create_confirmed_started_appointment(client, db_connection, admin_headers, seeded, patient["id"])
    _check_in(client, admin_headers, appointment_id)
    _pay(client, admin_headers, appointment_id)

    before = client.get("/api/appointments", headers=admin_headers).json()["items"]
    row_before = next(a for a in before if a["id"] == appointment_id)
    assert row_before["invoice_number"].startswith("INV-")
    assert row_before["refund_amount"] is None
    assert row_before["refund_reason"] is None
    assert row_before["refunded_at"] is None

    client.post(
        f"/api/appointments/{appointment_id}/refund-payment",
        json={"amount": 500, "reason": "Listing visibility check"},
        headers=admin_headers,
    )

    after = client.get("/api/appointments", headers=admin_headers).json()["items"]
    row_after = next(a for a in after if a["id"] == appointment_id)
    assert row_after["payment_status"] == "REFUNDED"
    assert float(row_after["refund_amount"]) == 500.0
    assert row_after["refund_reason"] == "Listing visibility check"
    assert row_after["refunded_at"] is not None
