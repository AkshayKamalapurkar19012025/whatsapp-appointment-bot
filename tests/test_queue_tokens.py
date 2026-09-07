"""
Tests for the patient queue token system (migrations/0012_appointment_
queue_tokens.sql): a token_number is assigned to an appointment the
moment it's checked in (marked Visited), scoped per doctor per doctor-
local day, plus GET /api/doctors/{id}/queue (the "now serving" view)
and the staff-triggered CHECK_IN mock notification.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _set_start_at(db_connection, appointment_id: int, start_at: datetime, end_at: datetime) -> None:
    """Direct-DB rewrite of an appointment's scheduled start/end -- same
    pattern as test_appointment_lifecycle.py's own helper of the same
    name. Needed here because _schedule_and_confirm schedules 10 days out (see
    its own comment) but /visit now requires the appointment to have
    started; token *ordering* in these tests depends on the order
    /visit is called, not on start_at, so shifting it into the past
    here doesn't affect what any of these tests are actually checking."""
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
            (start_at, end_at, appointment_id),
        )
    db_connection.commit()


def _schedule_and_confirm(client, db_connection, admin_headers, seeded, patient_name, phone_suffix, hour=9):
    # Distinct `hour` per call within the same test/doctor -- these
    # calls often schedule the same doctor for the same day (that's the
    # point, for token-ordering tests), so they need non-overlapping
    # times to each succeed rather than 409ing on slot overlap.
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
    client.post(f"/api/appointments/{created['id']}/confirm", headers=admin_headers)

    # /visit now requires the appointment to have started -- move it
    # into the recent past. Spaced an hour apart per `hour` (rather than
    # collapsed onto one instant), still well within "the past" (a full
    # day back, at most), so same-doctor appointments from different
    # `hour` calls don't overlap and trip the EXCLUDE constraint the way
    # they'd have to avoid at their *original* scheduling time too.
    anchor = datetime.now(dt_timezone.utc) - timedelta(days=1)
    past_start = anchor + timedelta(hours=hour)
    _set_start_at(db_connection, created["id"], past_start, past_start + timedelta(minutes=30))

    return {"patient": patient, "appointment_id": created["id"]}


def test_visit_assigns_sequential_token_numbers_per_doctor(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Token A",
        department_name="Token A Dept", appointment_type_name="Token A Type",
    )

    a = _schedule_and_confirm(client, db_connection, admin_headers, seeded, "Token Patient 1", 30000001, hour=9)
    b = _schedule_and_confirm(client, db_connection, admin_headers, seeded, "Token Patient 2", 30000002, hour=10)

    visit_a = client.post(f"/api/appointments/{a['appointment_id']}/visit", headers=admin_headers)
    visit_b = client.post(f"/api/appointments/{b['appointment_id']}/visit", headers=admin_headers)

    assert visit_a.status_code == 200
    assert visit_b.status_code == 200
    assert visit_a.json()["token_number"] == 1
    assert visit_b.json()["token_number"] == 2
    assert visit_a.json()["visited_at"] is not None


def test_token_numbers_are_independent_per_doctor(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded_a = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Token B1",
        department_name="Token B1 Dept", appointment_type_name="Token B1 Type",
    )
    seeded_b = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Token B2",
        department_name="Token B2 Dept", appointment_type_name="Token B2 Type",
    )

    first_for_a = _schedule_and_confirm(client, db_connection, admin_headers, seeded_a, "Token Patient 3", 30000003)
    first_for_b = _schedule_and_confirm(client, db_connection, admin_headers, seeded_b, "Token Patient 4", 30000004)

    visit_a = client.post(f"/api/appointments/{first_for_a['appointment_id']}/visit", headers=admin_headers)
    visit_b = client.post(f"/api/appointments/{first_for_b['appointment_id']}/visit", headers=admin_headers)

    # Each doctor's queue starts at 1 for the day, independent of the
    # other doctor's own check-ins.
    assert visit_a.json()["token_number"] == 1
    assert visit_b.json()["token_number"] == 1


def test_visit_sends_check_in_notification_with_token_number(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Token Notify",
        department_name="Token Notify Dept", appointment_type_name="Token Notify Type",
    )
    scheduled = _schedule_and_confirm(client, db_connection, admin_headers, seeded, "Token Notify Patient", 30000005)
    number = "+919930000005"

    response = client.post(f"/api/appointments/{scheduled['appointment_id']}/visit", headers=admin_headers)
    assert response.status_code == 200

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT kind, message_body FROM mock_sms_outbox WHERE whatsapp_number = %s",
            (number,),
        )
        rows = cur.fetchall()

    assert len(rows) == 1
    kind, message_body = rows[0]
    assert kind == "CHECK_IN"
    assert "token number is 1" in message_body
    assert "Dr. Token Notify" in message_body


