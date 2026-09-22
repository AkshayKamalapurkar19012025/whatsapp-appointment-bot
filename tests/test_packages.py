"""
Tests for the package catalog and its billing integration (OPD/HIMS
master spec Phase 12, section 39: migrations/0038_packages.sql) --
GET/GET admin/POST/PUT /api/packages, PATCH .../active, and billing a
package as a charge via POST /api/appointments/{id}/bill/charges with
source_package_id. Also covers the insurance/TPA extension point
(section 40, migrations/0039_invoice_bill_type.sql): PATCH .../bill's
new bill_type field.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9196{abs(hash(doctor_name)) % 10**8:08d}"},
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
    checkin = client.post(f"/api/appointments/{created['id']}/confirm-and-checkin", headers=admin_headers)
    assert checkin.status_code == 200

    return {"admin_headers": admin_headers, "patient": patient, "appointment": created}


# ---------------------------------------------------------------------
# Catalog CRUD
# ---------------------------------------------------------------------


def test_create_and_list_package(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    created = client.post(
        "/api/packages",
        json={"name": "Health Checkup Basic", "description": "CBC + Lipid Panel + Consultation", "price": 1500},
        headers=admin_headers,
    )
    assert created.status_code == 200
    body = created.json()
    assert body["name"] == "Health Checkup Basic"
    assert body["price"] == 1500
    assert body["active"] is True

    listed = client.get("/api/packages", headers=admin_headers)
    assert listed.status_code == 200
    assert any(p["id"] == body["id"] for p in listed.json())


def test_create_package_requires_permission(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection)
    response = client.post(
        "/api/packages", json={"name": "Staff Attempt", "price": 100}, headers=staff_headers
    )
    assert response.status_code == 403


def test_duplicate_package_name_rejected(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    client.post("/api/packages", json={"name": "Antenatal Package", "price": 2000}, headers=admin_headers)
    dup = client.post("/api/packages", json={"name": "Antenatal Package", "price": 2500}, headers=admin_headers)
    assert dup.status_code == 409


def test_update_package(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    created = client.post(
        "/api/packages", json={"name": "Master Health Checkup", "price": 3000}, headers=admin_headers
    ).json()

    updated = client.put(
        f"/api/packages/{created['id']}",
        json={"name": "Master Health Checkup", "description": "Now includes ECG", "price": 3500},
        headers=admin_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["price"] == 3500
    assert updated.json()["description"] == "Now includes ECG"


def test_deactivate_and_reactivate_package(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    created = client.post(
        "/api/packages", json={"name": "Seasonal Flu Package", "price": 800}, headers=admin_headers
    ).json()

    deactivated = client.patch(
        f"/api/packages/{created['id']}/active", json={"active": False}, headers=admin_headers
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False

    # No longer in the plain (active-only) listing...
    listed = client.get("/api/packages", headers=admin_headers)
    assert not any(p["id"] == created["id"] for p in listed.json())

    # ...but still visible in the admin listing, which shows everything.
    admin_listed = client.get("/api/packages/admin", headers=admin_headers)
    assert any(p["id"] == created["id"] and p["active"] is False for p in admin_listed.json())

    reactivated = client.patch(
        f"/api/packages/{created['id']}/active", json={"active": True}, headers=admin_headers
    )
    assert reactivated.json()["active"] is True


# ---------------------------------------------------------------------
# Billing integration
# ---------------------------------------------------------------------


def test_bill_a_package_as_a_charge(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    pkg = client.post(
        "/api/packages", json={"name": "Diabetes Panel", "price": 1200}, headers=admin_headers
    ).json()

    ctx = _checked_in_context(client, db_connection, "Dr. Package Bill")
    appointment_id = ctx["appointment"]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": pkg["name"], "amount": pkg["price"], "source_type": "PACKAGE", "source_package_id": pkg["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    bill = response.json()
    charge = next(c for c in bill["charges"] if c["source_package_id"] == pkg["id"])
    assert charge["source_type"] == "PACKAGE"
    assert charge["amount"] == 1200
    assert bill["gross_amount"] == 1200


def test_bill_a_package_can_be_billed_more_than_once(client, db_connection):
    """Unlike an order or dispense charge (one charge per source, enforced
    by a unique index), a package is a reusable catalog entry -- the same
    package charged twice on the same invoice (e.g. two units) must not
    hit a uniqueness conflict."""
    admin_headers = create_admin_and_get_headers(db_connection)
    pkg = client.post(
        "/api/packages", json={"name": "Vaccination Package", "price": 600}, headers=admin_headers
    ).json()

    ctx = _checked_in_context(client, db_connection, "Dr. Package Repeat")
    appointment_id = ctx["appointment"]["id"]

    charge_payload = {
        "description": pkg["name"],
        "amount": pkg["price"],
        "source_type": "PACKAGE",
        "source_package_id": pkg["id"],
    }
    first = client.post(f"/api/appointments/{appointment_id}/bill/charges", json=charge_payload, headers=admin_headers)
    second = client.post(f"/api/appointments/{appointment_id}/bill/charges", json=charge_payload, headers=admin_headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["gross_amount"] == 1200


def test_bill_nonexistent_package_rejected(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Package Missing")
    appointment_id = ctx["appointment"]["id"]

    response = client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "Ghost Package", "amount": 100, "source_type": "PACKAGE", "source_package_id": 999999},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 404


def test_bill_inactive_package_rejected(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    pkg = client.post(
        "/api/packages", json={"name": "Discontinued Package", "price": 400}, headers=admin_headers
    ).json()
    client.patch(f"/api/packages/{pkg['id']}/active", json={"active": False}, headers=admin_headers)

    ctx = _checked_in_context(client, db_connection, "Dr. Package Inactive")
    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/bill/charges",
        json={"description": pkg["name"], "amount": pkg["price"], "source_type": "PACKAGE", "source_package_id": pkg["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------
# Insurance/TPA extension point (bill_type)
# ---------------------------------------------------------------------


def test_invoice_defaults_to_cash_bill_type(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. BillType Default")
    response = client.get(f"/api/appointments/{ctx['appointment']['id']}/bill", headers=ctx["admin_headers"])
    assert response.json()["bill_type"] == "CASH"


def test_update_bill_type(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. BillType Update")
    response = client.patch(
        f"/api/appointments/{ctx['appointment']['id']}/bill",
        json={"bill_type": "TPA"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    assert response.json()["bill_type"] == "TPA"

    # Updating discount only (bill_type omitted) leaves it untouched.
    response = client.patch(
        f"/api/appointments/{ctx['appointment']['id']}/bill",
        json={"discount_amount": 50},
        headers=ctx["admin_headers"],
    )
    assert response.json()["bill_type"] == "TPA"


def test_invalid_bill_type_rejected(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. BillType Invalid")
    response = client.patch(
        f"/api/appointments/{ctx['appointment']['id']}/bill",
        json={"bill_type": "BITCOIN"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 422
