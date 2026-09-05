"""
Tests for the doctor profile enhancement (migrations/0014_doctor_profile.sql):
specialization/sub_specialization/qualifications/years_of_experience on
doctors, the multi-entry doctor_education table with its "at most one
featured entry" rule, and the compact summary fields (specialization,
years_of_experience, qualifications, education_location) surfaced by
every doctor-listing endpoint.

Photo upload has its own file: test_doctor_photo.py. The WhatsApp
"PROFILE <number>" side-channel has its own file: test_whatsapp_doctor_profile.py.
"""

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers


def _create_doctor(client, admin_headers, **overrides):
    body = {"name": "Dr. Profile Test", "specialization": "Cardiology"}
    body.update(overrides)
    return client.post("/api/doctors", json=body, headers=admin_headers)


def test_create_doctor_requires_specialization(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    response = client.post(
        "/api/doctors", json={"name": "Dr. No Specialization"}, headers=admin_headers
    )

    assert response.status_code == 422


def test_create_doctor_rejects_blank_specialization(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    response = _create_doctor(client, admin_headers, specialization="   ")

    assert response.status_code == 422


def test_create_doctor_with_full_profile_fields(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    response = _create_doctor(
        client,
        admin_headers,
        name="Dr. Full Profile",
        specialization="Cardiology",
        sub_specialization="Interventional Cardiology",
        qualifications="MBBS, MD (Cardiology)",
        years_of_experience=15,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["specialization"] == "Cardiology"
    assert body["sub_specialization"] == "Interventional Cardiology"
    assert body["qualifications"] == "MBBS, MD (Cardiology)"
    assert body["years_of_experience"] == 15
    assert body["photo_url"] is None


def test_create_doctor_optional_fields_default_to_none(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    body = _create_doctor(client, admin_headers, name="Dr. Minimal Profile").json()

    assert body["sub_specialization"] is None
    assert body["qualifications"] is None
    assert body["years_of_experience"] is None


def test_create_doctor_rejects_negative_years_of_experience(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    response = _create_doctor(client, admin_headers, years_of_experience=-1)

    assert response.status_code == 422


def test_create_doctor_requires_admin(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    response = _create_doctor(client, staff_headers)

    assert response.status_code == 403


def test_existing_doctor_created_before_this_feature_still_works(client, db_connection):
    """A doctor inserted directly (bypassing POST /doctors, simulating a
    pre-existing row) has NULL for every new column -- GET /doctors and
    GET /doctors/{id} must both still succeed and return None/omit the
    compact fields gracefully, per "existing doctors must continue
    working without requiring immediate profile backfill"."""
    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO doctors (name) VALUES (%s) RETURNING id", ("Dr. Legacy",))
        doctor_id = cur.fetchone()[0]
    db_connection.commit()

    listing = client.get("/api/doctors")
    assert listing.status_code == 200
    match = next(d for d in listing.json() if d["id"] == doctor_id)
    assert match["specialization"] is None
    assert match["years_of_experience"] is None
    assert match["education_location"] is None

    profile = client.get(f"/api/doctors/{doctor_id}")
    assert profile.status_code == 200
    assert profile.json()["specialization"] is None
    assert profile.json()["education"] == []


def test_update_doctor_via_put(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers, name="Dr. Before Update").json()

    response = client.put(
        f"/api/doctors/{doctor['id']}",
        json={
            "name": "Dr. After Update",
            "specialization": "Dermatology",
            "years_of_experience": 8,
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Dr. After Update"
    assert body["specialization"] == "Dermatology"
    assert body["years_of_experience"] == 8


def test_update_doctor_requires_admin(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    response = client.put(
        f"/api/doctors/{doctor['id']}",
        json={"name": "Dr. Hijacked", "specialization": "Oncology"},
        headers=staff_headers,
    )

    assert response.status_code == 403


def test_update_nonexistent_doctor_returns_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    response = client.put(
        "/api/doctors/999999999",
        json={"name": "Ghost", "specialization": "Cardiology"},
        headers=admin_headers,
    )

    assert response.status_code == 404


def test_get_doctor_profile_is_unauthenticated(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()

    response = client.get(f"/api/doctors/{doctor['id']}")

    assert response.status_code == 200


def test_get_doctor_profile_404_for_unknown_doctor(client, db_connection):
    response = client.get("/api/doctors/999999999")
    assert response.status_code == 404


def test_add_education_entry_requires_all_fields(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()

    response = client.post(
        f"/api/doctors/{doctor['id']}/education",
        json={"qualification": "MBBS", "institution": "AIIMS Delhi", "city": "Delhi"},
        headers=admin_headers,
    )

    assert response.status_code == 422


def test_add_education_entry_and_read_back_in_profile(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()

    created = client.post(
        f"/api/doctors/{doctor['id']}/education",
        json={
            "qualification": "MBBS",
            "institution": "AIIMS",
            "city": "New Delhi",
            "country": "India",
            "completion_year": 2005,
        },
        headers=admin_headers,
    )
    assert created.status_code == 200
    entry = created.json()
    assert entry["is_primary"] is False

    profile = client.get(f"/api/doctors/{doctor['id']}").json()
    assert len(profile["education"]) == 1
    assert profile["education"][0]["institution"] == "AIIMS"
    assert profile["education"][0]["completion_year"] == 2005


def test_education_entry_rejects_future_completion_year(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()

    response = client.post(
        f"/api/doctors/{doctor['id']}/education",
        json={
            "qualification": "MBBS",
            "institution": "AIIMS",
            "city": "New Delhi",
            "country": "India",
            "completion_year": 2999,
        },
        headers=admin_headers,
    )

    assert response.status_code == 422


def test_new_education_entry_never_auto_featured(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()

    client.post(
        f"/api/doctors/{doctor['id']}/education",
        json={
            "qualification": "MD",
            "institution": "PGIMER",
            "city": "Chandigarh",
            "country": "India",
            "completion_year": 2010,
        },
        headers=admin_headers,
    )

    listing = client.get("/api/doctors").json()
    match = next(d for d in listing if d["id"] == doctor["id"])
    assert match["education_location"] is None


def test_feature_education_entry_sets_compact_card_location(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()

    entry = client.post(
        f"/api/doctors/{doctor['id']}/education",
        json={
            "qualification": "MBBS",
            "institution": "AIIMS",
            "city": "New Delhi",
            "country": "India",
            "completion_year": 2005,
        },
        headers=admin_headers,
    ).json()

    feature_response = client.post(
        f"/api/doctors/{doctor['id']}/education/{entry['id']}/feature",
        headers=admin_headers,
    )
    assert feature_response.status_code == 200
    assert feature_response.json()["is_primary"] is True

    listing = client.get("/api/doctors").json()
    match = next(d for d in listing if d["id"] == doctor["id"])
    assert match["education_location"] == "AIIMS, New Delhi, India"


def test_featuring_a_new_entry_unfeatures_the_old_one(client, db_connection):
    """Only one education entry per doctor can be featured -- featuring
    a second entry must silently demote the first, never error and never
    leave two entries marked primary."""
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()

    entry_a = client.post(
        f"/api/doctors/{doctor['id']}/education",
        json={
            "qualification": "MBBS",
            "institution": "AIIMS",
            "city": "New Delhi",
            "country": "India",
            "completion_year": 2005,
        },
        headers=admin_headers,
    ).json()
    entry_b = client.post(
        f"/api/doctors/{doctor['id']}/education",
        json={
            "qualification": "MD",
            "institution": "PGIMER",
            "city": "Chandigarh",
            "country": "India",
            "completion_year": 2010,
        },
        headers=admin_headers,
    ).json()

    client.post(f"/api/doctors/{doctor['id']}/education/{entry_a['id']}/feature", headers=admin_headers)
    second = client.post(
        f"/api/doctors/{doctor['id']}/education/{entry_b['id']}/feature", headers=admin_headers
    )
    assert second.status_code == 200

    profile = client.get(f"/api/doctors/{doctor['id']}").json()
    primary_entries = [e for e in profile["education"] if e["is_primary"]]
    assert len(primary_entries) == 1
    assert primary_entries[0]["id"] == entry_b["id"]

    listing = client.get("/api/doctors").json()
    match = next(d for d in listing if d["id"] == doctor["id"])
    assert match["education_location"] == "PGIMER, Chandigarh, India"


def test_unfeature_education_entry_removes_compact_card_location(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()

    entry = client.post(
        f"/api/doctors/{doctor['id']}/education",
        json={
            "qualification": "MBBS",
            "institution": "AIIMS",
            "city": "New Delhi",
            "country": "India",
            "completion_year": 2005,
        },
        headers=admin_headers,
    ).json()
    client.post(f"/api/doctors/{doctor['id']}/education/{entry['id']}/feature", headers=admin_headers)

    unfeature = client.delete(
        f"/api/doctors/{doctor['id']}/education/{entry['id']}/feature", headers=admin_headers
    )
    assert unfeature.status_code == 200
    assert unfeature.json()["is_primary"] is False

    listing = client.get("/api/doctors").json()
    match = next(d for d in listing if d["id"] == doctor["id"])
    assert match["education_location"] is None


def test_delete_education_entry(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()

    entry = client.post(
        f"/api/doctors/{doctor['id']}/education",
        json={
            "qualification": "MBBS",
            "institution": "AIIMS",
            "city": "New Delhi",
            "country": "India",
            "completion_year": 2005,
        },
        headers=admin_headers,
    ).json()

    response = client.delete(
        f"/api/doctors/{doctor['id']}/education/{entry['id']}", headers=admin_headers
    )
    assert response.status_code == 200

    profile = client.get(f"/api/doctors/{doctor['id']}").json()
    assert profile["education"] == []


def test_delete_nonexistent_education_entry_returns_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()

    response = client.delete(
        f"/api/doctors/{doctor['id']}/education/999999999", headers=admin_headers
    )
    assert response.status_code == 404


def test_education_endpoints_require_admin(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    doctor = _create_doctor(client, admin_headers).json()
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    response = client.post(
        f"/api/doctors/{doctor['id']}/education",
        json={
            "qualification": "MBBS",
            "institution": "AIIMS",
            "city": "New Delhi",
            "country": "India",
            "completion_year": 2005,
        },
        headers=staff_headers,
    )

    assert response.status_code == 403


def test_department_doctors_listing_includes_compact_profile_fields(client, db_connection):
    from tests.helpers import seed_basic_doctor

    admin_headers = create_admin_and_get_headers(db_connection)
    seeded = seed_basic_doctor(client, db_connection, doctor_name="Dr. Compact Card")

    with db_connection.cursor() as cur:
        cur.execute(
            "UPDATE doctors SET specialization = %s, years_of_experience = %s, qualifications = %s WHERE id = %s",
            ("Neurology", 12, "MBBS, DM (Neurology)", seeded["doctor_id"]),
        )
    db_connection.commit()

    listing = client.get(f"/api/departments/{seeded['department_id']}/doctors")
    assert listing.status_code == 200
    match = next(d for d in listing.json() if d["id"] == seeded["doctor_id"])
    assert match["specialization"] == "Neurology"
    assert match["years_of_experience"] == 12
    assert match["qualifications"] == "MBBS, DM (Neurology)"
    assert match["education_location"] is None
