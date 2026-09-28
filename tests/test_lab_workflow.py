"""
Tests for the Laboratory diagnostic lifecycle (OPD/HIMS master spec
Phase 7, resumed by migrations/0054_diagnostic_workflow.sql):

    ORDERED -> COLLECTED -> IN_PROGRESS -> RESULT_ENTERED -> VERIFIED -> COMPLETED (released)

Sample collection (app/services/order_services.py's
record_sample_collection_service/reject_sample_service),
mark-in-progress, verify, and release -- everything test_orders.py/
test_order_results.py didn't already cover before this phase (those
two files' own LAB-order tests were updated to use SERVICE where their
point was orthogonal to this lifecycle, per each test's own comment).
"""

import threading
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
        json={"name": f"{doctor_name} Patient", "whatsapp_number": f"+9197{abs(hash(doctor_name)) % 10**8:08d}"},
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
    response = client.post(f"/api/appointments/{appointment['id']}/confirm-and-checkin", headers=admin_headers)
    assert response.status_code == 200

    return {"admin_headers": admin_headers, "seeded": seeded, "patient": patient, "appointment": appointment}


def _create_lab_order(client, appointment_id, headers, **overrides):
    payload = {"order_type": "LAB", "description": "CBC"}
    payload.update(overrides)
    return client.post(f"/api/appointments/{appointment_id}/orders", json=payload, headers=headers).json()


def _lab_tech_headers(db_connection):
    return create_staff_and_get_headers(db_connection, role="LAB_TECH")


# ---------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------


def test_full_lab_lifecycle_ends_completed_and_visible_to_doctor(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab Full")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    lab_tech_headers = _lab_tech_headers(db_connection)

    order = _create_lab_order(client, appointment_id, admin_headers)
    assert order["status"] == "ORDERED"

    collect = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood", "notes": "Fasting sample"},
        headers=lab_tech_headers,
    )
    assert collect.status_code == 200
    collect_body = collect.json()
    assert collect_body["status"] == "COLLECTED"
    assert len(collect_body["samples"]) == 1
    sample = collect_body["samples"][0]
    assert sample["sample_type"] == "Blood"
    assert sample["status"] == "COLLECTED"
    assert sample["sample_code"].startswith("LAB-")

    processing = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/start-processing",
        headers=lab_tech_headers,
    )
    assert processing.status_code == 200
    assert processing.json()["status"] == "IN_PROGRESS"

    result = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.4", "unit": "g/dL", "reference_range": "12-16"}]},
        headers=lab_tech_headers,
    )
    assert result.status_code == 200
    result_body = result.json()
    assert result_body["status"] == "RESULT_ENTERED"
    assert result_body["result_entered_at"] is not None
    assert len(result_body["results"]) == 1

    # A second technician must verify -- the same account that entered
    # the result is refused.
    same_tech_verify = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=lab_tech_headers,
    )
    assert same_tech_verify.status_code == 403

    verifier_headers = _lab_tech_headers(db_connection)
    verify = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=verifier_headers,
    )
    assert verify.status_code == 200
    verify_body = verify.json()
    assert verify_body["status"] == "VERIFIED"
    assert verify_body["verified_at"] is not None

    release = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/release",
        headers=verifier_headers,
    )
    assert release.status_code == 200
    release_body = release.json()
    assert release_body["status"] == "COMPLETED"
    assert release_body["completed_at"] is not None
    assert release_body["released_at"] is not None

    # Doctor sees the released result via the same order-listing
    # endpoint ConsultationWorkspace already uses.
    doctor_view = client.get(f"/api/appointments/{appointment_id}/orders", headers=admin_headers)
    assert doctor_view.status_code == 200
    released_order = next(o for o in doctor_view.json() if o["id"] == order["id"])
    assert released_order["status"] == "COMPLETED"
    assert released_order["results"][0]["parameter"] == "Hemoglobin"

    # The notification fires at release, not at result-entry.
    notifications = client.get("/api/notifications", headers=admin_headers).json()
    assert any(n["kind"] == "LAB_RESULT_AVAILABLE" for n in notifications["items"])


def test_admin_may_verify_their_own_result(client, db_connection):
    # No distinct pathologist role exists in this app -- ADMIN keeps
    # its existing system-wide override standing (migrations/0054's
    # own documented decision).
    ctx = _checked_in_context(client, db_connection, "Dr. Lab AdminVerify")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    order = _create_lab_order(client, appointment_id, admin_headers)
    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.4"}]},
        headers=admin_headers,
    )
    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "VERIFIED"


# ---------------------------------------------------------------------
# Sample collection / rejection / recollection
# ---------------------------------------------------------------------


def test_collect_sample_requires_lab_order_type(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab NotLabType")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "RADIOLOGY", "description": "Chest X-ray"},
        headers=admin_headers,
    ).json()

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_collect_sample_twice_without_rejection_is_conflict(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab DoubleCollect")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)

    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=admin_headers,
    )
    second = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=admin_headers,
    )
    assert second.status_code == 409


