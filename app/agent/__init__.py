"""
AI Agent Automation Layer (docs/implementation/AI_AGENT_IMPLEMENTATION_AUDIT.md).

Sits ABOVE the existing HospitalOS services and is never a system of
record. The only path from a model to hospital data is:

    model -> registered Tool (app/agent/tools) -> existing app/services/* -> DB

Nothing under app/agent/ issues hospital SQL; app/agent/store.py touches
only the agent_* tables (tests/agent/test_no_direct_db_access.py enforces
this). Business rules (check-in, queue, encounters) stay in the existing
services; this package only decides *whether/when* to call them and
records what happened.
"""
