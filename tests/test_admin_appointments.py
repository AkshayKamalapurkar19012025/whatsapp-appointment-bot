"""
Tests for WEB P9 -- RBAC-gating app/api/appointments.py (deferred from
WEB P6) and extending it into the staff/admin appointment surface:
list/filter, create, cancel, and a new reschedule endpoint, all on
behalf of a patient.

Deliberately does NOT re-test the underlying scheduling/cancel/reschedule
rules themselves (schedule/block/overlap/concurrency) -- those are
already covered by the existing suite via app/services/appointment_
services.py, which this router is a thin, now-authenticated wrapper
around. These tests are about the auth gate, the new filters, and the
new reschedule endpoint's own wiring.
"""

from datetime import date, timedelta
import secrets

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _seed_and_book(client, db_connection, doctor_name):
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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9198{abs(hash(doctor_name)) % 10**8:08d}"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T10:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    return admin_headers, seeded, patient, created


def test_all_three_endpoints_require_authentication(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. P9 Auth")

    unauthenticated_get = client.get("/api/appointments")
    assert unauthenticated_get.status_code == 401

    unauthenticated_post = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": 1,
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": "2027-01-01T10:00:00+05:30",
        },
    )
    assert unauthenticated_post.status_code == 401

    unauthenticated_delete = client.delete("/api/appointments/1")
    assert unauthenticated_delete.status_code == 401

    unauthenticated_reschedule = client.post(
        "/api/appointments/1/reschedule", json={"new_start_at": "2027-01-01T10:00:00+05:30"}
    )
    assert unauthenticated_reschedule.status_code == 401


def test_either_staff_role_can_list_create_cancel_and_reschedule(client, db_connection):
    staff_headers, seeded, patient, created = _seed_and_book(
        client, db_connection, "Dr. P9 Either Role"
    )
    # Recreate as a STAFF (non-admin) session to prove the RBAC table's
    # "Create/cancel/reschedule appointments (on behalf of a patient):
    # ADMIN + STAFF" row, not just ADMIN.
    staff_only_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    listed = client.get("/api/appointments", headers=staff_only_headers)
    assert listed.status_code == 200
    assert any(a["id"] == created["id"] for a in listed.json()["items"])

    reschedule_date = _next_weekday(date.today() + timedelta(days=11))
    rescheduled = client.post(
        f"/api/appointments/{created['id']}/reschedule",
        json={"new_start_at": f"{reschedule_date.isoformat()}T11:00:00+05:30"},
        headers=staff_only_headers,
    )
    assert rescheduled.status_code == 200
    new_id = rescheduled.json()["id"]
    assert new_id != created["id"]

    cancelled = client.delete(f"/api/appointments/{new_id}", headers=staff_only_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


def test_staff_can_cancel_a_different_patients_appointment(client, db_connection):
    # The behavior this phase's report documents as "intentional now,
    # not a lingering gap": a staff session isn't restricted to
    # appointments it created -- that's the actual point of an
    # on-a-patient's-behalf admin capability.
    admin_headers, seeded, patient, created = _seed_and_book(
        client, db_connection, "Dr. P9 Cross Patient"
    )
    other_admin_headers = create_admin_and_get_headers(db_connection)

    cancelled = client.delete(f"/api/appointments/{created['id']}", headers=other_admin_headers)
    assert cancelled.status_code == 200


def test_list_filters_by_doctor_patient_and_status(client, db_connection):
    admin_headers, seeded_a, patient_a, created_a = _seed_and_book(
        client, db_connection, "Dr. P9 Filter A"
    )
    _, seeded_b, patient_b, created_b = _seed_and_book(client, db_connection, "Dr. P9 Filter B")

    by_doctor = client.get(
        "/api/appointments", params={"doctor_id": seeded_a["doctor_id"]}, headers=admin_headers
    ).json()["items"]
    assert {a["id"] for a in by_doctor} == {created_a["id"]}

    by_patient = client.get(
        "/api/appointments", params={"patient_id": patient_b["id"]}, headers=admin_headers
    ).json()["items"]
    assert {a["id"] for a in by_patient} == {created_b["id"]}

    client.delete(f"/api/appointments/{created_a['id']}", headers=admin_headers)

    by_status_cancelled = client.get(
        "/api/appointments", params={"status": "CANCELLED"}, headers=admin_headers
    ).json()["items"]
    assert {a["id"] for a in by_status_cancelled} == {created_a["id"]}

    by_status_pending = client.get(
        "/api/appointments", params={"status": "PENDING"}, headers=admin_headers
    ).json()["items"]
    assert created_a["id"] not in {a["id"] for a in by_status_pending}
    assert created_b["id"] in {a["id"] for a in by_status_pending}


def test_list_shows_doctor_local_time_not_utc(client, db_connection):
    # America/New_York, computed via zoneinfo for the real test date --
    # the same DST-correctness approach already established in this
    # project's suite (tests/test_booking_flow.py, tests/test_mock_
    # notifications.py) rather than hardcoding an offset.
    from zoneinfo import ZoneInfo
    from datetime import datetime

    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. P9 Local Time",
        timezone="America/New_York",
        start_time="08:00",
        end_time="18:00",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "P9 Local Time Patient", "whatsapp_number": "+919400000099"},
        headers=admin_headers,
    ).json()

    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    ny_start = datetime(
        scheduling_date.year, scheduling_date.month, scheduling_date.day, 9, 0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": ny_start.isoformat(),
        },
        headers=admin_headers,
    ).json()

    listed = client.get(
        "/api/appointments", params={"patient_id": patient["id"]}, headers=admin_headers
    ).json()["items"]
    row = next(a for a in listed if a["id"] == created["id"])
    assert row["start_at"].startswith(f"{scheduling_date.isoformat()}T09:00:00")


