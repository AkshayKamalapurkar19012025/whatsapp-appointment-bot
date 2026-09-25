"""
Tests for OPD/HIMS master spec sections 67-68 (module licensing/
enablement + degradation): GET/PATCH /api/hospitals/{id}/modules[/...],
and the three degradation gates (LAB_RADIOLOGY on order creation,
PHARMACY on prescribing/dispensing, PACKAGES on package creation and
billing-via-package).

Every gating test runs against a freshly created hospital (never
hospital 1, the shared default every other test in the suite relies on
having every module enabled) -- hospital_modules is deliberately
reference data (not in tests/conftest.py's APP_TABLES, so it survives
between tests, same as hospitals/roles/permissions), so a test that
disabled hospital 1's own modules would leak into every test that runs
after it. A brand new hospital has no hospital_modules rows at all,
which is itself the "never licensed" case worth testing on its own.
"""

import secrets
from datetime import date, timedelta

from app.services.staff_auth import login as _staff_login
from tests.helpers import create_admin_and_get_headers, create_staff_for_test, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _new_hospital(db_connection) -> int:
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO hospitals (code, name) VALUES (%s, %s) RETURNING id",
            (f"TEST-MOD-{secrets.token_hex(4)}", "Module Test Hospital"),
        )
        (hospital_id,) = cur.fetchone()
    db_connection.commit()
    return hospital_id


def _admin_for_hospital(db_connection, hospital_id: int) -> dict:
    """A fresh ADMIN account created, then reassigned to hospital_id by
    its own known staff id (never an ambiguous "last session" lookup --
    seed_basic_doctor below also creates its own internal admin, so
    more than one staff_sessions row can exist by the time this runs).
    staff_auth.py resolves hospital_id fresh from the DB on every
    request, so reassigning after the token is issued still takes
    effect on every call made with these headers."""
    username = f"modtest-admin-{secrets.token_hex(4)}"
    password = "modtest-admin-password"  # noqa: S105 -- test-only
    account = create_staff_for_test(db_connection, username=username, password=password, role="ADMIN")

    with db_connection.cursor() as cur:
        cur.execute("UPDATE staff SET hospital_id = %s WHERE id = %s", (hospital_id, account["id"]))
        result = _staff_login(cur, username, password)
    db_connection.commit()

    return {"Authorization": f"Bearer {result['session_token']}"}


