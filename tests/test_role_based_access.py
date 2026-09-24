"""
Tests for master spec audit gap #3 -- "Role-based work is schema-only,
not real": migrations/0043_role_based_access.sql widens staff.role's
foreign key to every name in `roles` (previously CHECK-constrained to
just ADMIN/STAFF) and grants DOCTOR/PHARMACIST/BILLING a real subset of
the app's existing admin-tier permissions. RECEPTIONIST/LAB_TECH
deliberately get no new permission rows there -- see that migration's
own comment for why that's correct, not an oversight.

migrations/0048_clinical_rbac_permissions.sql later deepens this
further: vitals.record/consultation.write/order.create/
prescription.create, granted to NURSE (vitals only)/DOCTOR (all four)/
STAFF (all four, kept as a full-access generalist fallback rather than
forced onto one of the six specific roles) -- see that migration's own
docstring for the STAFF-stays-included decision.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def test_account_can_be_created_with_every_seeded_role(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)

    for role in ("DOCTOR", "NURSE", "RECEPTIONIST", "LAB_TECH", "PHARMACIST", "BILLING"):
        response = client.post(
            "/api/auth/staff/accounts",
            json={"username": f"rbac-{role.lower()}", "password": "a-strong-password", "role": role},
            headers=admin_headers,
        )
        assert response.status_code == 201, f"{role} -> {response.status_code}: {response.text}"
        assert response.json()["role"] == role


def test_unknown_role_is_rejected(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post(
        "/api/auth/staff/accounts",
        json={"username": "rbac-bogus", "password": "a-strong-password", "role": "SURGEON"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_new_role_account_can_log_in_and_reports_its_own_role(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    client.post(
        "/api/auth/staff/accounts",
        json={"username": "rbac-login-nurse", "password": "a-strong-password", "role": "NURSE"},
        headers=admin_headers,
    )

    login = client.post(
        "/api/auth/staff/login",
        json={"username": "rbac-login-nurse", "password": "a-strong-password"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['session_token']}"}

    me = client.get("/api/auth/staff/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["role"] == "NURSE"


def test_pharmacist_role_can_manage_stock_staff_role_cannot(client, db_connection):
    pharmacist_headers = create_staff_and_get_headers(db_connection, role="PHARMACIST")
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    body = {
        "medicine_name": "RBAC Test Medicine",
        "batch_number": "RBAC-BATCH-1",
        "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
        "quantity_on_hand": 10,
    }

    denied = client.post("/api/pharmacy/stock", json=body, headers=staff_headers)
    assert denied.status_code == 403

    allowed = client.post("/api/pharmacy/stock", json=body, headers=pharmacist_headers)
    assert allowed.status_code == 200
    assert allowed.json()["medicine_name"] == "RBAC Test Medicine"


def test_nurse_receptionist_lab_tech_cannot_manage_pharmacy_stock(client, db_connection):
    """migrations/0043 grants pharmacy.manage_stock to PHARMACIST only
    (plus ADMIN) -- NURSE/RECEPTIONIST/LAB_TECH hold no permission row
    for it (NURSE's own migrations/0048 grant is vitals.record only,
    a different permission entirely), so each is rejected here the same
    way STAFF was before migrations/0043 existed."""
    body = {
        "medicine_name": "RBAC Test Medicine 2",
        "batch_number": "RBAC-BATCH-2",
        "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
        "quantity_on_hand": 5,
    }
    for role in ("NURSE", "RECEPTIONIST", "LAB_TECH"):
        headers = create_staff_and_get_headers(db_connection, role=role)
        response = client.post("/api/pharmacy/stock", json=body, headers=headers)
        assert response.status_code == 403, f"{role} -> {response.status_code}"


# ---------------------------------------------------------------------
# migrations/0048_clinical_rbac_permissions.sql: vitals.record,
# consultation.write, order.create, prescription.create.
# ---------------------------------------------------------------------


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _checked_in_context(client, db_connection, doctor_name: str) -> dict:
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
    created = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    response = client.post(f"/api/appointments/{created['id']}/confirm-and-checkin", headers=admin_headers)
    assert response.status_code == 200
    return {"admin_headers": admin_headers, "appointment_id": created["id"]}


def test_nurse_and_doctor_can_record_vitals_receptionist_cannot(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. RBAC Vitals")
    body = {"pulse": 80}

    for role in ("NURSE", "DOCTOR"):
        headers = create_staff_and_get_headers(db_connection, role=role)
        response = client.post(f"/api/appointments/{ctx['appointment_id']}/vitals", json=body, headers=headers)
        assert response.status_code == 200, f"{role} -> {response.status_code}: {response.text}"

    receptionist_headers = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")
    denied = client.post(f"/api/appointments/{ctx['appointment_id']}/vitals", json=body, headers=receptionist_headers)
    assert denied.status_code == 403


def test_doctor_can_write_and_complete_consultation_nurse_cannot(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. RBAC Consultation")

    nurse_headers = create_staff_and_get_headers(db_connection, role="NURSE")
    denied = client.put(
        f"/api/appointments/{ctx['appointment_id']}/consultation",
        json={"chief_complaint": "Should not save"},
        headers=nurse_headers,
    )
    assert denied.status_code == 403

    doctor_headers = create_staff_and_get_headers(db_connection, role="DOCTOR")
    saved = client.put(
        f"/api/appointments/{ctx['appointment_id']}/consultation",
        json={"chief_complaint": "Fever", "diagnosis": "Viral fever"},
        headers=doctor_headers,
    )
    assert saved.status_code == 200

    completed = client.post(f"/api/appointments/{ctx['appointment_id']}/consultation/complete", headers=doctor_headers)
    assert completed.status_code == 200


def test_doctor_can_create_orders_pharmacist_cannot(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. RBAC Orders")
    body = {"order_type": "LAB", "description": "CBC"}

    pharmacist_headers = create_staff_and_get_headers(db_connection, role="PHARMACIST")
    denied = client.post(f"/api/appointments/{ctx['appointment_id']}/orders", json=body, headers=pharmacist_headers)
    assert denied.status_code == 403

    doctor_headers = create_staff_and_get_headers(db_connection, role="DOCTOR")
    allowed = client.post(f"/api/appointments/{ctx['appointment_id']}/orders", json=body, headers=doctor_headers)
    assert allowed.status_code == 200


def test_doctor_can_create_and_prescribe_prescription_billing_cannot(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. RBAC Prescription")
    body = {"medicine_name": "Paracetamol", "dosage": "500mg", "frequency": "TID", "duration": "3 days", "quantity": 9}

    billing_headers = create_staff_and_get_headers(db_connection, role="BILLING")
    denied = client.post(
        f"/api/appointments/{ctx['appointment_id']}/prescription/items", json=body, headers=billing_headers
    )
    assert denied.status_code == 403

    doctor_headers = create_staff_and_get_headers(db_connection, role="DOCTOR")
    added = client.post(
        f"/api/appointments/{ctx['appointment_id']}/prescription/items", json=body, headers=doctor_headers
    )
    assert added.status_code == 200

    prescribed = client.post(
        f"/api/appointments/{ctx['appointment_id']}/prescription/prescribe", headers=doctor_headers
    )
    assert prescribed.status_code == 200


def test_staff_keeps_full_clinical_documentation_access(client, db_connection):
    """The one deliberate departure from migrations/0043's own pattern
    (there, STAFF lost stock/billing-management/amend access): STAFF
    stays a full-access generalist fallback for vitals/consultation/
    orders/prescriptions rather than a forced migration off it, so an
    existing deployment with only plain STAFF accounts keeps working
    unchanged (migrations/0048's own docstring)."""
    ctx = _checked_in_context(client, db_connection, "Dr. RBAC Staff Fallback")
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")

    vitals = client.post(f"/api/appointments/{ctx['appointment_id']}/vitals", json={"pulse": 76}, headers=staff_headers)
    assert vitals.status_code == 200

    saved = client.put(
        f"/api/appointments/{ctx['appointment_id']}/consultation",
        json={"chief_complaint": "Cough"},
        headers=staff_headers,
    )
    assert saved.status_code == 200

    order = client.post(
        f"/api/appointments/{ctx['appointment_id']}/orders",
        json={"order_type": "LAB", "description": "Chest X-ray"},
        headers=staff_headers,
    )
    assert order.status_code == 200

    prescription = client.post(
        f"/api/appointments/{ctx['appointment_id']}/prescription/items",
        json={"medicine_name": "Cough Syrup", "dosage": "10ml", "frequency": "BID", "duration": "5 days", "quantity": 1},
        headers=staff_headers,
    )
    assert prescription.status_code == 200
