"""
Tests for the Medication Master (OPD/HIMS interoperability master prompt
Phase 5, migrations/0054_medication_master.sql):
GET/POST /api/pharmacy/medications, PATCH /api/pharmacy/medications/{id},
PATCH /api/pharmacy/medications/{id}/active, and the optional
medication_id link on POST .../prescription/items and POST/GET
/api/pharmacy/stock.

See docs/OPD_HIMS_STANDARDS_READINESS.md S5/S16/S17 for the approved
design and docs/workflows/PHARMACY.md for the documented behavior this
suite exercises.
"""

from datetime import date, timedelta

from tests.helpers import create_admin_and_get_headers, create_staff_and_get_headers, seed_basic_doctor


def _next_weekday(from_date: date | None = None) -> date:
    d = (from_date or date.today()) + timedelta(days=1)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


def _checked_in_patient(client, db_connection, doctor_name: str) -> dict:
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
    checkin = client.post(
        f"/api/appointments/{appointment['id']}/confirm-and-checkin", headers=admin_headers
    )
    assert checkin.status_code == 200
    return {"admin_headers": admin_headers, "patient": patient, "appointment": appointment}


def _create_medication(client, admin_headers, **overrides):
    payload = {"generic_name": "Test Amoxicillin"}
    payload.update(overrides)
    return client.post("/api/pharmacy/medications", json=payload, headers=admin_headers)


# ---------------------------------------------------------------------
# Medication creation / admin management
# ---------------------------------------------------------------------


def test_create_medication_requires_admin(client, db_connection):
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    response = _create_medication(client, staff_headers, generic_name="Staff Attempt Med")
    assert response.status_code == 403


