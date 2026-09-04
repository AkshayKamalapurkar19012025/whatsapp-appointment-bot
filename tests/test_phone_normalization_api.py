"""
Integration coverage for phone number normalization (UI/UX improvement
task): the same person typing their number in different formats across
separate requests must resolve to the same patient row, not create
duplicates. See app/utils/phone.py for the normalization rules and why
the WhatsApp inbound path is deliberately untouched.
"""

from tests.helpers import create_admin_and_get_headers


def test_otp_login_with_different_formats_of_the_same_number_reuses_one_patient(
    client, db_connection
):
    # First format: bare 10-digit, no country code.
    client.post("/api/auth/patient/otp/request", json={"whatsapp_number": "9123456780"})
    otp1 = client.get(
        "/api/auth/patient/otp/_dev_lookup", params={"whatsapp_number": "9123456780"}
    ).json()["otp_code"]
    verify1 = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": "9123456780", "otp": otp1, "name": "Format Test"},
    )
    assert verify1.status_code == 200
    patient_id = verify1.json()["patient"]["id"]
    assert verify1.json()["patient"]["whatsapp_number"] == "+919123456780"

    # Second format: same number, spaced and with a leading +91 -- must
    # resolve to the SAME patient, not register_required a brand new one.
    client.post("/api/auth/patient/otp/request", json={"whatsapp_number": "+91 91234 56780"})
    otp2 = client.get(
        "/api/auth/patient/otp/_dev_lookup", params={"whatsapp_number": "+91 91234 56780"}
    ).json()["otp_code"]
    verify2 = client.post(
        "/api/auth/patient/otp/verify",
        json={"whatsapp_number": "+91 91234 56780", "otp": otp2},
    )
    assert verify2.status_code == 200
    assert "registration_required" not in verify2.json()
    assert verify2.json()["patient"]["id"] == patient_id
    assert verify2.json()["is_new_patient"] is False

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM patients WHERE whatsapp_number = %s",
            ("+919123456780",),
        )
        assert cur.fetchone()[0] == 1


def test_admin_create_patient_normalizes_and_conflicts_on_duplicate(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    created = client.post(
        "/api/patients",
        json={"name": "Admin Created", "whatsapp_number": "9988776655"},
        headers=admin_headers,
    )
    assert created.status_code in (200, 201)
    assert created.json()["whatsapp_number"] == "+919988776655"

    # A staff member typing the SAME person's number in a different
    # format must not silently create a second patient record.
    duplicate = client.post(
        "/api/patients",
        json={"name": "Admin Created Again", "whatsapp_number": "+91-99887-76655"},
        headers=admin_headers,
    )
    assert duplicate.status_code == 409

    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM patients WHERE whatsapp_number = %s",
            ("+919988776655",),
        )
        assert cur.fetchone()[0] == 1


def test_admin_create_patient_with_already_e164_number_is_unaffected(client, db_connection):
    # Regression guard: a number that's already in the canonical form
    # (e.g. copy-pasted from WhatsApp) must pass through unchanged.
    admin_headers = create_admin_and_get_headers(db_connection)
    created = client.post(
        "/api/patients",
        json={"name": "Already Canonical", "whatsapp_number": "+919555111222"},
        headers=admin_headers,
    )
    assert created.status_code in (200, 201)
    assert created.json()["whatsapp_number"] == "+919555111222"
