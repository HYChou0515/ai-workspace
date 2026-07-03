"""`build_catalog(settings, config_dir)` — resolves usage entries against
presets and produces the runtime AgentConfigCatalog.

For each `kb_chat` / `infer_modules` entry: look up its preset → merge usage
overrides on top → read the prompt_file → emit a typed AgentConfig. The
catalog exposes the purpose-keyed accessors. (#89 P8: the old workspace_chat
picker / list / get / default / resolve were removed — per-App workspace
agents resolve through AppCatalog.)

These tests focus on resolution behaviour; the catalog accessors are
unit-tested in tests/agent/test_config_catalog.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.config.catalog_build import build_catalog
from workspace_app.config.loader import load


def test_each_resolved_kb_config_carries_its_preset_prompt_text():
    """The catalog reads `prompt_file` and stores the literal prompt
    text on `AgentConfig.system_prompt` — so the runner sees the
    resolved body, not the file ref."""
    settings = load(config_path=None, env={})
    cat = build_catalog(settings, config_dir=None)
    for cfg in cat.kb_chats():
        # length > 50 is the same sanity check used elsewhere.
        assert len(cfg.system_prompt) > 50


def test_kb_chat_resolves_to_a_single_typed_agent_config_with_kb_tools():
    settings = load(config_path=None, env={})
    cat = build_catalog(settings, config_dir=None)
    kb = cat.kb_chat()
    # kb-default preset ships kb_search as its allowed tool — the
    # resolved AgentConfig carries it.
    assert "kb_search" in kb.allowed_tools  # ty: ignore[unresolved-attribute, unsupported-operator]
    # The KB prompt is the knowledge-base body (not a file ref).
    assert "knowledge base" in kb.system_prompt.lower()  # ty: ignore[unresolved-attribute]


def test_kb_chat_missing_kb_search_in_allowed_tools_raises_at_catalog_build(tmp_path: Path):
    """The exact misconfig that motivated this validator: a deploy
    points kb_chat at a preset that doesn't ship `kb_search`. Before
    the fix, the KB sub-agent silently launched with the workspace
    toolset (no kb_search) and answered "I can't access the KB" in
    natural language — invisible to the operator until they tried.
    Now the catalog refuses to build."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "agents:\n"
        "  presets:\n"
        "    my-rca:\n"
        '      model: "openai/gpt-4o-mini"\n'
        '      prompt_file: "pkg:workspace_app.kb.prompts/system.md"\n'
        "  kb_chat:\n"
        "    preset: my-rca\n",
        encoding="utf-8",
    )
    settings = load(config_path=cfg_file, env={})
    with pytest.raises(ValueError, match="kb_search"):
        build_catalog(settings, config_dir=tmp_path)


def test_kb_chat_with_explicit_empty_allowed_tools_also_raises(tmp_path: Path):
    """Operator explicitly sets `allowed_tools: []` on kb_chat → still
    a broken config, same loud error."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "agents:\n  kb_chat:\n    preset: kb-default\n    allowed_tools: []\n",
        encoding="utf-8",
    )
    settings = load(config_path=cfg_file, env={})
    with pytest.raises(ValueError, match="kb_search"):
        build_catalog(settings, config_dir=tmp_path)


def test_kb_chat_with_kb_search_added_to_usage_overrides_passes(tmp_path: Path):
    """Operator wants to use the OpenAI preset for kb_chat but
    correctly adds `allowed_tools: [kb_search]` at the usage site —
    valid, build succeeds, KB chat works."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "agents:\n"
        "  presets:\n"
        "    my-rca:\n"
        '      model: "openai/gpt-4o-mini"\n'
        '      prompt_file: "pkg:workspace_app.kb.prompts/system.md"\n'
        "  kb_chat:\n"
        "    preset: my-rca\n"
        "    allowed_tools: [kb_search]\n",
        encoding="utf-8",
    )
    settings = load(config_path=cfg_file, env={})
    cat = build_catalog(settings, config_dir=tmp_path)
    assert "kb_search" in cat.kb_chat().allowed_tools  # ty: ignore