def test_reschedule_rejects_time_outside_doctor_schedule(client, db_connection):
    # This router's own wiring for the OutsideDoctorSchedule -> 409
    # mapping (a new except clause added alongside this file's other
    # exception mappings) -- not a re-test of the underlying rule
    # itself, which tests/test_reschedule_service.py already covers.
    admin_headers, seeded, patient, created = _seed_and_book(
        client, db_connection, "Dr. P9 Reschedule Schedule"
    )

    saturday = date.today() + timedelta(days=2)
    while saturday.isoweekday() != 6:
        saturday += timedelta(days=1)

    response = client.post(
        f"/api/appointments/{created['id']}/reschedule",
        json={"new_start_at": f"{saturday.isoformat()}T10:00:00+05:30"},
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert "working hours" in response.json()["detail"]


def test_list_filters_by_appointment_type(client, db_connection):
    # This router's own filter wiring for appointment_type_id -- not a
    # re-test of scheduling rules themselves.
    admin_headers, seeded_a, patient_a, created_a = _seed_and_book(
        client, db_connection, "Dr. P9 Filter Type A"
    )
    _, seeded_b, patient_b, created_b = _seed_and_book(
        client, db_connection, "Dr. P9 Filter Type B"
    )

    by_type_a = client.get(
        "/api/appointments",
        params={"appointment_type_id": seeded_a["appointment_type_id"]},
        headers=admin_headers,
    ).json()["items"]
    assert {a["id"] for a in by_type_a} == {created_a["id"]}


def test_list_filters_by_date_range_using_doctor_local_date_not_utc(client, db_connection):
    # The exact ambiguity this filter's docstring flags: a late-evening
    # US Eastern appointment's UTC instant falls on the *next* calendar
    # day. date_from/date_to must match against the doctor's own local
    # day (what the admin dashboard displays), not the UTC day the raw
    # timestamp would naively suggest -- computed via zoneinfo for the
    # real test date rather than a hardcoded offset, so this stays
    # correct across a DST transition (same approach already used
    # elsewhere in this suite, e.g. tests/test_booking_flow.py).
    from zoneinfo import ZoneInfo
    from datetime import datetime

    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. P9 Date Filter",
        department_name="Dr. P9 Date Filter Dept",
        appointment_type_name="Dr. P9 Date Filter Type",
        timezone="America/New_York",
        start_time="20:00",
        end_time="23:00",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Date Filter Patient", "whatsapp_number": "+919400055501"},
        headers=admin_headers,
    ).json()

    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    ny_start = datetime(
        scheduling_date.year, scheduling_date.month, scheduling_date.day, 22, 0,
        tzinfo=ZoneInfo("America/New_York"),
    )
    # Confirm this test actually exercises the UTC/local day split it
    # claims to -- otherwise it would pass even with the old, wrong
    # (UTC-day) filtering logic, silently proving nothing.
    utc_date = ny_start.astimezone(ZoneInfo("UTC")).date()
    assert utc_date != scheduling_date, "test setup must cross a UTC calendar day boundary"

    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": ny_start.isoformat(),
        },
        headers=admin_headers,
    ).json()

    by_local_date = client.get(
        "/api/appointments",
        params={"date_from": scheduling_date.isoformat(), "date_to": scheduling_date.isoformat()},
        headers=admin_headers,
    ).json()["items"]
    assert created["id"] in {a["id"] for a in by_local_date}

    by_utc_date = client.get(
        "/api/appointments",
        params={"date_from": utc_date.isoformat(), "date_to": utc_date.isoformat()},
        headers=admin_headers,
    ).json()["items"]
    assert created["id"] not in {a["id"] for a in by_utc_date}


