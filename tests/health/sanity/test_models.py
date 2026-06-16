"""get_sanity_models — the distinct text models the matrix probes."""

from __future__ import annotations

from workspace_app.config.schema import Settings
from workspace_app.factories import _resolve_llm_ref, get_sanity_models


def test_dedupes_sorts_and_covers_retrieval_but_not_vlm():
    s = Settings()
    models = get_sanity_models(s)

    assert models == sorted(set(models))  # sorted, no duplicates
    # the kb_search retrieval LLM (the reason this feature exists) IS covered
    retrieval = _resolve_llm_ref(s, s.kb.retrieval_llm)[0]
    assert retrieval in models
    # the vision model is NOT (it doesn't do text reasoning)
    vlm = _resolve_llm_ref(s, s.kb.vlm_llm)[0]
    assert vlm not in models


def test_get_sanity_llm_factory_builds_the_real_litellm_with_the_level():
    from workspace_app.factories import get_sanity_llm_factory
    from workspace_app.kb.llm import ILlm, LitellmLlm

    make = get_sanity_llm_factory(Settings())
    llm = make("ollama_chat/qwen3:14b", "none")
    # the SAME class kb_search uses — not a parallel runner
    assert isinstance(llm, ILlm) and isinstance(llm, LitellmLlm)
    assert llm.reasoning_effort == "none"  # the level is threaded into reasoning_effort
    assert (
        get_sanity_llm_factory(Settings())("ollama_chat/qwen3:14b", "low").reasoning_effort == "low"
    )


def test_includes_the_pickable_chat_fleet():
    s = Settings()
    models = get_sanity_models(s)
    # every workspace_chat / kb_chat entry's resolved model shows up
    for purpose in ("workspace_chat", "kb_chat"):
        for entry in s.agents.sub_agents.get(purpose, []):
            preset = s.agents.presets.get(str(entry.get("preset", "")))
            model = str(entry.get("model", "")) or (preset.model if preset else "")
            if model:
                assert model in models