def test_bundled_kb_chat_has_the_expected_kb_prompt_invariants():
    """Locks in the contract previously asserted by the old
    `default_kb_agent_config` test: the kb_chat config must carry
    a knowledge-base prompt with the `[n]` citation convention,
    `kb_search` plus the `lookup_glossary` (#106) context-card tool and the
    `request_wiki_update` (#397) correction tool, and non-empty quick-prompt
    suggestions."""
    settings = load(config_path=None, env={})
    cat = build_catalog(settings, config_dir=None)
    kb = cat.kb_chat()
    assert kb.allowed_tools == [  # ty: ignore[unresolved-attribute]
        "kb_search",
        "lookup_glossary",
        "request_wiki_update",
    ]
    assert "knowledge base" in kb.system_prompt.lower()  # ty: ignore[unresolved-attribute]
    assert "[n]" in kb.system_prompt  # ty: ignore[unresolved-attribute]
    assert kb.suggestions  # quick-prompt chips ship with the config  # ty: ignore


def test_preset_llm_sampling_penalties_flow_to_agent_config(tmp_path: Path):
    """#113 Layer 1: anti-repetition sampling penalties set under a preset's
    `llm:` block reach the resolved AgentConfig (and thence the runner's
    ModelSettings)."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "agents:\n"
        "  presets:\n"
        "    my-rca:\n"
        '      model: "openai/gpt-4o-mini"\n'
        '      prompt_file: "pkg:workspace_app.kb.prompts/system.md"\n'
        "      llm:\n"
        "        frequency_penalty: 0.3\n"
        "        presence_penalty: 0.2\n"
        "        repetition_penalty: 1.1\n"
        "  kb_chat:\n"
        "    preset: my-rca\n"
        "    allowed_tools: [kb_search]\n",
        encoding="utf-8",
    )
    settings = load(config_path=cfg_file, env={})
    kb = build_catalog(settings, config_dir=tmp_path).kb_chat()
    assert kb.frequency_penalty == 0.3  # ty: ignore[unresolved-attribute]
    assert kb.presence_penalty == 0.2  # ty: ignore[unresolved-attribute]
    assert kb.repetition_penalty == 1.1  # ty: ignore[unresolved-attribute]


def test_preset_llm_sampling_knobs_flow_to_agent_config(tmp_path: Path):
    """#107 request hygiene: temperature / top_p / max_tokens set under a
    preset's `llm:` block reach the resolved AgentConfig. Omitted knobs stay
    None (= param not sent; the server-side model default wins)."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "agents:\n"
        "  presets:\n"
        "    my-rca:\n"
        '      model: "openai/glm-5.1"\n'
        '      prompt_file: "pkg:workspace_app.kb.prompts/system.md"\n'
        "      llm:\n"
        "        temperature: 0.55\n"
        "        top_p: 1.0\n"
        "        max_tokens: 32000\n"
        "  kb_chat:\n"
        "    preset: my-rca\n"
        "    allowed_tools: [kb_search]\n",
        encoding="utf-8",
    )
    settings = load(config_path=cfg_file, env={})
    kb = build_catalog(settings, config_dir=tmp_path).kb_chat()
    assert kb.temperature == 0.55  # ty: ignore[unresolved-attribute]
    assert kb.top_p == 1.0  # ty: ignore[unresolved-attribute]
    assert kb.max_tokens == 32000  # ty: ignore[unresolved-attribute]
    assert kb.frequency_penalty is None  # ty: ignore[unresolved-attribute]


