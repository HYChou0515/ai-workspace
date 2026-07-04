"""#435 P6 live canned check — the create_entity cross-origin match (M1-AI, 决议8) must
actually work against a real small model, not just the fake-LLM plumbing. A fake proves
``match_prompt`` → ``collect`` → ``parse_match`` is wired; only a live model proves the
prompt elicits the RIGHT verdict: it merges an obvious duplicate and refuses an obvious
non-match (fail-open → NEW). Marked integration: full local suite against Ollama, not CI.

"Replay" shape — one prompt → one ``collect`` call, no store, no run engine.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from workspace_app.kb.llm import LitellmLlm
from workspace_app.workflow.entity_dedup import match_prompt, parse_match

pytestmark = pytest.mark.integration

_MODEL = "ollama_chat/qwen3:14b"
_BASE = "http://localhost:11434"

# A human already filed these two entities from another origin; the workflow is about to
# create one more and asks the model whether it is a duplicate of an existing candidate.
_CANDIDATES: list[dict[str, Any]] = [
    {"number": 3, "title": "Reflow oven zone-4 temperature drift"},
    {"number": 7, "title": "SMT feeder jam on line B"},
]
_CANDIDATE_NUMBERS: list[int] = [c["number"] for c in _CANDIDATES]


def _model_available() -> bool:
    try:
        tags = httpx.get(f"{_BASE}/api/tags", timeout=3).json()
    except Exception:
        return False
    return any(m.get("name") == "qwen3:14b" for m in tags.get("models", []))


def _decide(new_args: dict[str, str]) -> int | None:
    """The M1-AI decide leaf, end to end over a real model: build the classification
    prompt, ``collect`` the model's answer (reasoning=none so no <think> leaks into the
    verdict token), and run it through the fail-open ``parse_match`` guard."""
    llm = LitellmLlm(_MODEL, base_url=_BASE, reasoning_effort="none", timeout=120)
    answer = llm.collect(match_prompt(new_args, _CANDIDATES))
    return parse_match(answer, _CANDIDATE_NUMBERS)


@pytest.mark.skipif(not _model_available(), reason="qwen3:14b not pulled in local Ollama")
def test_live_model_merges_an_obvious_duplicate():
    # Same real defect as candidate #3, phrased differently → the model should pick 3.
    assert _decide({"title": "Zone 4 of the reflow oven is running hot"}) == 3


@pytest.mark.skipif(not _model_available(), reason="qwen3:14b not pulled in local Ollama")
def test_live_model_refuses_an_obvious_non_match():
    # A brand-new, unrelated defect → the model answers NEW (or anything non-matching),
    # and parse_match fail-opens to None so the workflow mints a fresh entity.
    assert _decide({"title": "Solder paste stencil misaligned on line C"}) is None
