"""
The single seam between the agent layer and a language model.

Agents talk to an `LLMClient`; production wires AnthropicLLM, tests wire
FakeLLM (scripted, hermetic -- no network, deterministic). The system
prompts are static strings (app/agent/prompts.py); everything that varies
per call travels in `payload`, which keeps the system prompt cache-stable.

What may go into `payload` is limited by construction: tool outputs are
already whitelisted/minimized by each tool's output schema
(app/agent/tools), and raw hospital rows never reach this module.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    def complete(self, *, agent: str, system: str, payload: dict) -> LLMResponse: ...


class LLMUnavailable(Exception):
    """No usable model is configured / the provider call failed."""


ScriptItem = "str | dict | Callable[[dict], str | dict] | Exception"


@dataclass
class FakeLLM:
    """Scripted model for tests. `script[agent]` is a list consumed one
    item per call (an item may be a JSON string, a dict, a callable taking
    the payload, or an Exception instance to raise). The last item repeats
    if the list runs out only when `repeat_last` is set; otherwise running
    out is a test bug and raises AssertionError."""

    script: dict[str, list] = field(default_factory=dict)
    repeat_last: bool = False
    calls: list[dict] = field(default_factory=list)

    def complete(self, *, agent: str, system: str, payload: dict) -> LLMResponse:
        self.calls.append({"agent": agent, "system": system, "payload": payload})
        queue = self.script.get(agent)
        if not queue:
            raise AssertionError(f"FakeLLM has no scripted response left for agent {agent!r}")
        item = queue[0] if (self.repeat_last and len(queue) == 1) else queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(payload)
        text = item if isinstance(item, str) else json.dumps(item)
        return LLMResponse(text=text, model="fake", input_tokens=len(json.dumps(payload)) // 4,
                           output_tokens=len(text) // 4)

    def calls_for(self, agent: str) -> list[dict]:
        return [c for c in self.calls if c["agent"] == agent]


class AnthropicLLM:
    """Real client (Claude API via the official SDK). Imported lazily so
    the app and the test suite run without the SDK or an API key.

    Thinking is left at the model default (always-on adaptive on Opus 5.5;
    the `thinking` and sampling parameters are deliberately not sent).
    Each agent's job is a short JSON transform, so effort is `low` by
    default -- raise via AGENT_LLM_EFFORT after measuring on real tasks.
    """

    def __init__(self, *, model: str, effort: str = "low", max_tokens: int = 4096, client: Any = None):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = client

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise LLMUnavailable("the 'anthropic' package is not installed") from exc
            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, *, agent: str, system: str, payload: dict) -> LLMResponse:
        client = self._get_client()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                output_config={"effort": self.effort},
                messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
            )
        except Exception as exc:  # SDK errors are provider-specific; one seam-level wrapper
            raise LLMUnavailable(f"model call failed for agent {agent!r}: {type(exc).__name__}") from exc

        if response.stop_reason == "refusal":
            raise LLMUnavailable(f"model refused for agent {agent!r}")

        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class UnavailableLLM:
    """Stand-in when no provider is configured: cancel/reject still work,
    anything that would need a model fails as LLMUnavailable."""

    def complete(self, *, agent: str, system: str, payload: dict) -> LLMResponse:
        raise LLMUnavailable("AGENT_LLM_PROVIDER is not configured")


def build_default_llm() -> LLMClient:
    """Production wiring, from app/config.py. Raises LLMUnavailable when the
    agent layer is not enabled -- the API turns that into a 503."""
    from app import config

    if config.AGENT_LLM_PROVIDER != "anthropic":
        raise LLMUnavailable("AGENT_LLM_PROVIDER is not configured")
    return AnthropicLLM(model=config.AGENT_LLM_MODEL, effort=config.AGENT_LLM_EFFORT)
