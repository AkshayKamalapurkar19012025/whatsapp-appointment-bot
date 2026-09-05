"""
Concurrent double-booking protection tests.

Three "Test A/B/C" scenarios plus the exclusion-constraint backstop
(Test D lives in tests/test_exclusion_constraint.py, since it works at
the raw-SQL level rather than through the app):

- test_simultaneous_whatsapp_bookings_same_slot (Test A): two patients
  race, via the WhatsApp flow, to book the identical slot.
- test_simultaneous_rest_bookings_same_slot (Test B): two direct
  POST /api/appointments race for the identical slot.
- test_cross_path_concurrent_booking (Test C): a WhatsApp booking
  confirmation races a direct POST /api/appointments for the identical
  doctor/slot.

HISTORY -- Test C used to be marked xfail(strict=True). It found a real,
reproducible gap: app/api/booking.py's pg_advisory_xact_lock and (the
then-current) app/api/appointments.py's SELECT...FOR UPDATE on doctors
are different Postgres locking primitives that don't block each other,
so both paths could pass their overlap check and both INSERT. A live
run (20 isolated attempts) produced a genuine double booking in 19 of
20. See docs/DATABASE_P1_NOTES.md item 4 for the full writeup.

FIX APPLIED: app/api/appointments.py now takes the same
pg_advisory_xact_lock(doctor_id) app/api/booking.py already used, at
the same relative position (after the doctor-block check, before the
final overlap re-check, held through the INSERT). Both paths also sit
behind the database-level EXCLUDE constraint from
migrations/0003_prevent_overlapping_bookings.sql as a second line of
defense. All three tests below now assert the double-booking-free
outcome directly (no xfail) -- see test_exclusion_constraint.py for the
test that deliberately bypasses the application lock to prove the
constraint backstop independently still works.
"""

import threading

from tests.conftest import APP_TABLES
from tests.helpers import create_admin_and_get_headers, seed_basic_doctor, register_patient


def send_booking(client, whatsapp_number, message):
    return client.post(
        "/api/booking", json={"whatsapp_number": whatsapp_number, "message": message}
    ).json()


def _truncate_all(db_connection):
    """Reset to a clean slate mid-test. Needed by
    test_cross_path_concurrent_booking, which loops several race
    attempts inside one test: without this, "select option 1" in each
    later attempt resolves to the FIRST attempt's department/doctor
    (alphabetically first, still present), not the current attempt's --
    which silently makes the two racing requests target different
    doctors instead of actually racing. (This is exactly the bug found,
    and fixed, while building this test -- see the P1 report.)"""
    with db_connection.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE " + ", ".join(APP_TABLES) + " RESTART IDENTITY CASCADE"
        )
    db_connection.commit()


