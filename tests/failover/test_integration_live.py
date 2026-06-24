"""Live-ish failover smoke test (#196) — real litellm, no model server.

The fake-LLM unit tests prove the policy; this proves the *integration*: a real
``litellm.completion`` against a dead endpoint raises before the first token, and
our FallbackLlm catches that as a pre-first failure and advances down the chain
(here both endpoints are dead, so it tries both then raises AllProvidersFailed —
which is exactly the switch path, exercised end-to-end through litellm).

Marked integration (does real litellm calls); runs in the full local suite, not CI.
"""

from __future__ import annotations

import pytest

from workspace_app.factories import LlmEndpoint
from workspace_app.failover.cooldown import CooldownRegistry
from workspace_app.failover.core import AllProvidersFailed
from workspace_app.failover.llm import FallbackLlm
from workspace_app.kb.llm import ILlm, LitellmLlm

pytestmark = pytest.mark.integration


def _dead(port: int) -> LlmEndpoint:
    return LlmEndpoint(
        model="ollama_chat/qwen3:14b",
        base_url=f"http://127.0.0.1:{port}",
        api_key=None,
        reasoning_effort=None,
        ttft_s=5.0,
        idle_s=5.0,
        cooldown_s=30.0,
    )


def _make(e: LlmEndpoint) -> ILlm:
    return LitellmLlm(e.model, base_url=e.base_url, api_key=e.api_key, timeout=2.0, num_retries=0)


def test_real_connection_error_drives_the_failover_chain():
    reg = CooldownRegistry(clock=lambda: 0.0)
    tried: list[str] = []
    llm = FallbackLlm(
        [_dead(9), _dead(1)],
        reg,
        make_llm=_make,
        on_switch=lambda label, exc: tried.append(label),
    )
    with pytest.raises(AllProvidersFailed):
        llm.collect("ping")
    # Both dead endpoints were attempted in order before giving up — the real
    # litellm connection error surfaced pre-first-token and drove the switch.
    assert tried == ["ollama_chat/qwen3:14b", "ollama_chat/qwen3:14b"]
    assert reg.is_cooling(("ollama_chat/qwen3:14b", "http://127.0.0.1:9")) is True
