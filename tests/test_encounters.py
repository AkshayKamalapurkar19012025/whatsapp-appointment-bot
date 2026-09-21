"""
Tests for the encounter foundation added in migrations/0028_encounters.sql
(OPD/HIMS master spec Phase 3, per docs/OPD_HIMS_P0_AUDIT.md section 3 and
the Option A decision: encounters live in this database, not a separate
service).

Every OPD appointment gets exactly one `encounters` row, opened alongside
it (app/services/appointment_services.py's create_appointment_service),
carried forward -- not re-created -- across a reschedule, and closed the
moment the appointment reaches a terminal status (CANCELLED, REJECTED,
COMPLETED, NO_SHOW). These tests exercise that lifecycle end-to-end
through the same REST endpoints test_appointment_lifecycle.py uses,
asserting on the encounters table directly (there is no encounters API
yet -- that's later work, not this phase's job).
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9198{abs(hash(doctor_name)) % 10**8:08d}"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
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


def _set_start_at(db_connection, appointment_id: int, start_at: datetime, end_at: datetime) -> None:
    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
            (start_at, end_at, appointment_id),
        )
    db_connection.commit()


def _encounter_for_appointment(db_connection, appointment_id: int):
    """Looks up via appointments.encounter_id (the forward FK
    migrations/0028_encounters.sql actually creates), not a reverse
    encounters.appointment_id column -- that column doesn't exist on
    this table; every OPD encounter is found through the appointment
    that opened it instead. Returns None if the appointment has no
    encounter_id set at all (shouldn't happen for any appointment
    created by create_appointment_service, but kept as a real "not
    found" rather than assumed)."""
    with db_connection.cursor() as cur:
        cur.execute("SELECT encounter_id FROM appointments WHERE id = %s", (appointment_id,))
        row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        encounter_id = row[0]

        cur.execute(
            "SELECT id, patient_id, encounter_type, status, closed_at FROM encounters WHERE id = %s",
            (encounter_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "patient_id": row[1],
        "encounter_type": row[2],
        "status": row[3],
        "closed_at": row[4],
    }


def _encounter_count_for_patient(db_connection, patient_id: int) -> int:
    with db_connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM encounters WHERE patient_id = %s", (patient_id,))
        return cur.fetchone()[0]


# ---------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------


def test_creating_an_appointment_opens_exactly_one_open_opd_encounter(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Encounter Create")

    assert ctx["appointment"]["encounter_id"] is not None

    encounter = _encounter_for_appointment(db_connection, ctx["appointment"]["id"])
    assert encounter is not None
    assert encounter["id"] == ctx["appointment"]["encounter_id"]
    assert encounter["patient_id"] == ctx["patient"]["id"]
    assert encounter["encounter_type"] == "OPD"
    assert encounter["status"] == "OPEN"
    assert encounter["closed_at"] is None
    assert _encounter_count_for_patient(db_connection, ctx["patient"]["id"]) == 1


def test_two_appointments_for_the_same_patient_get_two_encounters(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Encounter Two Visits")
    admin_headers = ctx["admin_headers"]
    seeded = ctx["seeded"]

    second_date = _next_weekday(date.today() + timedelta(days=17))
    second = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": ctx["patient"]["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{second_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()

    assert second["encounter_id"] != ctx["appointment"]["encounter_id"]
    assert _encounter_count_for_patient(db_connection, ctx["patient"]["id"]) == 2


# ---------------------------------------------------------------------
# Closing on a terminal status
# ---------------------------------------------------------------------


def test_cancel_closes_the_encounter(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Encounter Cancel")

    response = client.delete(
        f"/api/appointments/{ctx['appointment']['id']}", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200

    encounter = _encounter_for_appointment(db_connection, ctx["appointment"]["id"])
    assert encounter["status"] == "CLOSED"
    assert encounter["closed_at"] is not None


def test_reject_closes_the_encounter(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Encounter Reject")

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/reject", headers=ctx["admin_headers"]
    )
    assert response.status_code == 200

    encounter = _encounter_for_appointment(db_connection, ctx["appointment"]["id"])
    assert encounter["status"] == "CLOSED"
    assert encounter["closed_at"] is not None


def test_complete_closes_the_encounter(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Encounter Complete")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    client.post(f"/api/appointments/{appointment_id}/confirm", headers=admin_headers)

    encounter = _encounter_for_appointment(db_connection, appointment_id)
    assert encounter["status"] == "OPEN", "still open through confirm -- the episode hasn't ended"

    client.post(f"/api/appointments/{appointment_id}/visit", headers=admin_headers)
    encounter = _encounter_for_appointment(db_connection, appointment_id)
    assert encounter["status"] == "OPEN", "still open through check-in"

    response = client.post(f"/api/appointments/{appointment_id}/complete", headers=admin_headers)
    assert response.status_code == 200

    encounter = _encounter_for_appointment(db_connection, appointment_id)
    assert encounter["status"] == "CLOSED"
    assert encounter["closed_at"] is not None


def test_no_show_closes_the_encounter(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Encounter NoShow")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    client.post(f"/api/appointments/{appointment_id}/confirm", headers=admin_headers)

    past_start = datetime.now(dt_timezone.utc) - timedelta(hours=1)
    _set_start_at(db_connection, appointment_id, past_start, past_start + timedelta(minutes=30))

    response = client.post(f"/api/appointments/{appointment_id}/no-show", headers=admin_headers)
    assert response.status_code == 200

    encounter = _encounter_for_appointment(db_connection, appointment_id)
    assert encounter["status"] == "CLOSED"
    assert encounter["closed_at"] is not None


# ---------------------------------------------------------------------
# Reschedule -- same care episode, same encounter, moved forward
# ---------------------------------------------------------------------


def test_reschedule_carries_the_same_encounter_forward(client, db_connection):
    ctx = _seed_and_schedule(client, db_connection, "Dr. Encounter Reschedule")
    original_appointment_id = ctx["appointment"]["id"]
    original_encounter_id = ctx["appointment"]["encounter_id"]

    new_date = _next_weekday(date.today() + timedelta(days=24))
    response = client.post(
        f"/api/appointments/{original_appointment_id}/reschedule",
        json={"new_start_at": f"{new_date.isoformat()}T09:00:00+05:30"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    new_appointment_id = response.json()["id"]
    assert new_appointment_id != original_appointment_id

    # The old (now-cancelled) appointment row still carries its original
    # encounter_id -- that's accurate history (it's the row that really
    # did open this encounter), not something a reschedule should erase.
    old_encounter = _encounter_for_appointment(db_connection, original_appointment_id)
    assert old_encounter is not None
    assert old_encounter["id"] == original_encounter_id

    # The same encounter carries forward onto the new appointment row too
    # -- same encounter id, still open -- a reschedule is the same care
    # episode, not a new one.
    encounter = _encounter_for_appointment(db_connection, new_appointment_id)
    assert encounter is not None
    assert encounter["id"] == original_encounter_id
    assert encounter["status"] == "OPEN"
    assert encounter["closed_at"] is None

    # And no orphan/duplicate encounter was created for this patient --
    # both appointment rows point at the same one.
    assert _encounter_count_for_patient(db_connection, ctx["patient"]["id"]) == 1
