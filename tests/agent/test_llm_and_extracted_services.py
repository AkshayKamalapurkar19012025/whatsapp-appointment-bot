"""The model seam, plus regression tests for the small services extracted
from routers so the agent tools and the routes share one implementation."""

import json
from types import SimpleNamespace

import pytest

from app.agent.llm import AnthropicLLM, FakeLLM, LLMUnavailable, UnavailableLLM, build_default_llm
from app.services.appointment_services import list_appointments_service
from app.services.patient_lookup_service import get_patient_service, search_patients_service
from app.services.queue_read_service import get_doctor_queue_service
from app.services.staff_permissions import list_staff_permissions, staff_has_permission
from tests.helpers import create_staff_for_test


class _StubMessages:
    def __init__(self, response=None, error=None):
        self.calls, self.response, self.error = [], response, error

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.response


def _stub(text="{}", stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason, model="claude-opus-5-5",
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=11, output_tokens=7))


def test_anthropic_client_sends_a_static_system_prompt_and_json_payload_and_reads_usage():
    messages = _StubMessages(_stub('{"status": "out_of_scope"}'))
    llm = AnthropicLLM(model="claude-opus-5-5", client=SimpleNamespace(messages=messages))
    out = llm.complete(agent="intake", system="SYS", payload={"raw_input": "hi"})
    (call,) = messages.calls
    assert call["system"] == "SYS" and json.loads(call["messages"][0]["content"]) == {"raw_input": "hi"}
    assert call["model"] == "claude-opus-5-5" and call["output_config"] == {"effort": "low"}
    # thinking / sampling parameters are not sent (always-on thinking; sampling params are rejected)
    assert not ({"thinking", "temperature", "top_p", "top_k"} & set(call))
    assert out.text == '{"status": "out_of_scope"}' and (out.input_tokens, out.output_tokens) == (11, 7)


def test_a_refusal_or_provider_failure_becomes_llm_unavailable():
    with pytest.raises(LLMUnavailable, match="refused"):
        AnthropicLLM(model="m", client=SimpleNamespace(messages=_StubMessages(_stub(stop_reason="refusal")))).complete(
            agent="planner", system="s", payload={})
    with pytest.raises(LLMUnavailable, match="failed"):
        AnthropicLLM(model="m", client=SimpleNamespace(messages=_StubMessages(error=RuntimeError("500")))).complete(
            agent="planner", system="s", payload={})


def test_the_layer_is_off_unless_configured(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AGENT_LLM_PROVIDER", "")
    with pytest.raises(LLMUnavailable):
        build_default_llm()
    with pytest.raises(LLMUnavailable):
        UnavailableLLM().complete(agent="x", system="", payload={})


def test_fake_llm_fails_loudly_when_a_script_runs_out():
    llm = FakeLLM(script={"intake": ['{"a": 1}']})
    llm.complete(agent="intake", system="", payload={})
    with pytest.raises(AssertionError):
        llm.complete(agent="intake", system="", payload={})


# ---- extracted services -----------------------------------------------------------------------

def test_patient_search_service_requires_a_criterion_and_scopes_by_hospital(world):
    world.patient("Ravi Kumar")
    with world.db.cursor() as cur:
        with pytest.raises(ValueError):
            search_patients_service(cur, hospital_id=1)
        assert [p["name"] for p in search_patients_service(cur, q="ravi")] == ["Ravi Kumar"]
        assert search_patients_service(cur, q="ravi", hospital_id=1)[0]["uhid"].startswith("HOS-")
        assert search_patients_service(cur, q="ravi", hospital_id=99) == []
        assert get_patient_service(cur, 1, hospital_id=99) is None


def test_the_patient_routes_behave_as_before(client, world):
    ravi = world.patient("Ravi Kumar")
    found = client.get("/api/patients/search", params={"q": "Ravi"}, headers=world.admin_headers)
    assert found.status_code == 200 and [p["id"] for p in found.json()] == [ravi["id"]]
    assert client.get("/api/patients/search", headers=world.admin_headers).status_code == 400
    got = client.get(f"/api/patients/{ravi['id']}", headers=world.admin_headers).json()
    assert got["name"] == "Ravi Kumar" and "government_id" in got and "registered_at" in got
    assert client.get("/api/patients/99999", headers=world.admin_headers).status_code == 404


def test_appointment_listing_service_filters(world):
    ravi = world.patient("Ravi Kumar")
    a = world.appointment(ravi)
    world.appointment(world.patient("Meera Shah"), hour=11, minute=0)
    with world.db.cursor() as cur:
        assert list_appointments_service(cur)["total"] == 2
        assert [i["id"] for i in list_appointments_service(cur, appointment_id=a)["items"]] == [a]
        assert list_appointments_service(cur, appointment_id=a, hospital_id=99)["items"] == []
        assert list_appointments_service(cur, patient_id=ravi["id"], date_from=world.day, date_to=world.day)["total"] == 1
        assert list_appointments_service(cur, date_from=world.day.replace(year=world.day.year + 1))["total"] == 0


def test_queue_service_scopes_doctor_by_hospital(world):
    with world.db.cursor() as cur:
        assert get_doctor_queue_service(cur, world.seeded["doctor_id"])["waiting"] == []
        assert get_doctor_queue_service(cur, world.seeded["doctor_id"], hospital_id=99) is None
        assert get_doctor_queue_service(cur, 424242) is None


def test_queue_and_schedule_routes_behave_as_before(client, world):
    d = world.seeded["doctor_id"]
    assert client.get(f"/api/doctors/{d}/queue", headers=world.admin_headers).json()["waiting"] == []
    assert client.get("/api/doctors/424242/queue", headers=world.admin_headers).status_code == 404
    assert len(client.get(f"/api/doctors/{d}/schedule").json()) == 5
    assert client.get("/api/doctors/424242/schedule").status_code == 404


def test_staff_permission_resolver_matches_the_require_permission_dependency(client, world, db_connection):
    """require_permission now delegates to staff_permissions; the grants it resolves are unchanged."""
    acct = create_staff_for_test(db_connection, username="perm-check", password="perm-check-pw-1", role="RECEPTIONIST")
    with db_connection.cursor() as cur:
        perms = list_staff_permissions(cur, acct["id"])
        assert "appointment.check_in" in perms and "staff.manage" not in perms
        assert staff_has_permission(cur, acct["id"], "appointment.check_in")
        assert not staff_has_permission(cur, acct["id"], "staff.manage")
        # a live break-glass grant counts, exactly as in require_permission
        cur.execute("INSERT INTO break_glass_grants (staff_id, permission_name, reason, expires_at) "
                    "VALUES (%s, 'staff.manage', 'test', NOW() + INTERVAL '1 hour')", (acct["id"],))
        assert staff_has_permission(cur, acct["id"], "staff.manage")
        assert "staff.manage" in list_staff_permissions(cur, acct["id"])
    db_connection.commit()
