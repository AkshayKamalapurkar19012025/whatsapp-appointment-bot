"""
M8 (HospitalOS build plan): patient merge/unmerge, retired-UHID
resolution, and the plan's own explicit "Watch for" requirement --
merging must lock both patients in id order so two concurrent merges of
the same pair (in opposite roles) can't deadlock.
"""

import threading
from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, seed_basic_doctor


def _create_patient(client, admin_headers, name, whatsapp_number):
    return client.post(
        "/api/patients",
        json={"name": name, "whatsapp_number": whatsapp_number},
        headers=admin_headers,
    ).json()


def _book(client, admin_headers, seeded, patient_id, days_ahead, hour=9):
    target = date.today() + timedelta(days=days_ahead)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient_id,
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{target.isoformat()}T{hour:02d}:00:00+05:30",
        },
        headers=admin_headers,
    ).json()


def test_merge_moves_appointments_encounters_and_identifiers(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Merge One")

    survivor = _create_patient(client, admin_headers, "Survivor Patient", "+919760000001")
    retired = _create_patient(client, admin_headers, "Retired Patient", "+919760000002")

    booked = _book(client, admin_headers, seeded, retired["id"], days_ahead=3)
    assert booked["patient_id"] == retired["id"]

    response = client.post(
        f"/api/patients/{survivor['id']}/merge",
        json={"retired_patient_id": retired["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    merge_id = response.json()["merge_id"]

    with db_connection.cursor() as cur:
        cur.execute("SELECT patient_id FROM appointments WHERE id = %s", (booked["id"],))
        assert cur.fetchone()[0] == survivor["id"]

        cur.execute("SELECT patient_id FROM encounters WHERE id = %s", (booked["encounter_id"],))
        assert cur.fetchone()[0] == survivor["id"]

        cur.execute(
            "SELECT patient_id, is_primary FROM patient_identifiers WHERE value = %s",
            ("+919760000002",),
        )
        pid, is_primary = cur.fetchone()
        assert pid == survivor["id"]
        assert is_primary is False, "the retired patient's own primary identifier stays non-primary once absorbed"

        cur.execute("SELECT merged_into_id FROM patients WHERE id = %s", (retired["id"],))
        assert cur.fetchone()[0] == survivor["id"]

        cur.execute("SELECT affected FROM patient_merges WHERE id = %s", (merge_id,))
        affected = cur.fetchone()[0]
        assert affected["appointments"] == [booked["id"]]
        assert affected["encounters"] == [booked["encounter_id"]]


def test_merge_demotes_retired_primary_identifier_when_survivor_has_one_of_same_kind(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    survivor = _create_patient(client, admin_headers, "Kind Clash Survivor", "+919760000010")
    retired = _create_patient(client, admin_headers, "Kind Clash Retired", "+919760000011")

    response = client.post(
        f"/api/patients/{survivor['id']}/merge",
        json={"retired_patient_id": retired["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 200

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM patient_identifiers WHERE patient_id = %s AND kind = 'PHONE' AND is_primary = TRUE",
            (survivor["id"],),
        )
        assert cur.fetchone()[0] == 1, "the survivor must still have exactly one primary PHONE identifier"


def test_cannot_merge_patient_into_itself(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    patient = _create_patient(client, admin_headers, "Solo Patient", "+919760000020")

    response = client.post(
        f"/api/patients/{patient['id']}/merge",
        json={"retired_patient_id": patient["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 400


def test_cannot_merge_already_merged_patient(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    a = _create_patient(client, admin_headers, "Chain A", "+919760000030")
    b = _create_patient(client, admin_headers, "Chain B", "+919760000031")
    c = _create_patient(client, admin_headers, "Chain C", "+919760000032")

    first = client.post(
        f"/api/patients/{a['id']}/merge", json={"retired_patient_id": b["id"]}, headers=admin_headers
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/patients/{c['id']}/merge", json={"retired_patient_id": b["id"]}, headers=admin_headers
    )
    assert second.status_code == 409


def test_unmerge_restores_everything(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Unmerge")

    survivor = _create_patient(client, admin_headers, "Unmerge Survivor", "+919760000040")
    retired = _create_patient(client, admin_headers, "Unmerge Retired", "+919760000041")
    booked = _book(client, admin_headers, seeded, retired["id"], days_ahead=3)

    merge_id = client.post(
        f"/api/patients/{survivor['id']}/merge",
        json={"retired_patient_id": retired["id"]},
        headers=admin_headers,
    ).json()["merge_id"]

    response = client.post(f"/api/patients/merges/{merge_id}/unmerge", headers=admin_headers)
    assert response.status_code == 200

    with db_connection.cursor() as cur:
        cur.execute("SELECT patient_id FROM appointments WHERE id = %s", (booked["id"],))
        assert cur.fetchone()[0] == retired["id"]

        cur.execute("SELECT merged_into_id FROM patients WHERE id = %s", (retired["id"],))
        assert cur.fetchone()[0] is None

        cur.execute(
            "SELECT patient_id, is_primary FROM patient_identifiers WHERE value = %s",
            ("+919760000041",),
        )
        pid, is_primary = cur.fetchone()
        assert pid == retired["id"]
        assert is_primary is True


def test_unmerge_refused_after_new_appointment_for_survivor(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Unmerge Blocked")

    survivor = _create_patient(client, admin_headers, "Blocked Survivor", "+919760000050")
    retired = _create_patient(client, admin_headers, "Blocked Retired", "+919760000051")

    merge_id = client.post(
        f"/api/patients/{survivor['id']}/merge",
        json={"retired_patient_id": retired["id"]},
        headers=admin_headers,
    ).json()["merge_id"]

    # A new appointment for the survivor, created after the merge --
    # unmerge can no longer safely tell whether this one is really
    # theirs or should have belonged to the identity being restored.
    _book(client, admin_headers, seeded, survivor["id"], days_ahead=4)

    response = client.post(f"/api/patients/merges/{merge_id}/unmerge", headers=admin_headers)
    assert response.status_code == 409


def test_retired_uhid_resolves_to_survivor(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    survivor = _create_patient(client, admin_headers, "UHID Survivor", "+919760000060")
    retired = _create_patient(client, admin_headers, "UHID Retired", "+919760000061")
    retired_uhid = retired["uhid"]

    client.post(
        f"/api/patients/{survivor['id']}/merge",
        json={"retired_patient_id": retired["id"]},
        headers=admin_headers,
    )

    response = client.get(f"/api/patients/by-uhid/{retired_uhid}", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == survivor["id"]
    assert body["retired"] is True
    assert body["retired_uhid"] == retired_uhid

    # The survivor's own UHID still resolves directly, not retired.
    direct = client.get(f"/api/patients/by-uhid/{survivor['uhid']}", headers=admin_headers)
    assert direct.status_code == 200
    assert direct.json() == {
        "id": survivor["id"],
        "name": survivor["name"],
        "uhid": survivor["uhid"],
        "retired": False,
    }


def test_concurrent_merges_of_the_same_pair_do_not_deadlock(client, db_connection):
    """The plan's own "Watch for": merge must lock both patients in id
    order, not surviving-then-retired, so two concurrent merges of the
    same pair (in opposite roles) serialize instead of deadlocking.
    Without that discipline, thread 1 locking A-then-B while thread 2
    locks B-then-A is a textbook deadlock; Postgres would eventually
    kill one side with a deadlock_detected error rather than truly
    hang, but that's still a failure this test rules out -- both calls
    must complete, and (since both patients can't independently survive
    a merge with the other already retired) exactly one must succeed."""
    admin_headers = create_admin_and_get_headers(db_connection)
    a = _create_patient(client, admin_headers, "Deadlock A", "+919760000070")
    b = _create_patient(client, admin_headers, "Deadlock B", "+919760000071")

    results = {}
    barrier = threading.Barrier(2)

    def merge(surviving_id, retired_id, key):
        barrier.wait()
        response = client.post(
            f"/api/patients/{surviving_id}/merge",
            json={"retired_patient_id": retired_id},
            headers=admin_headers,
        )
        results[key] = response.status_code

    t1 = threading.Thread(target=merge, args=(a["id"], b["id"], "a_survives"))
    t2 = threading.Thread(target=merge, args=(b["id"], a["id"], "b_survives"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive(), "a deadlock would leave one thread hung"
    assert sorted(results.values()) == [200, 409], f"expected exactly one merge to succeed, got {results}"

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM patients WHERE id IN (%s, %s) AND merged_into_id IS NOT NULL",
            (a["id"], b["id"]),
        )
        assert cur.fetchone()[0] == 1, "exactly one of the two patients must end up retired"