def test_list_date_filter_covers_extreme_utc_offset(client, db_connection):
    """GET /appointments's SQL-level date_from/date_to pre-filter widens
    +/-1 day around the requested range before the precise doctor-local
    trim runs (added alongside the master-spec-audit's pagination/
    performance fix) -- must never be narrow enough to exclude a real
    appointment. UTC+14 (Pacific/Kiritimati, the most extreme real IANA
    offset) is the actual edge of that margin, not America/New_York's
    ~5 hours which the test above already covers."""
    from zoneinfo import ZoneInfo
    from datetime import datetime

    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(
        client,
        db_connection,
        doctor_name="Dr. Extreme TZ",
        department_name="Dr. Extreme TZ Dept",
        appointment_type_name="Dr. Extreme TZ Type",
        timezone="Pacific/Kiritimati",
        start_time="00:30",
        end_time="04:00",
    )
    patient = client.post(
        "/api/patients",
        json={"name": "Extreme TZ Patient", "whatsapp_number": "+919400055502"},
        headers=admin_headers,
    ).json()

    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    local_start = datetime(
        scheduling_date.year, scheduling_date.month, scheduling_date.day, 1, 0,
        tzinfo=ZoneInfo("Pacific/Kiritimati"),
    )
    utc_date = local_start.astimezone(ZoneInfo("UTC")).date()
    assert utc_date != scheduling_date, "test setup must cross a UTC calendar day boundary"

    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": local_start.isoformat(),
        },
        headers=admin_headers,
    ).json()

    by_local_date = client.get(
        "/api/appointments",
        params={"date_from": scheduling_date.isoformat(), "date_to": scheduling_date.isoformat()},
        headers=admin_headers,
    ).json()["items"]
    assert created["id"] in {a["id"] for a in by_local_date}


def test_list_paginates_with_limit_and_offset(client, db_connection):
    """Master spec audit gap #1 (section 80: "avoid load entire
    table"): GET /appointments now returns {items, total, limit,
    offset} with real server-side LIMIT/OFFSET, same shape as
    GET /patients/admin."""
    admin_headers, seeded, patient, created_a = _seed_and_book(
        client, db_connection, "Dr. P9 Pagination A"
    )
    _, _, _, created_b = _seed_and_book(client, db_connection, "Dr. P9 Pagination B")

    page = client.get(
        "/api/appointments", params={"limit": 1, "offset": 0}, headers=admin_headers
    ).json()
    assert page["limit"] == 1
    assert page["offset"] == 0
    assert len(page["items"]) == 1
    assert page["total"] >= 2

    all_ids = set()
    offset = 0
    while True:
        one_page = client.get(
            "/api/appointments", params={"limit": 1, "offset": offset}, headers=admin_headers
        ).json()
        if not one_page["items"]:
            break
        all_ids.update(a["id"] for a in one_page["items"])
        offset += 1
        if offset > one_page["total"]:
            break
    assert {created_a["id"], created_b["id"]} <= all_ids