def _checked_in_context_for_hospital(client, db_connection, doctor_name: str) -> dict:
    """Same shape as every other _checked_in_context helper in this
    suite, but the doctor (and therefore the encounter/appointment) is
    moved to a freshly created hospital, and booked/checked-in by an
    admin who belongs to that same hospital."""
    seeded = seed_basic_doctor(
        client, db_connection, doctor_name=doctor_name,
        department_name=f"{doctor_name} Dept", appointment_type_name=f"{doctor_name} Type",
    )
    hospital_id = _new_hospital(db_connection)
    admin_headers = _admin_for_hospital(db_connection, hospital_id)

    with db_connection.cursor() as cur:
        cur.execute("UPDATE doctors SET hospital_id = %s WHERE id = %s", (hospital_id, seeded["doctor_id"]))
    db_connection.commit()

    patient = client.post(
        "/api/patients",
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9195{abs(hash(doctor_name)) % 10**8:08d}"},
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

    return {"admin_headers": admin_headers, "appointment_id": created["id"], "hospital_id": hospital_id}


def _license_and_enable(client, admin_headers, hospital_id: int, module_key: str):
    lic = client.patch(
        f"/api/hospitals/{hospital_id}/modules/{module_key}/license",
        json={"licensed": True},
        headers=admin_headers,
    )
    assert lic.status_code == 200, lic.text
    en = client.patch(
        f"/api/hospitals/{hospital_id}/modules/{module_key}/enable",
        json={"enabled": True},
        headers=admin_headers,
    )
    assert en.status_code == 200, en.text


# ---------------------------------------------------------------------
# Licensing/enablement API
# ---------------------------------------------------------------------


def test_new_hospital_starts_with_every_module_unlicensed_and_unavailable(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    hospital_id = _new_hospital(db_connection)

    response = client.get(f"/api/hospitals/{hospital_id}/modules", headers=admin_headers)
    assert response.status_code == 200
    modules = {m["module_key"]: m for m in response.json()}
    assert set(modules.keys()) == {"LAB_RADIOLOGY", "PHARMACY", "PACKAGES"}
    for m in modules.values():
        assert m["licensed"] is False
        assert m["enabled"] is False
        assert m["available"] is False


def test_seeded_hospital_has_every_module_licensed_and_enabled(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get("/api/hospitals/1/modules", headers=admin_headers)
    assert response.status_code == 200
    for m in response.json():
        assert m["licensed"] is True
        assert m["enabled"] is True
        assert m["available"] is True


def test_hospital_admin_cannot_enable_an_unlicensed_module(client, db_connection):
    # Master spec section 67: "Hospital admin must NOT be able to
    # self-grant paid modules."
    admin_headers = create_admin_and_get_headers(db_connection)
    hospital_id = _new_hospital(db_connection)

    response = client.patch(
        f"/api/hospitals/{hospital_id}/modules/PHARMACY/enable",
        json={"enabled": True},
        headers=admin_headers,
    )
    assert response.status_code == 403


def test_licensing_then_enabling_makes_a_module_available(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    hospital_id = _new_hospital(db_connection)

    _license_and_enable(client, admin_headers, hospital_id, "PHARMACY")

    modules = {
        m["module_key"]: m
        for m in client.get(f"/api/hospitals/{hospital_id}/modules", headers=admin_headers).json()
    }
    assert modules["PHARMACY"]["degradation"] == "BLOCKED"
    assert modules["PHARMACY"]["licensed"] is True
    assert modules["PHARMACY"]["enabled"] is True
    assert modules["PHARMACY"]["available"] is True


def test_revoking_license_auto_disables(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    hospital_id = _new_hospital(db_connection)
    _license_and_enable(client, admin_headers, hospital_id, "PACKAGES")

    revoke = client.patch(
        f"/api/hospitals/{hospital_id}/modules/PACKAGES/license",
        json={"licensed": False},
        headers=admin_headers,
    )
    assert revoke.status_code == 200
    assert revoke.json()["licensed"] is False
    assert revoke.json()["enabled"] is False


def test_relicensing_never_auto_enables(client, db_connection):
    # A module that was licensed+enabled, then un-licensed (which force-
    # disables it), then re-licensed, must stay disabled -- re-enabling
    # is always its own separate, deliberate action.
    admin_headers = create_admin_and_get_headers(db_connection)
    hospital_id = _new_hospital(db_connection)
    _license_and_enable(client, admin_headers, hospital_id, "LAB_RADIOLOGY")

    client.patch(f"/api/hospitals/{hospital_id}/modules/LAB_RADIOLOGY/license", json={"licensed": False}, headers=admin_headers)
    relicense = client.patch(f"/api/hospitals/{hospital_id}/modules/LAB_RADIOLOGY/license", json={"licensed": True}, headers=admin_headers)

    assert relicense.status_code == 200
    assert relicense.json()["licensed"] is True
    assert relicense.json()["enabled"] is False


def test_module_endpoints_reject_non_admin(client, db_connection):
    from tests.helpers import create_staff_and_get_headers

    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    hospital_id = _new_hospital(db_connection)

    assert client.get(f"/api/hospitals/{hospital_id}/modules", headers=staff_headers).status_code == 403
    assert client.patch(
        f"/api/hospitals/{hospital_id}/modules/PHARMACY/license", json={"licensed": True}, headers=staff_headers
    ).status_code == 403


def test_unknown_module_key_rejected(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    hospital_id = _new_hospital(db_connection)
    response = client.patch(
        f"/api/hospitals/{hospital_id}/modules/NOT_A_REAL_MODULE/license",
        json={"licensed": True},
        headers=admin_headers,
    )
    assert response.status_code == 422  # Literal type rejects it before the service layer


# ---------------------------------------------------------------------
# Degradation: LAB_RADIOLOGY -> EXTERNAL
# ---------------------------------------------------------------------


def test_lab_order_blocked_when_module_unavailable_but_external_referral_still_works(client, db_connection):
    ctx = _checked_in_context_for_hospital(client, db_connection, "Dr. ModTest Lab")

    blocked = client.post(
        f"/api/appointments/{ctx['appointment_id']}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=ctx["admin_headers"],
    )
    assert blocked.status_code == 403

    # The spec's own worked example: External Referral is the
    # degradation path, and it's unaffected by the LAB_RADIOLOGY gate.
    referral = client.post(
        f"/api/appointments/{ctx['appointment_id']}/orders",
        json={"order_type": "EXTERNAL_REFERRAL", "description": "CBC", "external_destination": "City Lab"},
        headers=ctx["admin_headers"],
    )
    assert referral.status_code == 200


def test_lab_order_succeeds_once_module_is_available(client, db_connection):
    ctx = _checked_in_context_for_hospital(client, db_connection, "Dr. ModTest LabEnabled")
    _license_and_enable(client, ctx["admin_headers"], ctx["hospital_id"], "LAB_RADIOLOGY")

    response = client.post(
        f"/api/appointments/{ctx['appointment_id']}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200


def test_disabling_lab_module_never_hides_historical_orders(client, db_connection):
    ctx = _checked_in_context_for_hospital(client, db_connection, "Dr. ModTest LabHistory")
    _license_and_enable(client, ctx["admin_headers"], ctx["hospital_id"], "LAB_RADIOLOGY")
    order = client.post(
        f"/api/appointments/{ctx['appointment_id']}/orders",
        json={"order_type": "LAB", "description": "CBC"},
        headers=ctx["admin_headers"],
    ).json()

    client.patch(
        f"/api/hospitals/{ctx['hospital_id']}/modules/LAB_RADIOLOGY/enable",
        json={"enabled": False},
        headers=ctx["admin_headers"],
    )

    listing = client.get(f"/api/appointments/{ctx['appointment_id']}/orders", headers=ctx["admin_headers"])
    assert listing.status_code == 200
    assert any(o["id"] == order["id"] for o in listing.json())


# ---------------------------------------------------------------------
# Degradation: PHARMACY -> BLOCKED
# ---------------------------------------------------------------------


def test_prescribing_blocked_when_pharmacy_unavailable(client, db_connection):
    ctx = _checked_in_context_for_hospital(client, db_connection, "Dr. ModTest Pharmacy")

    response = client.post(
        f"/api/appointments/{ctx['appointment_id']}/prescription/items",
        json={"medicine_name": "Paracetamol", "quantity": 10},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 403


def test_prescribing_and_dispensing_succeed_once_pharmacy_is_available(client, db_connection):
    ctx = _checked_in_context_for_hospital(client, db_connection, "Dr. ModTest PharmacyEnabled")
    _license_and_enable(client, ctx["admin_headers"], ctx["hospital_id"], "PHARMACY")

    item = client.post(
        f"/api/appointments/{ctx['appointment_id']}/prescription/items",
        json={"medicine_name": "Paracetamol", "quantity": 10},
        headers=ctx["admin_headers"],
    )
    assert item.status_code == 200

    prescribed = client.post(
        f"/api/appointments/{ctx['appointment_id']}/prescription/prescribe",
        headers=ctx["admin_headers"],
    )
    assert prescribed.status_code == 200


# ---------------------------------------------------------------------
# Degradation: PACKAGES -> HIDDEN
# ---------------------------------------------------------------------


def test_creating_a_package_blocked_when_packages_module_unavailable(client, db_connection):
    hospital_id = _new_hospital(db_connection)
    admin_headers = _admin_for_hospital(db_connection, hospital_id)

    response = client.post(
        "/api/packages",
        json={"name": "Health Checkup Basic", "price": 999},
        headers=admin_headers,
    )
    assert response.status_code == 403


def test_billing_via_package_blocked_when_packages_module_unavailable(client, db_connection):
    # The package itself is created while PACKAGES is available (so it
    # exists to bill against), then the module is disabled -- billing a
    # *new* charge through it must now be blocked even though the
    # package row itself still exists.
    hospital_id = _new_hospital(db_connection)
    admin_headers = _admin_for_hospital(db_connection, hospital_id)
    _license_and_enable(client, admin_headers, hospital_id, "PACKAGES")

    package = client.post(
        "/api/packages", json={"name": "Health Checkup Full", "price": 1499}, headers=admin_headers
    ).json()

    seeded = seed_basic_doctor(
        client, db_connection, doctor_name="Dr. ModTest PackageBilling",
        department_name="ModTest PackageBilling Dept", appointment_type_name="ModTest PackageBilling Type",
    )
    with db_connection.cursor() as cur:
        cur.execute("UPDATE doctors SET hospital_id = %s WHERE id = %s", (hospital_id, seeded["doctor_id"]))
    db_connection.commit()

    patient = client.post(
        "/api/patients",
        json={"name": "ModTest PackageBilling Patient", "whatsapp_number": "+919511112222"},
        headers=admin_headers,
    ).json()
    scheduling_date = _next_weekday(date.today() + timedelta(days=10))
    appointment = client.post(
        "/api/appointments",
        json={
            "doctor_id": seeded["doctor_id"],
            "patient_id": patient["id"],
            "appointment_type_id": seeded["appointment_type_id"],
            "start_at": f"{scheduling_date.isoformat()}T09:00:00+05:30",
        },
        headers=admin_headers,
    ).json()
    checkin = client.post(f"/api/appointments/{appointment['id']}/confirm-and-checkin", headers=admin_headers)
    assert checkin.status_code == 200

    client.patch(
        f"/api/hospitals/{hospital_id}/modules/PACKAGES/enable", json={"enabled": False}, headers=admin_headers
    )

    response = client.post(
        f"/api/appointments/{appointment['id']}/bill/charges",
        json={
            "description": "Health Checkup Full",
            "amount": 1499,
            "source_type": "PACKAGE",
            "source_package_id": package["id"],
        },
        headers=admin_headers,
    )
    assert response.status_code == 403