def test_simultaneous_whatsapp_bookings_same_slot(client, db_connection):
    """Two different patients race, via the WhatsApp flow, to book the
    exact same doctor/slot. Exactly one must succeed."""
    seed_basic_doctor(client, db_connection)

    number_a = "+919600000001"
    number_b = "+919600000002"
    register_patient(client, number_a, "Racer A")
    register_patient(client, number_b, "Racer B")

    for number in (number_a, number_b):
        send_booking(client, number, "1")  # book
        send_booking(client, number, "1")  # department
        send_booking(client, number, "1")  # doctor
        send_booking(client, number, "1")  # appointment type
        send_booking(client, number, "4")  # date option 4
        send_booking(client, number, "1")  # slot 1
        # both now sitting at CONFIRM_BOOKING for the identical slot

    results = {}
    barrier = threading.Barrier(2)

    def confirm(number, key):
        barrier.wait()
        results[key] = send_booking(client, number, "1")

    t1 = threading.Thread(target=confirm, args=(number_a, "a"))
    t2 = threading.Thread(target=confirm, args=(number_b, "b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    booked = [k for k, v in results.items() if v["next_step"] == "BOOKED"]
    rejected = [k for k, v in results.items() if v["next_step"] != "BOOKED"]

    assert len(booked) == 1, f"expected exactly one booking to succeed, got {results}"
    assert len(rejected) == 1
    rejected_response = results[rejected[0]]
    assert (
        rejected_response.get("error")
        == "That slot was just booked by someone else. Please choose another date or slot."
    ), f"expected the normal slot-unavailable response, got {rejected_response}"

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM appointments WHERE status = 'PENDING'"
        )
        booked_count = cur.fetchone()[0]
    assert booked_count == 1, f"expected exactly one Pending appointment, found {booked_count}"


def test_simultaneous_rest_bookings_same_slot(client, db_connection):
    """Test B: two direct POST /api/appointments race for the identical
    doctor/slot. Exactly one must succeed with 200, the other must fail
    with 409 and the standard overlap-conflict message."""
    seeded = seed_basic_doctor(client, db_connection)
    staff_headers = create_admin_and_get_headers(db_connection)

    patient_a = client.post(
        "/api/patients",
        json={"name": "REST Racer A", "whatsapp_number": "+919650000001"},
        headers=staff_headers,
    ).json()
    patient_b = client.post(
        "/api/patients",
        json={"name": "REST Racer B", "whatsapp_number": "+919650000002"},
        headers=staff_headers,
    ).json()

    # A fixed, known-available slot: the doctor's schedule (seed_basic_doctor)
    # runs 09:00-17:00 every weekday; pick the next Monday-Friday date a
    # comfortable number of days out so it's unambiguously in the schedule
    # and unaffected by whatever "today" is when the suite runs.
    from datetime import date, timedelta

    target_date = date.today() + timedelta(days=7)
    while target_date.weekday() >= 5:  # skip weekends
        target_date += timedelta(days=1)
    start_at = f"{target_date.isoformat()}T09:00:00+05:30"

    results = {}
    barrier = threading.Barrier(2)

    def do_create(patient_id, key):
        barrier.wait()
        response = client.post(
            "/api/appointments",
            json={
                "doctor_id": seeded["doctor_id"],
                "patient_id": patient_id,
                "appointment_type_id": seeded["appointment_type_id"],
                "start_at": start_at,
            },
            headers=staff_headers,
        )
        results[key] = {"status_code": response.status_code, "body": response.json()}

    t1 = threading.Thread(target=do_create, args=(patient_a["id"], "a"))
    t2 = threading.Thread(target=do_create, args=(patient_b["id"], "b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    succeeded = [k for k, v in results.items() if v["status_code"] == 200]
    failed = [k for k, v in results.items() if v["status_code"] != 200]

    assert len(succeeded) == 1, f"expected exactly one 200, got {results}"
    assert len(failed) == 1
    assert results[failed[0]]["status_code"] == 409
    assert (
        results[failed[0]]["body"]["detail"]
        == "Appointment overlaps with existing appointment"
    )

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM appointments WHERE doctor_id = %s AND status = 'PENDING'",
            (seeded["doctor_id"],),
        )
        booked_count = cur.fetchone()[0]
    assert booked_count == 1, f"expected exactly one Pending appointment, found {booked_count}"


def test_cross_path_concurrent_booking(client, db_connection):
    """
    WhatsApp booking confirmation vs. direct POST /api/appointments,
    racing for the identical doctor/slot. See module docstring -- this
    is checking whether double-booking protection holds when the two
    different locking strategies race each other, not assuming it does.

    Runs the race multiple times (fresh, fully truncated state each
    attempt -- see _truncate_all -- since a real TOCTOU race is
    timing-sensitive and might not reproduce on every single attempt).
    """
    attempts = 8
    any_double_booked = False
    outcomes = []

    for attempt in range(attempts):
        # Full truncate (not just a fresh doctor) so "select option 1"
        # unambiguously means "the one just created" -- see _truncate_all.
        _truncate_all(db_connection)

        seeded = seed_basic_doctor(client, db_connection, doctor_name=f"Dr. Race {attempt}")
        staff_headers = create_admin_and_get_headers(db_connection)

        whatsapp_number = f"+91970000{attempt:04d}"
        register_patient(client, whatsapp_number, f"WhatsApp Racer {attempt}")

        rest_patient = client.post(
            "/api/patients",
            json={"name": f"REST Racer {attempt}", "whatsapp_number": f"+91971000{attempt:04d}"},
            headers=staff_headers,
        ).json()

        # Drive the WhatsApp path to CONFIRM_BOOKING, and read back the
        # exact slot it will book, so the REST call can target the
        # identical doctor_id/start_at.
        send_booking(client, whatsapp_number, "1")  # book
        send_booking(client, whatsapp_number, "1")  # department
        send_booking(client, whatsapp_number, "1")  # doctor
        send_booking(client, whatsapp_number, "1")  # appointment type
        send_booking(client, whatsapp_number, "1")  # date option 1
        confirm_step = send_booking(client, whatsapp_number, "1")  # slot 1
        assert confirm_step["next_step"] == "CONFIRM_BOOKING"
        target_start_at = confirm_step["start_at"]

        results = {}
        barrier = threading.Barrier(2)

        def do_whatsapp_confirm():
            barrier.wait()
            results["whatsapp"] = client.post(
                "/api/booking",
                json={"whatsapp_number": whatsapp_number, "message": "1"},
            ).json()

        def do_rest_create():
            barrier.wait()
            response = client.post(
                "/api/appointments",
                json={
                    "doctor_id": seeded["doctor_id"],
                    "patient_id": rest_patient["id"],
                    "appointment_type_id": seeded["appointment_type_id"],
                    "start_at": target_start_at,
                },
                headers=staff_headers,
            )
            results["rest"] = {
                "status_code": response.status_code,
                "body": response.json(),
            }

        t1 = threading.Thread(target=do_whatsapp_confirm)
        t2 = threading.Thread(target=do_rest_create)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        whatsapp_booked = results["whatsapp"]["next_step"] == "BOOKED"
        rest_booked = results["rest"]["status_code"] == 200
        succeeded_count = sum([whatsapp_booked, rest_booked])

        with db_connection.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM appointments
                WHERE doctor_id = %s AND status = 'PENDING'
                """,
                (seeded["doctor_id"],),
            )
            booked_count = cur.fetchone()[0]

        outcome = {
            "attempt": attempt,
            "whatsapp_booked": whatsapp_booked,
            "rest_status": results["rest"]["status_code"],
            "booked_rows_for_doctor": booked_count,
        }
        outcomes.append(outcome)

        if booked_count > 1:
            any_double_booked = True

        assert succeeded_count == 1, (
            f"attempt {attempt}: expected exactly one path to succeed, "
            f"got {outcome}"
        )
        assert booked_count == 1, (
            f"attempt {attempt}: expected exactly one Pending appointment "
            f"for this doctor, found {booked_count}"
        )

    print("\nCross-path concurrency results (all passed):")
    for outcome in outcomes:
        print(f"  {outcome}")

    assert not any_double_booked, (
        "Cross-path double booking IS possible: a WhatsApp booking "
        "confirmation and a direct POST /api/appointments for the same "
        "doctor/slot both succeeded in at least one attempt. Details:\n"
        + "\n".join(str(o) for o in outcomes)
    )


def test_concurrent_reschedule_vs_fresh_booking_same_target_slot(client, db_connection):
    """
    Patient A reschedules an existing appointment INTO slot Y at the
    same instant Patient B tries to freshly book slot Y directly via
    the REST path. Exactly one must win. If A's reschedule loses, A's
    ORIGINAL appointment must remain Pending and untouched -- a failed
    reschedule must never lose the original booking (see
    app/api/booking.py's RESCHEDULE_FINAL_CONFIRM comment on why the
    cancel-old + insert-new both happen in one transaction).
    """
    seeded = seed_basic_doctor(client, db_connection)

    # Patient A: book date option 4, slot 1 (the "original" appointment).
    # Deliberately NOT date option 1 ("today") -- get_upcoming_booked_
    # appointments() filters on start_at > NOW(), and a "today" slot can
    # already be in the past by the time this test reaches the reschedule
    # step below, depending on what time of day the suite happens to run
    # (this exact pitfall was hit and documented earlier in this project's
    # testing -- see tests/test_booking_flow.py's reschedule/cancel tests,
    # which use date_option="5" for the same reason).
    number_a = "+919660000001"
    register_patient(client, number_a, "Reschedule Racer A")
    send_booking(client, number_a, "1")  # book
    send_booking(client, number_a, "1")  # department
    send_booking(client, number_a, "1")  # doctor
    send_booking(client, number_a, "1")  # appointment type
    send_booking(client, number_a, "4")  # date option 4
    send_booking(client, number_a, "1")  # slot 1
    original = send_booking(client, number_a, "1")  # confirm
    assert original["next_step"] == "BOOKED"
    original_id = original["appointment"]["id"]

    # Drive A into RESCHEDULE_FINAL_CONFIRM targeting date option 2, slot 1
    # (the target slot Y). Reschedule always shows RESCHEDULE_SELECT (a
    # numbered list) even with one appointment -- unlike cancel, it has
    # no single-appointment shortcut (verified in test_booking_flow.py).
    send_booking(client, number_a, "main menu")
    send_booking(client, number_a, "2")  # reschedule -> RESCHEDULE_SELECT
    send_booking(client, number_a, "1")  # pick appointment 1 -> RESCHEDULE_CONFIRM
    send_booking(client, number_a, "1")  # confirm reschedule intent -> RESCHEDULE_DATE
    send_booking(client, number_a, "2")  # date option 2 -> RESCHEDULE_SLOT
    target_step = send_booking(client, number_a, "1")  # slot 1 -> RESCHEDULE_FINAL_CONFIRM
    assert target_step["next_step"] == "RESCHEDULE_FINAL_CONFIRM"
    target_start_at = target_step["start_at"]

    # Patient B: a fresh REST booking for the exact same doctor/slot Y.
    staff_headers = create_admin_and_get_headers(db_connection)
    patient_b = client.post(
        "/api/patients",
        json={"name": "Fresh Booking Racer B", "whatsapp_number": "+919660000002"},
        headers=staff_headers,
    ).json()

    results = {}
    barrier = threading.Barrier(2)

    def do_reschedule():
        barrier.wait()
        results["reschedule"] = client.post(
            "/api/booking", json={"whatsapp_number": number_a, "message": "1"}
        ).json()

    def do_fresh_booking():
        barrier.wait()
        response = client.post(
            "/api/appointments",
            json={
                "doctor_id": seeded["doctor_id"],
                "patient_id": patient_b["id"],
                "appointment_type_id": seeded["appointment_type_id"],
                "start_at": target_start_at,
            },
            headers=staff_headers,
        )
        results["fresh"] = {"status_code": response.status_code, "body": response.json()}

    t1 = threading.Thread(target=do_reschedule)
    t2 = threading.Thread(target=do_fresh_booking)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    reschedule_won = results["reschedule"]["next_step"] == "RESCHEDULED"
    fresh_won = results["fresh"]["status_code"] == 200

    assert reschedule_won != fresh_won, (
        f"expected exactly one side to win, got reschedule={results['reschedule']}, "
        f"fresh={results['fresh']}"
    )

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT id, status FROM appointments WHERE id = %s",
            (original_id,),
        )
        original_row = cur.fetchone()

        cur.execute(
            "SELECT count(*) FROM appointments WHERE doctor_id = %s AND status = 'PENDING'",
            (seeded["doctor_id"],),
        )
        total_booked = cur.fetchone()[0]

    if reschedule_won:
        # Original was correctly cancelled as part of the successful reschedule.
        assert original_row[1] == "CANCELLED"
        assert total_booked == 1  # only the new (rescheduled) appointment
    else:
        # Reschedule lost: the original booking must remain intact, not lost.
        assert original_row[1] == "PENDING", (
            "reschedule lost the race but the ORIGINAL appointment was not "
            "preserved -- this is the exact failure mode the spec forbids"
        )
        # Two distinct, non-overlapping appointments now exist for this
        # doctor: A's untouched original (date option 1) and B's fresh
        # booking (date option 2, slot Y) -- not a double booking, since
        # they're on different dates.
        assert total_booked == 2