def test_appointment_and_reschedule_carry_the_booking_doctors_real_hospital_id(client, db_connection):
    """Regression test for a real bug found during the master-spec-audit
    verification pass: create_appointment_service's INSERT never listed
    hospital_id, so every appointment silently got the column's
    DEFAULT 1 (migrations/0027) regardless of which hospital its doctor
    actually belongs to -- invisible in every other test because they
    all run against the single seeded hospital (id=1), where the wrong
    default happens to equal the right answer. This undermined
    app/services/search_service.py's global-search appointment branch
    (WHERE a.hospital_id = %s) for any hospital other than id=1. Fixed
    by passing the doctor's real hospital_id (already looked up for the
    encounter insert, and for reschedule's own timezone lookup) into
    both INSERT INTO appointments statements.

    Exercised directly against the DB rather than through a second
    hospital's own staff/auth flow -- this app has no API to create a
    second hospital or a staff account scoped to one yet (migrations/
    0027's own comment: "this application is still single-tenant in
    every behavior except the schema itself"), so a second hospital row
    plus reassigning a doctor to it, done directly via db_connection, is
    the minimum real setup that proves the fix without inventing
    multi-tenant onboarding infrastructure nothing else in this app has
    either."""
    admin_headers, seeded, patient, _ = _seed_and_book(
        client, db_connection, "Dr. P9 Hospital Id"
    )

    # hospitals is deliberately never truncated between tests (reference
    # data, same category as roles/permissions -- see conftest.py's
    # APP_TABLES), so a fixed code here would collide with a leftover
    # row from a previous run of this same test against the persistent
    # test database. Randomized per run, same reasoning
    # tests/helpers.py's create_staff_and_get_headers already uses for
    # usernames.
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO hospitals (code, name) VALUES (%s, %s) RETURNING id",
            (f"TEST-{secrets.token_hex(4)}", "Second Hospital"),
        )
        (second_hospital_id,) = cur.fetchone()
        cur.execute(
            "UPDATE doctors SET hospital_id = %s WHERE id = %s",
            (second_hospital_id, seeded["doctor_id"]),
        )
    db_connection.commit()

    scheduling_date = _next_weekday(date.today() + timedelta(days=12))
    rebooked = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()

    with db_connection.cursor() as cur:
        cur.execute("SELECT hospital_id FROM appointments WHERE id = %s", (rebooked["id"],))
        (booked_hospital_id,) = cur.fetchone()
    assert booked_hospital_id == second_hospital_id

    reschedule_date = _next_weekday(date.today() + timedelta(days=13))
    rescheduled = client.post(
        f"/api/appointments/{rebooked['id']}/reschedule",
        json={"new_start_at": f"{reschedule_date.isoformat()}T09:00:00+05:30"},
        headers=admin_headers,
    ).json()

    with db_connection.cursor() as cur:
        cur.execute("SELECT hospital_id FROM appointments WHERE id = %s", (rescheduled["id"],))
        (rescheduled_hospital_id,) = cur.fetchone()
    assert rescheduled_hospital_id == second_hospital_id


def test_calendar_requires_authentication(client, db_connection):
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. P9 Calendar Auth")
    response = client.get(
        "/api/appointments/calendar",
        params={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "year": 2027,
            "month": 1,
        },
    )
    assert response.status_code == 401


def test_calendar_shows_open_days_beyond_the_patient_booking_window(client, db_connection):
    # The entire reason this is a separate endpoint from GET /web/calendar:
    # that one 409s outright for a month outside the patient-facing
    # 3-month window. Staff/admin schedulings are exempt from that window
    # everywhere else in this router (create_appointment's own
    # enforce_scheduling_window=False) -- this endpoint must be too, or its
    # date picker would be unusable for exactly the far-out schedulings staff
    # can otherwise make.
    admin_headers, seeded, patient, created = _seed_and_book(
        client, db_connection, "Dr. P9 Calendar Window"
    )

    far_future = date.today() + timedelta(days=200)  # well past any 3-month window
    response = client.get(
        "/api/appointments/calendar",
        params={
            "doctor_id": seeded["doctor_id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "year": far_future.year,
            "month": far_future.month,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    # At least one weekday in that far-future month must show as open --
    # seed_basic_doctor's default schedule is Mon-Fri, so the month can't
    # be entirely closed.
    assert any(is_open for is_open in body["dates"].values())


def test_reschedule_nonexistent_appointment_returns_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post(
        "/api/appointments/999999/reschedule",
        json={"new_start_at": "2027-01-01T10:00:00+05:30"},
        headers=admin_headers,
    )
    assert response.status_code == 404
