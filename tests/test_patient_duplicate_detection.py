"""
M8 (HospitalOS build plan): duplicate detection at registration. Warns,
never blocks -- every test here creates the new patient successfully
regardless of what candidates turn up; see
app/services/patient_duplicate_detection.py for why this only runs on
the admin create_patient path.
"""

from datetime import date

from tests.helpers import create_admin_and_get_headers


def _create_patient(client, admin_headers, name, whatsapp_number, **extra):
    return client.post(
        "/api/patients",
        json={"name": name, "whatsapp_number": whatsapp_number, **extra},
        headers=admin_headers,
    )


def test_similar_name_surfaces_as_possible_duplicate(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    _create_patient(client, admin_headers, "Rajesh Kumar Sharma", "+919770000001")

    response = _create_patient(client, admin_headers, "Rajesh Kumar Sharmaa", "+919770000002")
    assert response.status_code == 200
    body = response.json()

    assert len(body["possible_duplicates"]) == 1
    candidate = body["possible_duplicates"][0]
    assert "name" in candidate["matched_on"]
    assert candidate["name_similarity"] > 0


def test_unrelated_name_and_details_surface_no_duplicates(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    _create_patient(client, admin_headers, "Priya Patel", "+919770000010")

    response = _create_patient(client, admin_headers, "Completely Different Person", "+919770000011")
    assert response.status_code == 200
    assert response.json()["possible_duplicates"] == []


def test_same_date_of_birth_surfaces_as_possible_duplicate_even_with_different_name(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    dob = date(1990, 5, 20).isoformat()
    _create_patient(client, admin_headers, "Anil Verma", "+919770000020", date_of_birth=dob)

    response = _create_patient(
        client, admin_headers, "Totally Unrelated Name", "+919770000021", date_of_birth=dob
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["possible_duplicates"]) == 1
    assert body["possible_duplicates"][0]["matched_on"] == ["date_of_birth"]


def test_same_government_id_surfaces_as_possible_duplicate(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    _create_patient(client, admin_headers, "Sunita Rao", "+919770000030", government_id="AADHAAR-1111")

    response = _create_patient(
        client, admin_headers, "Different Name Entirely", "+919770000031", government_id="AADHAAR-1111"
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["possible_duplicates"]) == 1
    assert body["possible_duplicates"][0]["matched_on"] == ["government_id"]


def test_merged_patient_is_excluded_from_duplicate_candidates(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    survivor = _create_patient(client, admin_headers, "Merge Target", "+919770000040").json()
    retired = _create_patient(client, admin_headers, "Merge Target", "+919770000041").json()

    client.post(
        f"/api/patients/{survivor['id']}/merge",
        json={"retired_patient_id": retired["id"]},
        headers=admin_headers,
    )

    # A third "Merge Target" should match the survivor, never the
    # already-retired identity.
    response = _create_patient(client, admin_headers, "Merge Target", "+919770000042")
    assert response.status_code == 200
    candidate_ids = [c["candidate_patient_id"] for c in response.json()["possible_duplicates"]]
    assert survivor["id"] in candidate_ids
    assert retired["id"] not in candidate_ids


def test_reviewer_decision_is_recorded_and_cannot_be_redecided(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    _create_patient(client, admin_headers, "Decision Test Person", "+919770000050")
    response = _create_patient(client, admin_headers, "Decision Test Persan", "+919770000051")
    review_id = response.json()["possible_duplicates"][0]["review_id"]

    decide = client.patch(
        f"/api/patients/duplicate-reviews/{review_id}",
        json={"decision": "NOT_DUPLICATE"},
        headers=admin_headers,
    )
    assert decide.status_code == 200

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT reviewer_decision, reviewed_by_staff_id, decided_at FROM patient_duplicate_reviews WHERE id = %s",
            (review_id,),
        )
        decision, staff_id, decided_at = cur.fetchone()
        assert decision == "NOT_DUPLICATE"
        assert staff_id is not None
        assert decided_at is not None

    redecide = client.patch(
        f"/api/patients/duplicate-reviews/{review_id}",
        json={"decision": "CONFIRMED_DUPLICATE"},
        headers=admin_headers,
    )
    assert redecide.status_code == 404


def test_invalid_decision_value_is_rejected(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    _create_patient(client, admin_headers, "Bad Decision Person", "+919770000060")
    response = _create_patient(client, admin_headers, "Bad Decision Persen", "+919770000061")
    review_id = response.json()["possible_duplicates"][0]["review_id"]

    decide = client.patch(
        f"/api/patients/duplicate-reviews/{review_id}",
        json={"decision": "MAYBE"},
        headers=admin_headers,
    )
    assert decide.status_code == 422