def test_reject_sample_reverts_order_to_ordered_and_allows_recollection(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab Reject")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)

    collected = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=admin_headers,
    ).json()
    sample_id = collected["samples"][0]["id"]

    rejected = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/samples/{sample_id}/reject",
        json={"reason": "Hemolyzed sample"},
        headers=admin_headers,
    )
    assert rejected.status_code == 200
    rejected_body = rejected.json()
    assert rejected_body["status"] == "ORDERED"
    assert rejected_body["samples"][0]["status"] == "REJECTED"
    assert rejected_body["samples"][0]["rejected_reason"] == "Hemolyzed sample"

    # Recollect -- a fresh row, the rejected one stays as history.
    recollected = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=admin_headers,
    )
    assert recollected.status_code == 200
    recollected_body = recollected.json()
    assert recollected_body["status"] == "COLLECTED"
    assert len(recollected_body["samples"]) == 2
    assert {s["status"] for s in recollected_body["samples"]} == {"COLLECTED", "REJECTED"}


def test_reject_already_rejected_sample_is_conflict(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab DoubleReject")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)

    collected = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=admin_headers,
    ).json()
    sample_id = collected["samples"][0]["id"]

    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/samples/{sample_id}/reject",
        json={"reason": "Wrong tube"},
        headers=admin_headers,
    )
    # Order is back to ORDERED, so a second reject attempt on the same
    # (already-rejected) sample row must fail on the sample's own
    # state, not the order's.
    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=admin_headers,
    )
    second_reject = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/samples/{sample_id}/reject",
        json={"reason": "Trying again"},
        headers=admin_headers,
    )
    assert second_reject.status_code == 409


def test_reject_sample_requires_a_reason(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab RejectNoReason")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)
    collected = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=admin_headers,
    ).json()

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/samples/{collected['samples'][0]['id']}/reject",
        json={"reason": ""},
        headers=admin_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------
# Result entry does not hard-require collection first (documented,
# deliberate scope decision -- migrations/0054's header).
# ---------------------------------------------------------------------


def test_result_can_be_entered_without_prior_collection(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab NoCollectionFirst")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.4"}]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "RESULT_ENTERED"


# ---------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------


def test_cannot_verify_before_result_entered(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab VerifyTooSoon")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_cannot_release_before_verified(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab ReleaseTooSoon")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)
    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.4"}]},
        headers=admin_headers,
    )

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/release",
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_cannot_record_a_second_result_once_result_entered(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab DoubleResult")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)
    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.4"}]},
        headers=admin_headers,
    )

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.9"}]},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_cannot_double_verify(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab DoubleVerify")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)
    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.4"}]},
        headers=admin_headers,
    )
    client.post(f"/api/appointments/{appointment_id}/orders/{order['id']}/verify", headers=admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_cannot_double_release(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab DoubleRelease")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)
    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.4"}]},
        headers=admin_headers,
    )
    client.post(f"/api/appointments/{appointment_id}/orders/{order['id']}/verify", headers=admin_headers)
    client.post(f"/api/appointments/{appointment_id}/orders/{order['id']}/release", headers=admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/release",
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_start_processing_requires_collection_first(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab ProcessTooSoon")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)

    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/start-processing",
        headers=admin_headers,
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------


def test_receptionist_cannot_collect_sample(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab RBAC Collect")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)

    receptionist_headers = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")
    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=receptionist_headers,
    )
    assert response.status_code == 403


def test_receptionist_cannot_verify_or_release(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab RBAC VerifyRelease")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)
    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.4"}]},
        headers=admin_headers,
    )

    receptionist_headers = create_staff_and_get_headers(db_connection, role="RECEPTIONIST")
    verify = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=receptionist_headers,
    )
    assert verify.status_code == 403

    release = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/release",
        headers=receptionist_headers,
    )
    assert release.status_code == 403


def test_lab_tech_can_collect_verify_and_release(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab RBAC LabTech")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)

    enterer_headers = _lab_tech_headers(db_connection)
    verifier_headers = _lab_tech_headers(db_connection)

    assert client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=enterer_headers,
    ).status_code == 200
    assert client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.4"}]},
        headers=enterer_headers,
    ).status_code == 200
    assert client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=verifier_headers,
    ).status_code == 200
    assert client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/release",
        headers=verifier_headers,
    ).status_code == 200


def test_diagnostic_actions_require_staff_auth(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab Auth")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)

    assert client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
    ).status_code == 401
    assert client.post(f"/api/appointments/{appointment_id}/orders/{order['id']}/start-processing").status_code == 401
    assert client.post(f"/api/appointments/{appointment_id}/orders/{order['id']}/verify").status_code == 401
    assert client.post(f"/api/appointments/{appointment_id}/orders/{order['id']}/release").status_code == 401


# ---------------------------------------------------------------------
# Worklist visibility across the new intermediate states
# ---------------------------------------------------------------------


