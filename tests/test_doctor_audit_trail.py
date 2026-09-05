"""
Tests for doctors.created_at/created_by (migration 0009): a doctor created
through POST /doctors records which staff account created it, and both
POST /doctors and GET /doctors return that alongside created_at.
"""

from tests.helpers import create_staff_and_get_headers, create_staff_for_test
from app.services.staff_auth import login


def _admin_headers_and_username(db_connection):
    username = "audit-admin"
    password = "audit-admin-password"  # noqa: S105 -- test-only
    create_staff_for_test(db_connection, username=username, password=password, role="ADMIN")

    with db_connection.cursor() as cur:
        result = login(cur, username, password)
    db_connection.commit()

    return {"Authorization": f"Bearer {result['session_token']}"}, username


def test_create_doctor_records_created_by(client, db_connection):
    admin_headers, username = _admin_headers_and_username(db_connection)

    response = client.post(
        "/api/doctors",
        json={"name": "Dr. Audit Trail", "specialization": "General Medicine"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()

    assert body["created_by"] == username
    assert body["created_at"]  # non-empty ISO timestamp


def test_list_doctors_includes_created_by(client, db_connection):
    admin_headers, username = _admin_headers_and_username(db_connection)

    created = client.post(
        "/api/doctors",
        json={"name": "Dr. Audit List", "specialization": "General Medicine"},
        headers=admin_headers,
    ).json()

    listing = client.get("/api/doctors")
    assert listing.status_code == 200
    match = next(d for d in listing.json() if d["id"] == created["id"])

    assert match["created_by"] == username
    assert match["created_at"] == created["created_at"]


def test_doctor_created_by_is_null_when_staff_unknown(client, db_connection):
    """A doctor row with no created_by (e.g. seeded before this column
    existed) must still list cleanly -- the LEFT JOIN to staff must not
    turn a doctor with no attributed creator into a broken/missing row."""
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO doctors (name) VALUES (%s) RETURNING id",
            ("Dr. No Creator On Record",),
        )
        doctor_id = cur.fetchone()[0]
    db_connection.commit()

    listing = client.get("/api/doctors")
    assert listing.status_code == 200
    match = next(d for d in listing.json() if d["id"] == doctor_id)

    assert match["created_by"] is None
    assert match["created_at"]
