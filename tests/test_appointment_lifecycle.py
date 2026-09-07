"""
Tests for the four staff-driven appointment lifecycle transitions added
in migrations/0011_appointment_lifecycle_statuses.sql:
POST /api/appointments/{id}/confirm, /reject, /visit, /complete.

Every appointment now starts PENDING (create_appointment_service, used
by WhatsApp, patient web scheduling, and this same admin create endpoint)
and moves forward one step at a time:
    PENDING -> CONFIRMED -> CHECKED_IN -> COMPLETED
       \\-> REJECTED

Each endpoint only accepts its one specific starting status -- confirm
only from PENDING, reject only from PENDING, visit only from CONFIRMED
(status -> CHECKED_IN, renamed from VISITED in migrations/0015),
complete only from CHECKED_IN -- rejecting everything else with 409.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _seed_and_schedule(client, db_connection, doctor_name: str) -> dict:
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
    # Doctor-local wall-clock string, deliberately kept alongside the
    # response below -- create_appointment_service's returned start_at
    # is read back from Postgres and comes back UTC-labeled (the same
    # "UTC-normalized on read-back" characteristic documented throughout
    # this codebase, e.g. appointment_services.py's reschedule test),
    # NOT the doctor's local wall-clock time. Reusing that UTC-labeled
    # value to target "the same slot" in a later request would shift
    # which doctor_schedule row it's checked against and spuriously fail
    # -- callers that need to re-target this exact slot must reuse this
    # original request string, not the response.
    start_at_local = f"{scheduling_date.isoformat()}T09:00:00+05:30"
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": start_at_local,
        },
        headers=admin_headers,
    ).json()
    assert created["status"] == "PENDING"

    return {
        "admin_headers": admin_headers,
        "seeded": seeded,
        "patient": patient,
        "appointment": created,
        "start_at_local": start_at_local,
    }


# ---------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------


def test_confirm_requires_staff_auth(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Confirm Auth")
    response = client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm")
    assert response.status_code == 401


def test_confirm_pending_appointment_succeeds(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Confirm Normal")
    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CONFIRMED"

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (ctx["appointment"]["id"],))
        assert cur.fetchone()[0] == "CONFIRMED"


def test_confirm_nonexistent_appointment_is_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post("/api/appointments/999999999/confirm", headers=admin_headers)
    assert response.status_code == 404


def test_confirm_already_confirmed_appointment_is_409(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Confirm Twice")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------


def test_reject_pending_appointment_succeeds_and_releases_slot(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Reject Normal")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/reject", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"

    # A Rejected appointment releases its slot (migrations/0011's
    # RELEASED_STATUSES) -- a brand new scheduling for the exact same
    # doctor/time must now succeed rather than 409 on overlap.
    other_patient = client.post(
        "/api/patients",
        json={"name": "Reject Slot Reuser", "whatsapp_number": "+919912340001"},
        headers=ctx["admin_headers"],
    ).json()
    retry = client.post(
        "/api/appointments",
        json={
            "doctor_id": ctx["seeded"]["doctor_id"],
            "patient_id": other_patient["id"],
            "appointment_type_id": ctx["seeded"]["appointment_type_id"],
            "start_at": ctx["start_at_local"],
        },
        headers=ctx["admin_headers"],
    )
    assert retry.status_code == 200


def test_reject_confirmed_appointment_is_409(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Reject Confirmed")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/reject", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------
# Visit
# ---------------------------------------------------------------------


def _set_start_at(db_connection, appointment_id: int, start_at: datetime, end_at: datetime) -> None:
    """Directly rewrite an appointment's scheduled start/end -- same
    direct-DB-manipulation pattern as test_reschedule_service.py and
    test_exclusion_constraint.py, used here because _seed_and_schedule
    deliberately schedules 10 days out (see its own comment) and the tests
    below need to control exactly how far in the past/future start_at
    is relative to "now" at assertion time, which the scheduling API
    itself has no lever for."""
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
            (start_at, end_at, appointment_id),
        )
    db_connection.commit()


def test_visit_confirmed_appointment_succeeds(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Visit Normal")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    # _seed_and_schedule schedules 10 days out -- move start_at into the past so
    # this exercises the ordinary "appointment already started" case.
    past_start = datetime.now(dt_timezone.utc) - timedelta(hours=1)
    _set_start_at(db_connection, ctx["appointment"]["id"], past_start, past_start + timedelta(minutes=30))

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CHECKED_IN"


def test_visit_pending_appointment_is_409(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Visit Pending")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


def test_visit_future_confirmed_appointment_is_409(client, db_connection):
    # _seed_and_schedule already schedules 10 days out, so this appointment
    # hasn't started -- no start_at manipulation needed.
    ctx = _seed_and_schedule(client, db_connection, "Dr. Visit Future")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409
    assert "not started" in response.json()["detail"].lower()

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (ctx["appointment"]["id"],))
        assert cur.fetchone()[0] == "CONFIRMED", "a rejected visit attempt must not change status"


def test_visit_appointment_a_minute_before_start_is_409(client, db_connection):
    # Boundary case: still-future by a small margin, not merely "far in
    # the future" -- proves the check compares real instants, not dates.
    ctx = _seed_and_schedule(client, db_connection, "Dr. Visit Boundary Future")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    future_start = datetime.now(dt_timezone.utc) + timedelta(minutes=1)
    _set_start_at(db_connection, ctx["appointment"]["id"], future_start, future_start + timedelta(minutes=30))

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


def test_visit_appointment_just_after_start_succeeds(client, db_connection):
    # Boundary case: just started, not "long past" -- the other half of
    # the same instant-comparison proof as the test above.
    ctx = _seed_and_schedule(client, db_connection, "Dr. Visit Boundary Past")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    just_started = datetime.now(dt_timezone.utc) - timedelta(seconds=5)
    _set_start_at(db_connection, ctx["appointment"]["id"], just_started, just_started + timedelta(minutes=30))

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CHECKED_IN"


def test_visit_same_local_day_but_still_future_slot_is_409(client, db_connection):
    # Guards against a naive "is it today (doctor-local)" check standing
    # in for a real instant comparison: this appointment's start_at is
    # later on the *same* doctor-local calendar day, several hours from
    # now -- a date-only check would wrongly treat "today" as startable
    # regardless of time-of-day. seed_basic_doctor's default timezone is
    # Asia/Kolkata (UTC+5:30), deliberately not UTC, so this also proves
    # the comparison isn't accidentally UTC-datebound either.
    ctx = _seed_and_schedule(client, db_connection, "Dr. Visit Same Day Future")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    doctor_tz = dt_timezone(timedelta(hours=5, minutes=30))
    now_local = datetime.now(doctor_tz)
    later_today = now_local.replace(hour=23, minute=0, second=0, microsecond=0)
    if later_today <= now_local:
        later_today = now_local + timedelta(hours=1)
    _set_start_at(db_connection, ctx["appointment"]["id"], later_today, later_today + timedelta(minutes=30))

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


def test_visit_cancelled_appointment_is_409(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Visit Cancelled")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])
    client.delete(f"/api/appointments/{ctx['appointment']['id']}", headers=ctx["admin_headers"])

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (ctx["appointment"]["id"],))
        assert cur.fetchone()[0] == "CANCELLED"

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


def test_visit_already_visited_appointment_is_409(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Visit Twice")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    past_start = datetime.now(dt_timezone.utc) - timedelta(hours=1)
    _set_start_at(db_connection, ctx["appointment"]["id"], past_start, past_start + timedelta(minutes=30))

    first = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"]
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"]
    )
    assert second.status_code == 409


# ---------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------


def test_complete_visited_appointment_succeeds(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Complete Normal")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    # _seed_and_schedule schedules 10 days out -- move start_at into the past so
    # /visit (now gated on the appointment having started) succeeds.
    past_start = datetime.now(dt_timezone.utc) - timedelta(hours=1)
    _set_start_at(db_connection, ctx["appointment"]["id"], past_start, past_start + timedelta(minutes=30))

    client.post(f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/complete", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"


def test_complete_confirmed_but_not_visited_appointment_is_409(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Complete Skip Visit")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/complete", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------
# No-Show (migrations/0015 -- manual front-desk action only, no
# cron/scheduler; see mark_no_show_service)
# ---------------------------------------------------------------------


def test_no_show_confirmed_appointment_succeeds(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. NoShow Normal")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    # _seed_and_schedule schedules 10 days out -- move start_at into the past,
    # same as the /visit tests: you can't yet know a patient won't show
    # up for a slot that hasn't happened yet (see mark_no_show_service's
    # AppointmentNotStarted guard).
    past_start = datetime.now(dt_timezone.utc) - timedelta(hours=1)
    _set_start_at(db_connection, ctx["appointment"]["id"], past_start, past_start + timedelta(minutes=30))

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/no-show", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200
    assert response.json()["status"] == "NO_SHOW"

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (ctx["appointment"]["id"],))
        assert cur.fetchone()[0] == "NO_SHOW"


def test_no_show_future_confirmed_appointment_is_409(client, db_connection):
    # _seed_and_schedule already schedules 10 days out, so this appointment
    # hasn't started -- no start_at manipulation needed. Mirrors
    # test_visit_future_confirmed_appointment_is_409 above.
    ctx = _seed_and_schedule(client, db_connection, "Dr. NoShow Future")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/no-show", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409
    assert "not started" in response.json()["detail"].lower()

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (ctx["appointment"]["id"],))
        assert cur.fetchone()[0] == "CONFIRMED", "a rejected no-show attempt must not change status"


def test_no_show_pending_appointment_is_409(client, db_connection):
    # An unapproved request isn't a no-show -- it's a different,
    # out-of-scope problem (see mark_no_show_service's own docstring).
    ctx = _seed_and_schedule(client, db_connection, "Dr. NoShow Pending")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/no-show", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (ctx["appointment"]["id"],))
        assert cur.fetchone()[0] == "PENDING", "a rejected no-show attempt must not change status"


def test_no_show_checked_in_appointment_is_409(client, db_connection):
    # A checked-in patient is physically present -- "no-show" from
    # CHECKED_IN is a contradiction, deliberately not a valid transition.
    ctx = _seed_and_schedule(client, db_connection, "Dr. NoShow CheckedIn")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    past_start = datetime.now(dt_timezone.utc) - timedelta(hours=1)
    _set_start_at(db_connection, ctx["appointment"]["id"], past_start, past_start + timedelta(minutes=30))
    client.post(f"/api/appointments/{ctx['appointment']['id']}/visit", headers=ctx["admin_headers"])

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/no-show", headers=ctx["admin_headers"]
    )
    assert response.status_code == 409

    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM appointments WHERE id = %s", (ctx["appointment"]["id"],))
        assert cur.fetchone()[0] == "CHECKED_IN", "a rejected no-show attempt must not change status"


def test_no_show_does_not_release_the_slot(client, db_connection):
    # Deliberately the OPPOSITE expectation from Rejected/Cancelled
    # (migrations/0011's RELEASED_STATUSES): an earlier draft of this
    # test assumed NO_SHOW should behave like those and release its
    # slot, and asserted success retrying ctx["start_at_local"] -- which
    # only passed because that's the ORIGINAL future slot, no longer
    # occupied by this appointment at all once AppointmentNotStarted
    # (below) forced moving it into the past first. That proved nothing.
    #
    # Retrying the slot the appointment actually still occupies (the
    # past start_at it was moved to) correctly gets rejected: NO_SHOW is
    # NOT in RELEASED_STATUSES, on purpose. Unlike Reject/Cancel, a
    # no-show can only ever be marked on an appointment that has already
    # started (mark_no_show_service's own AppointmentNotStarted guard,
    # same as check-in's) -- so by the time a slot could be marked
    # NO_SHOW, its time has already passed and no real future scheduling
    # could ever target it anyway. "Does it release the slot" is
    # consequently not a meaningful guarantee to make for NO_SHOW the
    # way it is for Reject/Cancel, which both apply to still-future
    # appointments.
    ctx = _seed_and_schedule(client, db_connection, "Dr. NoShow SlotReuse")
    client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=ctx["admin_headers"])

    past_start = datetime.now(dt_timezone.utc) - timedelta(hours=1)
    _set_start_at(db_connection, ctx["appointment"]["id"], past_start, past_start + timedelta(minutes=30))
    client.post(f"/api/appointments/{ctx['appointment']['id']}/no-show", headers=ctx["admin_headers"])

    other_patient = client.post(
        "/api/patients",
        json={"name": "NoShow Slot Reuser", "whatsapp_number": "+919912340002"},
        headers=ctx["admin_headers"],
    ).json()
    retry = client.post(
        "/api/appointments",
        json={
            "doctor_id": ctx["seeded"]["doctor_id"],
            "patient_id": other_patient["id"],
            "appointment_type_id": ctx["seeded"]["appointment_type_id"],
            "start_at": past_start.isoformat(),
        },
        headers=ctx["admin_headers"],
    )
    assert retry.status_code == 409


def test_no_show_requires_staff_auth(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. NoShow Auth")
    response = client.post(f"/api/appointments/{ctx['appointment']['id']}/no-show")
    assert response.status_code == 401


def test_lifecycle_endpoints_usable_by_plain_staff_not_just_admin(client, db_connection):
    # Same RBAC as cancel/reschedule on this router -- any authenticated
    # STAFF session, not require_role("ADMIN").
    ctx = _seed_and_schedule(client, db_connection, "Dr. Lifecycle Staff")
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    response = client.post(f"/api/appointments/{ctx['appointment']['id']}/confirm", headers=staff_headers)
    assert response.status_code == 200