def test_usage_entry_overrides_take_precedence_over_preset(tmp_path: Path):
    """A usage entry that overrides allowed_tools wins over its preset's
    value (list-replace semantics from Q5). Tested on kb_chat — the override
    keeps kb_search (required) and adds exec, replacing the preset's list."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "agents:\n"
        "  kb_chat:\n"
        '    - { preset: "kb-default", name: "Restricted",\n'
        "        allowed_tools: [kb_search, exec] }\n",
        encoding="utf-8",
    )
    settings = load(config_path=cfg_file, env={})
    cat = build_catalog(settings, config_dir=tmp_path)
    assert len(cat.kb_chats()) == 1
    assert cat.kb_chats()[0].allowed_tools == ["kb_search", "exec"]


def test_resolved_config_carries_per_preset_llm_endpoint(tmp_path: Path):
    """A preset declares `llm.base_url` / `llm.api_key`; the resolved
    AgentConfig surfaces them so LitellmAgentRunner uses the right
    endpoint per turn."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "agents:\n"
        "  presets:\n"
        "    my-kb:\n"
        '      model: "ollama_chat/qwen3:14b"\n'
        '      prompt_file: "pkg:workspace_app.kb.prompts/system.md"\n'
        "      allowed_tools: [kb_search]\n"
        "      llm:\n"
        '        base_url: "https://my-ollama:11434"\n'
        '        api_key: "secret"\n'
        "  kb_chat:\n"
        "    preset: my-kb\n",
        encoding="utf-8",
    )
    settings = load(config_path=cfg_file, env={})
    cat = build_catalog(settings, config_dir=tmp_path)
    cfg = cat.kb_chat()
    assert cfg.llm_base_url == "https://my-ollama:11434"  # ty: ignore[unresolved-attribute]
    assert cfg.llm_api_key == "secret"  # ty: ignore[unresolved-attribute]


def test_description_flows_from_preset_to_resolved_config(tmp_path: Path):
    """The model picker renders each entry's `description`: operators write it
    on the preset; bundled defaults ship one so the picker is never blank-noted
    on first run."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
agents:
  presets:
    fast:
      model: ollama_chat/qwen3:8b
      prompt_file: pkg:workspace_app.kb.prompts/system.md
      allowed_tools: [kb_search]
      description: Quick lookups and summaries.
  kb_chat:
    - preset: fast
      name: fast-lane
""",
        encoding="utf-8",
    )
    settings = load(config_path=cfg)
    catalog = build_catalog(settings, config_dir=tmp_path)
    [cfg_fast] = [c for c in catalog.kb_chats() if c.name == "fast-lane"]
    assert cfg_fast.description == "Quick lookups and summaries."
    # Bundled defaults: every kb-chat entry carries a short blurb.
    bundled = build_catalog(load(config_path=None), config_dir=None)
    assert all(c.description for c in bundled.kb_chats())


def test_usage_suggestions_accept_plain_strings_back_compat(tmp_path: Path):
    """Back-compat (#96): a config usage may list suggestions as bare strings
    (the old shorthand) instead of `{label, prompt}` maps. List-replace
    semantics hand those raw strings to resolve_usage, which must read a string
    as `label == prompt` rather than crash on `s["label"]`."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
agents:
  presets:
    p:
      model: ollama_chat/qwen3:8b
      prompt_file: pkg:workspace_app.kb.prompts/system.md
      allowed_tools: [kb_search]
  kb_chat:
    - preset: p
      name: shorthand
      suggestions: ["Run SPC", "Draft the report"]
""",
        encoding="utf-8",
    )
    settings = load(config_path=cfg)
    cat = build_catalog(settings, config_dir=tmp_path)
    [c] = [c for c in cat.kb_chats() if c.name == "shorthand"]
    assert [s.label for s in c.suggestions] == ["Run SPC", "Draft the report"]
    assert [s.prompt for s in c.suggestions] == ["Run SPC", "Draft the report"]


def test_catalog_build_carries_suggestions_as_structured_objects():
    """``Preset.suggestions`` → ``AgentConfig.suggestions`` keeps the
    structured shape across the dataclass→msgspec.Struct hop. Bundled
    string-form presets surface with ``label == prompt`` (#91).
    """
    from workspace_app.resources.agent_config import Suggestion

    settings = load(config_path=None, env={})
    cat = build_catalog(settings, config_dir=None)
    kb = cat.kb_chat()
    assert kb.suggestions, "expected the bundled kb-chat suggestions to flow through"  # ty: ignore
    for s in kb.suggestions:  # ty: ignore[unresolved-attribute]
        assert isinstance(s, Suggestion), (
            f"suggestion should be a Suggestion struct after catalog build; got {type(s).__name__}"
        )
        assert s.label and s.prompt
