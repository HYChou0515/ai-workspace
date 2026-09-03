"""resolve_llm_chain — a preset ref → ordered failover endpoints (#196).

Exercises the config→endpoints resolution: primary only when there are no
fallbacks, primary + fallbacks in order, per-entry timeout overrides, the
role-level reasoning_effort propagating to every entry, and the off switch.
"""

from __future__ import annotations

import dataclasses

from workspace_app.config.loader import load
from workspace_app.config.schema import RetrievalLlmRef
from workspace_app.factories import resolve_llm_chain


def _settings(tmp_path, body: str):
    p = tmp_path / "config.yaml"
    p.write_text(body)
    return load(config_path=p, env={})


def test_no_ref_resolves_to_empty_chain(tmp_path):
    assert resolve_llm_chain(_settings(tmp_path, "agents: {}\n"), None) == []


def test_single_preset_resolves_to_one_entry_using_global_timeouts(tmp_path):
    settings = _settings(
        tmp_path,
        """
failover: { ttft_timeout_s: 8, cooldown_s: 30, idle_timeout_s: 120 }
agents:
  presets:
    solo: { model: "ollama_chat/qwen3:14b" }
""",
    )
    chain = resolve_llm_chain(settings, RetrievalLlmRef(preset="solo"))
    assert [e.model for e in chain] == ["ollama_chat/qwen3:14b"]
    assert (chain[0].ttft_s, chain[0].cooldown_s, chain[0].idle_s) == (8.0, 30.0, 120.0)


def test_fallbacks_resolve_in_priority_order(tmp_path):
    settings = _settings(
        tmp_path,
        """
agents:
  presets:
    primary: { model: "m-primary", fallbacks: [spare1, spare2] }
    spare1: { model: "m-spare1" }
    spare2: { model: "m-spare2" }
""",
    )
    chain = resolve_llm_chain(settings, RetrievalLlmRef(preset="primary"))
    assert [e.model for e in chain] == ["m-primary", "m-spare1", "m-spare2"]


def test_fallbacks_are_not_expanded_recursively(tmp_path):
    settings = _settings(
        tmp_path,
        """
agents:
  presets:
    primary: { model: "m-primary", fallbacks: [spare1] }
    spare1: { model: "m-spare1", fallbacks: [spare2] }
    spare2: { model: "m-spare2" }
""",
    )
    chain = resolve_llm_chain(settings, RetrievalLlmRef(preset="primary"))
    # spare1's OWN fallback (spare2) is ignored — the chain is [primary, spare1].
    assert [e.model for e in chain] == ["m-primary", "m-spare1"]


def test_per_preset_timeout_overrides_win_over_global(tmp_path):
    settings = _settings(
        tmp_path,
        """
failover: { ttft_timeout_s: 8 }
agents:
  presets:
    primary: { model: "m-fast", fallbacks: [slow] }
    slow: { model: "m-slow", ttft_timeout_s: 30 }
""",
    )
    chain = resolve_llm_chain(settings, RetrievalLlmRef(preset="primary"))
    assert chain[0].ttft_s == 8.0  # primary inherits the global default
    assert chain[1].ttft_s == 30.0  # the slow fallback's own override


def test_rate_limit_budget_resolves_like_the_other_chain_budgets(tmp_path):
    """`rate_limit_budget_s` is a chain-level knob and must flow the same way
    `total_deadline_s` does — global `failover:` default, overridable on the
    chain-HEAD preset — or it is a documented setting nothing reads (a dead
    knob). Entered through the real path: yaml → load → resolve_llm_chain."""
    settings = _settings(
        tmp_path,
        """
failover: { rate_limit_budget_s: 600 }
agents:
  presets:
    primary: { model: "m-primary", fallbacks: [spare], rate_limit_budget_s: 900 }
    spare: { model: "m-spare" }
""",
    )
    chain = resolve_llm_chain(settings, RetrievalLlmRef(preset="primary"))
    assert chain[0].rate_limit_budget_s == 900.0  # head override wins
    assert chain[1].rate_limit_budget_s == 600.0  # others carry the global

    plain = _settings(tmp_path, 'agents: { presets: { solo: { model: "m" } } }\n')
    solo = resolve_llm_chain(plain, RetrievalLlmRef(preset="solo"))
    assert solo[0].rate_limit_budget_s == 7200.0  # the shipped two-hour default


def test_reasoning_effort_propagates_to_every_entry(tmp_path):
    settings = _settings(
        tmp_path,
        """
agents:
  presets:
    primary: { model: "m-primary", fallbacks: [spare1] }
    spare1: { model: "m-spare1" }
""",
    )
    chain = resolve_llm_chain(settings, RetrievalLlmRef(preset="primary", reasoning_effort="none"))
    assert [e.reasoning_effort for e in chain] == ["none", "none"]


def test_cooldown_key_is_model_and_endpoint(tmp_path):
    settings = _settings(
        tmp_path,
        """
agents:
  presets:
    primary: { model: "m", llm: { base_url: "http://a:1" } }
""",
    )
    chain = resolve_llm_chain(settings, RetrievalLlmRef(preset="primary"))
    assert chain[0].cooldown_key == ("m", "http://a:1")


def test_inline_model_override_applies_to_primary_only(tmp_path):
    settings = _settings(
        tmp_path,
        """
agents:
  presets:
    primary: { model: "m-preset", fallbacks: [spare1] }
    spare1: { model: "m-spare1" }
""",
    )
    ref = dataclasses.replace(RetrievalLlmRef(preset="primary"), model="m-override")
    chain = resolve_llm_chain(settings, ref)
    assert [e.model for e in chain] == ["m-override", "m-spare1"]  # only primary overridden