def test_default_worklist_includes_unverified_and_unreleased_lab_orders(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab WorklistVisibility")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)

    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.4"}]},
        headers=admin_headers,
    )
    worklist = client.get("/api/orders/worklist", headers=admin_headers).json()
    assert order["id"] in {row["id"] for row in worklist}, "RESULT_ENTERED order should still be pending work"

    client.post(f"/api/appointments/{appointment_id}/orders/{order['id']}/verify", headers=admin_headers)
    worklist = client.get("/api/orders/worklist", headers=admin_headers).json()
    assert order["id"] in {row["id"] for row in worklist}, "VERIFIED order should still be pending release"

    client.post(f"/api/appointments/{appointment_id}/orders/{order['id']}/release", headers=admin_headers)
    worklist = client.get("/api/orders/worklist", headers=admin_headers).json()
    assert order["id"] not in {row["id"] for row in worklist}, "Released (COMPLETED) order should leave the worklist"


# ---------------------------------------------------------------------
# Non-diagnostic order types are unaffected
# ---------------------------------------------------------------------


def test_diagnostic_endpoints_reject_non_diagnostic_order_types(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab NonDiagnostic")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]

    order = client.post(
        f"/api/appointments/{appointment_id}/orders",
        json={"order_type": "PROCEDURE", "description": "Wound dressing"},
        headers=admin_headers,
    ).json()

    assert client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=admin_headers,
    ).status_code == 422
    assert client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/start-processing",
        headers=admin_headers,
    ).status_code == 422
    assert client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
        headers=admin_headers,
    ).status_code == 422
    assert client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/release",
        headers=admin_headers,
    ).status_code == 422

    # And a PROCEDURE order still completes in one step, exactly as
    # before Phase 7.
    response = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Outcome", "result_value": "Dressed, no complications"}]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"


# ---------------------------------------------------------------------
# Billing integration untouched: an order can still be billed at any
# pre-release stage (list_unbilled_sources_service only excludes
# CANCELLED), and cancellation still works mid-lifecycle.
# ---------------------------------------------------------------------


def test_collected_lab_order_is_still_billable_and_cancellable(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab Billing")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)
    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
        json={"sample_type": "Blood"},
        headers=admin_headers,
    )

    unbilled = client.get(f"/api/appointments/{appointment_id}/bill/unbilled", headers=admin_headers)
    assert unbilled.status_code == 200
    assert order["id"] in {o["order_id"] for o in unbilled.json()["orders"]}

    charge = client.post(
        f"/api/appointments/{appointment_id}/bill/charges",
        json={"description": "CBC", "amount": 300.00, "source_type": "LAB", "source_order_id": order["id"]},
        headers=admin_headers,
    )
    assert charge.status_code == 200

    cancel = client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/cancel",
        json={"reason": "Patient declined"},
        headers=admin_headers,
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "CANCELLED"


# ---------------------------------------------------------------------
# Concurrency -- same "FOR UPDATE row lock, two threads race, exactly
# one wins" pattern as tests/test_concurrency_hardening.py's payment/
# dispense tests, applied to _lock_order (app/services/
# order_services.py), the shared row-lock helper every diagnostic
# transition in this file goes through.
# ---------------------------------------------------------------------


def test_two_technicians_collecting_the_same_order_only_one_wins(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab ConcurrentCollect")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)

    results = {}
    barrier = threading.Barrier(2)

    def do_collect(key):
        barrier.wait()
        response = client.post(
            f"/api/appointments/{appointment_id}/orders/{order['id']}/collect-sample",
            json={"sample_type": "Blood"},
            headers=admin_headers,
        )
        results[key] = response.status_code

    threads = [threading.Thread(target=do_collect, args=(k,)) for k in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    succeeded = [k for k, code in results.items() if code == 200]
    assert len(succeeded) == 1, f"expected exactly one 200, got {results}"
    assert sorted(results.values()) == [200, 409]

    with db_connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM lab_samples WHERE order_id = %s", (order["id"],))
        (sample_count,) = cur.fetchone()
    assert sample_count == 1, f"expected exactly one lab_samples row, found {sample_count}"


def test_two_staff_verifying_the_same_result_only_one_wins(client, db_connection):
    ctx = _checked_in_context(client, db_connection, "Dr. Lab ConcurrentVerify")
    appointment_id = ctx["appointment"]["id"]
    admin_headers = ctx["admin_headers"]
    order = _create_lab_order(client, appointment_id, admin_headers)
    client.post(
        f"/api/appointments/{appointment_id}/orders/{order['id']}/result",
        json={"items": [{"parameter": "Hemoglobin", "result_value": "13.4"}]},
        headers=admin_headers,
    )

    verifier_a = _lab_tech_headers(db_connection)
    verifier_b = _lab_tech_headers(db_connection)
    results = {}
    barrier = threading.Barrier(2)

    def do_verify(key, headers):
        barrier.wait()
        response = client.post(
            f"/api/appointments/{appointment_id}/orders/{order['id']}/verify",
            headers=headers,
        )
        results[key] = response.status_code

    threads = [
        threading.Thread(target=do_verify, args=("a", verifier_a)),
        threading.Thread(target=do_verify, args=("b", verifier_b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    succeeded = [k for k, code in results.items() if code == 200]
    assert len(succeeded) == 1, f"expected exactly one 200, got {results}"
    assert sorted(results.values()) == [200, 409]
