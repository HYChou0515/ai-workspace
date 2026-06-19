"""Characterization tests filling coverage gaps in ``config/catalog_build.py``.

Covers the empty-required-purpose guards, the non-KB-purpose skip branch, the
``_empty_required_message`` variants and the ``_default_name`` new-purpose
fallback — paths the behaviour suite (which only feeds well-formed configs)
doesn't reach.
"""

from __future__ import annotations

import dataclasses

import pytest

from workspace_app.config.catalog_build import (
    _default_name,
    _empty_required_message,
    build_catalog,
)
from workspace_app.config.schema import Settings


def _with_sub_agents(**sub_agents) -> Settings:
    s = Settings()
    return dataclasses.replace(s, agents=dataclasses.replace(s.agents, sub_agents=sub_agents))


def test_empty_kb_chat_raises_with_the_default_hint():
    """A required KB purpose with no entries fails the build loud (line 53 →
    `_empty_required_message`, lines 83-87)."""
    s = _with_sub_agents(kb_chat=[], infer_modules=[{"preset": "infer-modules-default"}])
    with pytest.raises(ValueError, match="agents.kb_chat is empty"):
        build_catalog(s, config_dir=None)


def test_empty_infer_modules_raises_with_its_own_hint():
    """The infer_modules branch of `_empty_required_message` (lines 89-93)."""
    s = _with_sub_agents(kb_chat=[{"preset": "kb-default"}], infer_modules=[])
    with pytest.raises(ValueError, match="agents.infer_modules is empty"):
        build_catalog(s, config_dir=None)


def test_non_kb_purpose_with_entries_skips_kb_search_validation():
    """A custom (non-KB-required) purpose builds its configs without the
    kb_search assertion (branch 63->65) — pointing at a preset that lacks
    kb_search entirely."""
    base = Settings()
    presets = dict(base.agents.presets)
    # A preset with a resolvable prompt_file but NO kb_search in allowed_tools.
    presets["plain"] = dataclasses.replace(
        presets["kb-default"],
        prompt_file="pkg:workspace_app.kb.prompts/system.md",
        allowed_tools=None,
    )
    s = dataclasses.replace(
        base,
        agents=dataclasses.replace(
            base.agents,
            presets=presets,
            sub_agents={
                "kb_chat": [{"preset": "kb-default"}],
                "infer_modules": [{"preset": "infer-modules-default"}],
                "my_purpose": [{"preset": "plain"}],
            },
        ),
    )
    catalog = build_catalog(s, config_dir=None)
    assert "my_purpose" in catalog.purposes()
    configs = catalog.configs_for("my_purpose")
    assert len(configs) == 1
    # default_name falls back to None → resolve_usage uses the preset name (line 107).
    assert configs[0].name == "plain"


def test_empty_required_message_for_an_arbitrary_purpose():
    """The generic fallback message (line 94) — reached when some other purpose
    name is required+empty (defensive: the only entries in
    `_KB_REQUIRED_PURPOSES` today are kb_chat / infer_modules, so call the
    helper directly)."""
    msg = _empty_required_message("qtime_pair_selector")
    assert msg == "agents.qtime_pair_selector is empty — at least one entry is required."


def test_default_name_returns_none_for_a_new_purpose():
    """A purpose with no default-name policy yields None (line 107)."""
    assert _default_name("qtime_pair_selector", 0) is None
    # Sanity: the bundled purposes still carry their picker labels.
    assert _default_name("kb_chat", 0) == "KB Agent"
    assert _default_name("infer_modules", 0) == "Infer Modules"