def test_visit_requires_confirmed_status(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Token Pending",
        department_name="Token Pending Dept", appointment_type_name="Token Pending Type",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Token Pending Patient", "whatsapp_number": "+919930000006"},
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
    assert created["status"] == "PENDING"

    response = client.post(f"/api/appointments/{created['id']}/visit", headers=admin_headers)
    assert response.status_code == 409


# ---------------------------------------------------------------------
# GET /api/doctors/{doctor_id}/queue
# ---------------------------------------------------------------------


def test_queue_requires_staff_auth(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Queue Auth",
        department_name="Queue Auth Dept", appointment_type_name="Queue Auth Type",
    )
    response = client.get(f"/api/doctors/{seeded['doctor_id']}/queue")
    assert response.status_code == 401


def test_queue_readable_by_plain_staff(client, db_connection):
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Queue Staff",
        department_name="Queue Staff Dept", appointment_type_name="Queue Staff Type",
    )
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    response = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=staff_headers)
    assert response.status_code == 200


def test_queue_nonexistent_doctor_is_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/doctors/999999999/queue", headers=admin_headers)
    assert response.status_code == 404


def test_queue_splits_now_serving_waiting_and_completed(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Queue Split",
        department_name="Queue Split Dept", appointment_type_name="Queue Split Type",
    )

    p1 = _schedule_and_confirm(client, db_connection, admin_headers, seeded, "Queue Patient 1", 30000007, hour=9)
    p2 = _schedule_and_confirm(client, db_connection, admin_headers, seeded, "Queue Patient 2", 30000008, hour=10)
    p3 = _schedule_and_confirm(client, db_connection, admin_headers, seeded, "Queue Patient 3", 30000009, hour=11)

    # Token order: p1 -> #1, p2 -> #2, p3 -> #3.
    client.post(f"/api/appointments/{p1['appointment_id']}/visit", headers=admin_headers)
    client.post(f"/api/appointments/{p2['appointment_id']}/visit", headers=admin_headers)
    client.post(f"/api/appointments/{p3['appointment_id']}/visit", headers=admin_headers)

    # p1 gets seen and completed -- should move out of "waiting" entirely
    # (not just out of "now serving").
    client.post(f"/api/appointments/{p1['appointment_id']}/complete", headers=admin_headers)

    response = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["doctor_name"] == "Dr. Queue Split"
    assert body["now_serving"]["token_number"] == 2
    assert body["now_serving"]["patient_name"] == "Queue Patient 2"
    assert [w["token_number"] for w in body["waiting"]] == [3]
    assert [c["token_number"] for c in body["completed"]] == [1]


def test_queue_visited_at_is_doctor_local_time_not_utc(client, db_connection):
    # America/New_York -- large, unambiguous offset from UTC, same
    # choice tests/test_admin_appointments.py's own local-time test uses,
    # so a regression can't hide behind a coincidentally-small offset.
    import datetime as dt
    from zoneinfo import ZoneInfo

    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Queue TZ",
        department_name="Queue TZ Dept", appointment_type_name="Queue TZ Type",
        timezone="America/New_York",
    )
    scheduled = _schedule_and_confirm(client, db_connection, admin_headers, seeded, "Queue TZ Patient", 30000011)

    before = dt.datetime.now(ZoneInfo("America/New_York"))
    client.post(f"/api/appointments/{scheduled['appointment_id']}/visit", headers=admin_headers)
    after = dt.datetime.now(ZoneInfo("America/New_York"))

    body = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=admin_headers).json()
    visited_at = dt.datetime.fromisoformat(body["now_serving"]["visited_at"])

    assert visited_at.utcoffset() == before.utcoffset(), (
        "visited_at should carry the doctor's own UTC offset, not be UTC-normalized"
    )
    assert before <= visited_at <= after


def test_queue_empty_when_nobody_checked_in_today(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Queue Empty",
        department_name="Queue Empty Dept", appointment_type_name="Queue Empty Type",
    )
    response = client.get(f"/api/doctors/{seeded['doctor_id']}/queue", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["now_serving"] is None
    assert body["waiting"] == []
    assert body["completed"] == []


# ---------------------------------------------------------------------
# Patient-facing visibility (GET /api/web/appointments/me)
# ---------------------------------------------------------------------


def test_web_my_appointments_includes_token_number_once_checked_in(client, db_connection):
    from tests.helpers import register_and_login_web_patient

    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. Queue Web",
        department_name="Queue Web Dept", appointment_type_name="Queue Web Type",
    )
    number = "+919930000010"
    token = register_and_login_web_patient(client, number, "Queue Web Patient")

    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/web/appointments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T09:00:00+05:30",
        },
    ).json()
    assert created["status"] == "PENDING"

    client.post(f"/api/appointments/{created['id']}/confirm", headers=admin_headers)

    listing_before = client.get(
        "/api/web/appointments/me", headers={"Authorization": f"Bearer {token}"}
    ).json()
    upcoming_entry = next(a for a in listing_before["upcoming"] if a["id"] == created["id"])
    assert upcoming_entry["token_number"] is None

    # /visit now requires the appointment to have started -- move it
    # into the past *after* the "upcoming" assertion above (which needs
    # it still in the future) and before checking in.
    past_start = datetime.now(dt_timezone.utc) - timedelta(hours=1)
    _set_start_at(db_connection, created["id"], past_start, past_start + timedelta(minutes=30))

    client.post(f"/api/appointments/{created['id']}/visit", headers=admin_headers)

    listing_after = client.get(
        "/api/web/appointments/me", headers={"Authorization": f"Bearer {token}"}
    ).json()
    history_entry = next(a for a in listing_after["history"] if a["id"] == created["id"])
    assert history_entry["token_number"] == 1
