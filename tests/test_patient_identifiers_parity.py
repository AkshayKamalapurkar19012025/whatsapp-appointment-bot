"""
M4-M5 (HospitalOS build plan) parity test: looking a patient up by their
PHONE identifier must return the exact same row as looking them up by
patients.whatsapp_number directly. Runs for the whole of M4-M5 -- see
app/services/patient_identifiers.py's module docstring for why this
table exists and which readers still need to migrate onto it.
"""

from tests.helpers import register_patient
from app.services.patient_identifiers import resolve_patient_by_identifier


def test_identifier_lookup_matches_column_lookup_for_every_patient(client, db_connection):
    register_patient(client, "+919700000001", "Parity Patient One")
    register_patient(client, "+919700000002", "Parity Patient Two")

    with db_connection.cursor() as cur:
        cur.execute("SELECT id, hospital_id, whatsapp_number FROM patients")
        patients = cur.fetchall()

    assert len(patients) >= 2, "expected at least the two patients just registered"

    with db_connection.cursor() as cur:
        for patient_id, hospital_id, whatsapp_number in patients:
            resolved = resolve_patient_by_identifier(cur, hospital_id, "PHONE", whatsapp_number)
            assert resolved is not None, f"no identifier match for patient {patient_id}"
            assert resolved["id"] == patient_id
            assert resolved["whatsapp_number"] == whatsapp_number


def test_updating_whatsapp_number_keeps_identifier_in_sync(client, db_connection):
    from tests.helpers import create_admin_and_get_headers

    admin_headers = create_admin_and_get_headers(db_connection)

    created = client.post(
        "/api/patients",
        json={"name": "Identifier Update Patient", "whatsapp_number": "+919700000003"},
        headers=admin_headers,
    ).json()

    updated = client.patch(
        f"/api/patients/{created['id']}",
        json={"name": "Identifier Update Patient", "whatsapp_number": "+919700000004"},
        headers=admin_headers,
    )
    assert updated.status_code == 200

    with db_connection.cursor() as cur:
        cur.execute("SELECT hospital_id FROM patients WHERE id = %s", (created["id"],))
        hospital_id = cur.fetchone()[0]

        # The old number must no longer resolve to this patient...
        stale = resolve_patient_by_identifier(cur, hospital_id, "PHONE", "+919700000003")
        assert stale is None or stale["id"] != created["id"]

        # ...and the new one must.
        resolved = resolve_patient_by_identifier(cur, hospital_id, "PHONE", "+919700000004")
        assert resolved is not None
        assert resolved["id"] == created["id"]
