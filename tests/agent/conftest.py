"""Fixtures for the agent-layer tests: a small hospital (one doctor, a few
patients, appointments), staff accounts per role with real AuthContexts,
and an Orchestrator wired to a scripted FakeLLM against the real test
database."""

from datetime import date, datetime, timedelta

import pytest

from app.agent.authz import build_auth_context
from app.agent.llm import FakeLLM
from app.agent.orchestrator import Orchestrator, OrchestratorConfig
from app.agent.tools.hospital import build_default_registry
from tests.helpers import create_admin_and_get_headers, create_staff_for_test, seed_basic_doctor


def _next_weekday() -> date:
    d = date.today() + timedelta(days=10)
    while d.isoweekday() not in (1, 2, 3, 4, 5):
        d += timedelta(days=1)
    return d


class World:
    def __init__(self, client, db):
        self.client = client
        self.db = db
        self.day = _next_weekday()
        self.admin_headers = create_admin_and_get_headers(db)
        self.seeded = seed_basic_doctor(
            client, db, doctor_name="Dr. Agent", department_name="Agent Dept", appointment_type_name="Agent Visit")
        self._phone = 30000000
        self._n = 0

    def patient(self, name: str, **extra) -> dict:
        self._phone += 1
        return self.client.post(
            "/api/patients", json={"name": name, "whatsapp_number": f"+9197{self._phone:08d}", **extra},
            headers=self.admin_headers).json()

    def appointment(self, patient: dict, *, hour=10, minute=30, confirm=True) -> int:
        start = f"{self.day.isoformat()}T{hour:02d}:{minute:02d}:00+05:30"
        created = self.client.post(
            "/api/appointments",
            json={"doctor_id": self.seeded["doctor_id"], "patient_id": patient["id"],
                  "appointment_type_id": self.seeded["appointment_type_id"], "start_at": start},
            headers=self.admin_headers)
        assert created.status_code == 200, created.text
        appt_id = created.json()["id"]
        if confirm:
            assert self.client.post(f"/api/appointments/{appt_id}/confirm", headers=self.admin_headers).status_code == 200
        return appt_id

    def status(self, appointment_id: int) -> str:
        with self.db.cursor() as cur:
            cur.execute("SELECT status FROM appointments WHERE id = %s", (appointment_id,))
            row = cur.fetchone()
        self.db.commit()
        return row[0]

    def ctx(self, role="RECEPTIONIST", *, patient_ids=None):
        n = self._n = self._n + 1
        account = create_staff_for_test(self.db, username=f"agent-{role.lower()}-{n}", password="agent-test-pw-1", role=role)
        staff = {"id": account["id"], "role": role, "hospital_id": 1}
        with self.db.cursor() as cur:
            ctx = build_auth_context(cur, staff, patient_ids=patient_ids, session_id="test-session")
        self.db.commit()
        return ctx

    def orchestrator(self, script: dict, *, registry=None, config=None, repeat_last=False):
        llm = FakeLLM(script=script, repeat_last=repeat_last)
        orch = Orchestrator(llm, registry or build_default_registry(), config=config or OrchestratorConfig(),
                            today_fn=lambda: self.day)
        return orch, llm

    def second_hospital(self) -> int:
        """A second tenant (hospital 2), removed again at teardown -- the
        hospitals table is reference data the per-test truncate leaves alone."""
        self.rows("INSERT INTO hospitals (code, name) VALUES ('OTHER', 'Other Hospital') ON CONFLICT DO NOTHING")
        return self.rows("SELECT id FROM hospitals WHERE code = 'OTHER'")[0][0]

    def rows(self, sql, params=()):
        with self.db.cursor() as cur:
            cur.execute(sql, params or None)
            out = cur.fetchall() if cur.description else []
        self.db.commit()
        return out


@pytest.fixture
def world(client, db_connection):
    w = World(client, db_connection)
    yield w
    db_connection.rollback()
    for table in ("patients", "appointments", "staff", "doctors", "departments", "appointment_types"):
        w.rows(f"UPDATE {table} SET hospital_id = 1 WHERE hospital_id <> 1")
    w.rows("DELETE FROM hospitals WHERE code = 'OTHER'")