def test_create_medication_minimal(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = _create_medication(client, admin_headers, generic_name="Minimal Med")
    assert response.status_code == 200
    body = response.json()
    assert body["generic_name"] == "Minimal Med"
    assert body["active"] is True
    assert body["display_name"] == "Minimal Med"


def test_create_medication_full_fields_and_display_name(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = _create_medication(
        client,
        admin_headers,
        generic_name="Paracetamol",
        brand_name="Panadol",
        strength="500mg",
        dosage_form="Tablet",
        default_route="Oral",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Paracetamol (Panadol) 500mg Tablet"


def test_create_duplicate_medication_is_409(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    payload = {"generic_name": "Dup Med", "brand_name": "DupBrand", "strength": "10mg", "dosage_form": "Tablet"}
    first = client.post("/api/pharmacy/medications", json=payload, headers=admin_headers)
    assert first.status_code == 200
    second = client.post("/api/pharmacy/medications", json=payload, headers=admin_headers)
    assert second.status_code == 409


def test_different_strength_is_not_a_duplicate(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    base = {"generic_name": "Strength Med", "brand_name": "SBrand", "dosage_form": "Tablet"}
    first = client.post("/api/pharmacy/medications", json={**base, "strength": "250mg"}, headers=admin_headers)
    second = client.post("/api/pharmacy/medications", json={**base, "strength": "500mg"}, headers=admin_headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] != second.json()["id"]


def test_update_medication(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    created = _create_medication(client, admin_headers, generic_name="Update Me").json()

    response = client.patch(
        f"/api/pharmacy/medications/{created['id']}",
        json={"generic_name": "Updated Name", "strength": "20mg"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["generic_name"] == "Updated Name"
    assert response.json()["strength"] == "20mg"


def test_update_nonexistent_medication_is_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.patch(
        "/api/pharmacy/medications/999999", json={"generic_name": "Ghost"}, headers=admin_headers
    )
    assert response.status_code == 404


def test_deactivate_and_reactivate_medication(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    created = _create_medication(client, admin_headers, generic_name="Toggle Me").json()

    deactivated = client.patch(
        f"/api/pharmacy/medications/{created['id']}/active", json={"active": False}, headers=admin_headers
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False

    # No longer returned by the default (active-only) list.
    listing = client.get("/api/pharmacy/medications", headers=admin_headers).json()
    assert not any(m["id"] == created["id"] for m in listing)

    # Still visible with include_inactive=True.
    listing_all = client.get(
        "/api/pharmacy/medications", params={"include_inactive": True}, headers=admin_headers
    ).json()
    assert any(m["id"] == created["id"] for m in listing_all)

    reactivated = client.patch(
        f"/api/pharmacy/medications/{created['id']}/active", json={"active": True}, headers=admin_headers
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["active"] is True


def test_deactivate_toggle_requires_admin(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    staff_headers = create_staff_and_get_headers(db_connection, role="STAFF")
    created = _create_medication(client, admin_headers, generic_name="Staff Cannot Toggle").json()

    response = client.patch(
        f"/api/pharmacy/medications/{created['id']}/active", json={"active": False}, headers=staff_headers
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------


def test_search_by_generic_name_case_insensitive(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    _create_medication(client, admin_headers, generic_name="Searchable Ibuprofen")

    response = client.get(
        "/api/pharmacy/medications", params={"search": "searchable ibu"}, headers=admin_headers
    )
    assert response.status_code == 200
    assert any(m["generic_name"] == "Searchable Ibuprofen" for m in response.json())


def test_search_by_brand_name(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    _create_medication(client, admin_headers, generic_name="Some Generic", brand_name="UniqueBrandXyz")

    response = client.get(
        "/api/pharmacy/medications", params={"search": "UniqueBrandXyz"}, headers=admin_headers
    )
    assert any(m["brand_name"] == "UniqueBrandXyz" for m in response.json())


def test_search_by_strength(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    _create_medication(client, admin_headers, generic_name="Strength Search Med", strength="777mg")

    response = client.get("/api/pharmacy/medications", params={"search": "777mg"}, headers=admin_headers)
    assert any(m["strength"] == "777mg" for m in response.json())


def test_search_no_match_returns_empty(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.get(
        "/api/pharmacy/medications", params={"search": "NoSuchMedicineNameAtAllXyz123"}, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------
# Prescription integration
# ---------------------------------------------------------------------


def test_prescription_item_still_works_without_medication_id(client, db_connection):
    """Backward compatibility: medication_id is fully optional."""
    ctx = _checked_in_patient(client, db_connection, "Dr Med None")
    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/prescription/items",
        json={"medicine_name": "Freetext Only Med", "quantity": 5},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["prescription"]["items"][0]["medicine_name"] == "Freetext Only Med"


def test_prescription_item_with_medication_id(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Med Linked")
    medication = _create_medication(
        client, ctx["admin_headers"], generic_name="Linked Med", brand_name="LinkedBrand"
    ).json()

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/prescription/items",
        json={
            "medicine_name": "LinkedBrand",
            "quantity": 5,
            "medication_id": medication["id"],
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 200
    item = response.json()["prescription"]["items"][0]
    assert item["medicine_name"] == "LinkedBrand"
    assert item.get("medication_id") == medication["id"]


def test_prescription_item_with_unknown_medication_id_is_404(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Med Unknown")
    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/prescription/items",
        json={"medicine_name": "Whatever", "quantity": 5, "medication_id": 999999},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 404


def test_prescription_item_with_inactive_medication_id_is_409(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Med Inactive")
    medication = _create_medication(client, ctx["admin_headers"], generic_name="Inactive Rx Med").json()
    client.patch(
        f"/api/pharmacy/medications/{medication['id']}/active", json={"active": False}, headers=ctx["admin_headers"]
    )

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/prescription/items",
        json={"medicine_name": "Inactive Rx Med", "quantity": 5, "medication_id": medication["id"]},
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 409


def test_historical_prescription_item_readable_after_medication_deactivated(client, db_connection):
    """A medication deactivated after being prescribed must not affect
    the readability of that already-created prescription item."""
    ctx = _checked_in_patient(client, db_connection, "Dr Med Historical")
    medication = _create_medication(client, ctx["admin_headers"], generic_name="Was Active Med").json()

    add = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/prescription/items",
        json={"medicine_name": "Was Active Med", "quantity": 5, "medication_id": medication["id"]},
        headers=ctx["admin_headers"],
    )
    assert add.status_code == 200

    deactivate = client.patch(
        f"/api/pharmacy/medications/{medication['id']}/active", json={"active": False}, headers=ctx["admin_headers"]
    )
    assert deactivate.status_code == 200

    prescription = client.get(
        f"/api/appointments/{ctx['appointment']['id']}/prescription", headers=ctx["admin_headers"]
    ).json()
    assert prescription["items"][0]["medicine_name"] == "Was Active Med"
    assert prescription["items"][0].get("medication_id") == medication["id"]


# ---------------------------------------------------------------------
# Pharmacy stock integration
# ---------------------------------------------------------------------


def _stock_payload(**overrides):
    payload = {
        "medicine_name": "Stock Med",
        "batch_number": f"B-MED-{abs(hash(str(overrides))) % 10**8}",
        "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
        "quantity_on_hand": 100,
        "unit_price": 2.5,
    }
    payload.update(overrides)
    return payload


def test_create_stock_with_medication_id(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    medication = _create_medication(client, admin_headers, generic_name="Stock Linked Med").json()

    response = client.post(
        "/api/pharmacy/stock",
        json=_stock_payload(medicine_name="Stock Linked Med", medication_id=medication["id"]),
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json().get("medication_id") == medication["id"]


def test_create_stock_with_unknown_medication_id_is_404(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    response = client.post(
        "/api/pharmacy/stock",
        json=_stock_payload(medication_id=999999),
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_create_stock_with_inactive_medication_id_is_409(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    medication = _create_medication(client, admin_headers, generic_name="Inactive Stock Med").json()
    client.patch(
        f"/api/pharmacy/medications/{medication['id']}/active", json={"active": False}, headers=admin_headers
    )

    response = client.post(
        "/api/pharmacy/stock",
        json=_stock_payload(medicine_name="Inactive Stock Med", medication_id=medication["id"]),
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_list_stock_filtered_by_medication_id(client, db_connection):
    admin_headers = create_admin_and_get_headers(db_connection)
    med_a = _create_medication(client, admin_headers, generic_name="Filter Med A").json()
    med_b = _create_medication(client, admin_headers, generic_name="Filter Med B").json()

    client.post(
        "/api/pharmacy/stock",
        json=_stock_payload(medicine_name="Filter Med A", medication_id=med_a["id"], batch_number="B-FILT-A"),
        headers=admin_headers,
    )
    client.post(
        "/api/pharmacy/stock",
        json=_stock_payload(medicine_name="Filter Med B", medication_id=med_b["id"], batch_number="B-FILT-B"),
        headers=admin_headers,
    )

    response = client.get(
        "/api/pharmacy/stock", params={"medication_id": med_a["id"]}, headers=admin_headers
    )
    assert response.status_code == 200
    results = response.json()
    assert all(s["medication_id"] == med_a["id"] for s in results)
    assert any(s["batch_number"] == "B-FILT-A" for s in results)


def test_shared_medication_identity_across_prescription_and_stock_dispenses(client, db_connection):
    """The core Phase 5 guarantee: a prescription item and a stock batch
    that share the same medication_id are treated as the same medicine
    for dispensing, even if their free-text names differ (e.g. brand vs.
    generic spelling)."""
    ctx = _checked_in_patient(client, db_connection, "Dr Med Shared Identity")
    admin_headers = ctx["admin_headers"]
    medication = _create_medication(
        client, admin_headers, generic_name="Shared Identity Med", brand_name="SharedBrand"
    ).json()

    stock = client.post(
        "/api/pharmacy/stock",
        json=_stock_payload(
            medicine_name="Shared Identity Med (generic spelling)",
            medication_id=medication["id"],
            batch_number="B-SHARED",
        ),
        headers=admin_headers,
    ).json()

    prescription = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/prescription/items",
        json={
            "medicine_name": "SharedBrand (brand spelling)",
            "quantity": 5,
            "medication_id": medication["id"],
        },
        headers=admin_headers,
    ).json()["prescription"]
    item_id = prescription["items"][0]["id"]
    client.post(f"/api/appointments/{ctx['appointment']['id']}/prescription/prescribe", headers=admin_headers)

    dispense = client.post(
        f"/api/pharmacy/items/{item_id}/dispense",
        json={"quantity": 5, "pharmacy_stock_id": stock["id"]},
        headers=admin_headers,
    )
    # Free text alone ("Shared Identity Med (generic spelling)" vs.
    # "SharedBrand (brand spelling)") would previously have 422'd as a
    # MedicineMismatch -- the shared medication_id now vouches for it.
    assert dispense.status_code == 200


# ---------------------------------------------------------------------
# Allergy check regression (Phase 4 baseline + Phase 5 extension)
# ---------------------------------------------------------------------


def _add_allergy(client, patient_id, admin_headers, allergen, severity="MODERATE"):
    response = client.post(
        f"/api/patients/{patient_id}/allergies",
        json={"allergen": allergen, "severity": severity, "reaction": "Rash"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    return response.json()


def test_allergy_check_still_fires_with_medication_id_and_matching_freetext(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Med Allergy Freetext")
    _add_allergy(client, ctx["patient"]["id"], ctx["admin_headers"], "Penicillin", severity="SEVERE")
    medication = _create_medication(client, ctx["admin_headers"], generic_name="Penicillin V").json()

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/prescription/items",
        json={"medicine_name": "Penicillin V", "quantity": 10, "medication_id": medication["id"]},
        headers=ctx["admin_headers"],
    )
    body = response.json()
    assert body["allergy_warning"] is not None
    assert body["allergy_warning"]["conflicts"][0]["allergen"] == "Penicillin"


def test_allergy_check_extended_by_canonical_medication_name(client, db_connection):
    """Phase 5 extension: the typed medicine_name ("Panadol") doesn't
    itself contain the allergen text, but the linked Medication
    Master's own generic_name ("Paracetamol") does -- so the check must
    still fire, because it also checks the resolved medication's
    generic_name/brand_name, not just what was typed."""
    ctx = _checked_in_patient(client, db_connection, "Dr Med Allergy Canonical")
    _add_allergy(client, ctx["patient"]["id"], ctx["admin_headers"], "Paracetamol", severity="MODERATE")
    medication = _create_medication(
        client, ctx["admin_headers"], generic_name="Paracetamol", brand_name="Panadol"
    ).json()

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/prescription/items",
        json={"medicine_name": "Panadol", "quantity": 10, "medication_id": medication["id"]},
        headers=ctx["admin_headers"],
    )
    body = response.json()
    assert body["allergy_warning"] is not None
    assert body["allergy_warning"]["conflicts"][0]["allergen"] == "Paracetamol"


def test_allergy_check_no_false_positive_from_unrelated_medication_id(client, db_connection):
    ctx = _checked_in_patient(client, db_connection, "Dr Med Allergy None")
    _add_allergy(client, ctx["patient"]["id"], ctx["admin_headers"], "Peanuts")
    medication = _create_medication(client, ctx["admin_headers"], generic_name="Totally Unrelated Med").json()

    response = client.post(
        f"/api/appointments/{ctx['appointment']['id']}/prescription/items",
        json={"medicine_name": "Totally Unrelated Med", "quantity": 10, "medication_id": medication["id"]},
        headers=ctx["admin_headers"],
    )
    assert response.json()["allergy_warning"] is None
