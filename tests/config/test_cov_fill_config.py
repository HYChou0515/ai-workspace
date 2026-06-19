"""Characterization tests filling coverage gaps in the config package.

These exercise specific defensive / edge branches in ``config/loader.py``,
``config/schema.py`` and ``config/prompt_file.py`` that the behaviour-focused
suites don't reach. Behaviour is asserted exactly as the code does it today
(characterization), not as a new contract.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from workspace_app.config import loader
from workspace_app.config.loader import load
from workspace_app.config.prompt_file import PromptFileNotFound, resolve_prompt_file
from workspace_app.config.schema import Settings, Suggestion, _to_suggestion

# ─── loader internal helpers (loader.py 209/211, 222, 252, 257-258/260/262) ──


def test_flatten_bundled_sub_agents_noop_when_agents_not_a_dict():
    """`_flatten_bundled_sub_agents` returns early (line 209) when `agents`
    isn't a dict — a defensive guard for a malformed bundled tree."""
    bundled: dict = {"agents": ["not", "a", "dict"]}
    loader._flatten_bundled_sub_agents(bundled)
    assert bundled == {"agents": ["not", "a", "dict"]}  # untouched


def test_flatten_bundled_sub_agents_without_sub_agents_key_is_inert():
    """When `agents` has no `sub_agents` key, `pop` yields None so the
    `isinstance(sub_agents, dict)` guard (211->exit) skips the update."""
    bundled: dict = {"agents": {"presets": {}}}
    loader._flatten_bundled_sub_agents(bundled)
    assert bundled == {"agents": {"presets": {}}}


def test_pack_merged_sub_agents_noop_when_agents_not_a_dict():
    """`_pack_merged_sub_agents` returns early (line 222) when `agents` isn't
    a dict."""
    merged: dict = {"agents": 123}
    loader._pack_merged_sub_agents(merged)
    assert merged == {"agents": 123}


def test_load_yaml_missing_file_returns_empty(tmp_path: Path):
    """`_load_yaml` on a non-file path returns {} (line 252)."""
    assert loader._load_yaml(tmp_path / "nope.yaml") == {}


def test_load_yaml_parse_error_raises_naming_the_path(tmp_path: Path):
    """A genuinely broken YAML document raises ValueError naming the path
    (lines 257-258)."""
    bad = tmp_path / "config.yaml"
    bad.write_text("server: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML parse error"):
        loader._load_yaml(bad)


def test_load_yaml_empty_document_returns_empty(tmp_path: Path):
    """A YAML file that parses to None (empty / comments only) → {} (line 260)."""
    empty = tmp_path / "config.yaml"
    empty.write_text("# only a comment\n", encoding="utf-8")
    assert loader._load_yaml(empty) == {}


def test_load_yaml_non_mapping_root_raises(tmp_path: Path):
    """A YAML root that isn't a mapping (here a list) raises (line 262)."""
    listy = tmp_path / "config.yaml"
    listy.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a mapping at the root"):
        loader._load_yaml(listy)


# ─── validation branches (loader.py 426, 438->433, 442, 449, 454, 462, 465-467) ──


def test_validate_skips_a_non_dict_preset_value():
    """`_check_agents_keys` skips a non-dict preset value (line 426 `continue`)
    and `_check_preset_required_fields` tolerates it too (line 509 `continue`):
    `_validate` doesn't raise on a malformed (non-dict) preset — construction
    would, but the strict-validation stage lets it through."""
    merged = {"agents": {"presets": {"weird": "not-a-mapping"}}}
    # No raise — both the unknown-key walk (426) and the required-field walk
    # (509) skip the non-dict preset.
    loader._validate(merged, source="<test>")


def test_usage_list_with_a_non_dict_entry_is_skipped(tmp_path: Path):
    """A list-form purpose whose entry is not a dict is skipped (line 442
    `continue`) instead of validated as a usage dict."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              kb_chat:
                - "just-a-string"
                - { preset: kb-default }
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    # The string entry is dropped by _normalize_usage_list; the dict survives.
    assert s.agents.sub_agents["kb_chat"] == [{"preset": "kb-default"}]


def test_unknown_field_on_a_preset_raises(tmp_path: Path):
    """`_check_preset_dict` raises on an unknown preset field (line 449)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              presets:
                p:
                  model: m
                  bogus_field: x
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bogus_field"):
        load(config_path=cfg, env={})


def test_unknown_llm_subfield_on_a_preset_raises(tmp_path: Path):
    """`_check_preset_dict` validates the preset's `llm` block too (line 454)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              presets:
                p:
                  model: m
                  llm:
                    token: sk-xxx
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="token"):
        load(config_path=cfg, env={})


def test_unknown_field_on_a_usage_entry_raises(tmp_path: Path):
    """`_check_usage_dict` raises on a field outside the usage allow-set
    (line 462)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              kb_chat:
                - preset: kb-default
                  not_a_usage_field: 1
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not_a_usage_field"):
        load(config_path=cfg, env={})


def test_unknown_llm_subfield_on_a_usage_entry_raises(tmp_path: Path):
    """`_check_usage_dict` validates the usage entry's `llm` block (lines
    465-467)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              kb_chat:
                - preset: kb-default
                  llm:
                    token: sk-xxx
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="token"):
        load(config_path=cfg, env={})


def test_usage_dict_valid_llm_subfield_passes_through_the_loop():
    """A usage entry whose `llm` block holds only valid PresetLlmSettings keys
    takes the loop's False branch and exits cleanly (branches 465->468, 466->465),
    no raise."""
    loader._check_usage_dict(
        {"llm": {"frequency_penalty": 0.3}}, prefix="agents.kb_chat[0]", source="<test>"
    )


def test_check_agents_keys_skips_a_purpose_that_is_neither_dict_nor_list():
    """A purpose whose value is neither a dict nor a list (here None) matches
    neither the dict nor the list branch — `_check_agents_keys` just loops on to
    the next purpose (branch 438->433), no raise."""
    node = {
        "presets": {},
        "weird_purpose": None,  # neither dict nor list → falls through (438->433)
        "kb_chat": [{"preset": "kb-default"}],
    }
    loader._check_agents_keys(node, prefix="agents", source="<test>")


def test_usage_dict_legacy_single_dict_form_is_validated(tmp_path: Path):
    """The legacy single-dict purpose form reaches `_check_usage_dict` via the
    `isinstance(entries_node, dict)` branch (line 437) — an unknown field there
    still raises."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              kb_chat:
                preset: kb-default
                nope: 1
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="nope"):
        load(config_path=cfg, env={})


def test_preset_missing_model_raises_naming_the_preset(tmp_path: Path):
    """`_check_preset_required_fields` raises when a preset omits `model`
    (line 511)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              presets:
                p:
                  description: no model here
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing required field 'model'"):
        load(config_path=cfg, env={})


def test_normalize_usage_list_drops_non_dict_entries_returns_empty():
    """`_normalize_usage_list` with a scalar returns [] (line 646)."""
    assert loader._normalize_usage_list("scalar") == []
    assert loader._normalize_usage_list(None) == []


# ─── schema._to_suggestion (schema.py 339, 347) ───────────────────────────


def test_to_suggestion_passes_through_an_existing_suggestion():
    """A `Suggestion` instance is returned as-is (schema.py line 339)."""
    s = Suggestion(label="L", prompt="P")
    assert _to_suggestion(s) is s


def test_to_suggestion_rejects_an_unsupported_type():
    """A value that is neither str / dict / Suggestion raises TypeError
    (schema.py line 347)."""
    with pytest.raises(TypeError, match="must be str | dict | Suggestion"):
        _to_suggestion(123)


def test_settings_still_constructs_with_no_args():
    """Sanity anchor — the default Settings tree is intact."""
    assert isinstance(Settings(), Settings)


# ─── prompt_file._read_pkg no-slash guard (prompt_file.py line 66) ─────────


def test_pkg_form_without_a_slash_raises_expected_shape():
    """A `pkg:` value with no `/` separator can't name a sub-path → raises with
    the expected-shape hint (line 66)."""
    with pytest.raises(PromptFileNotFound, match=r"pkg:<package>/<path/to.md>"):
        resolve_prompt_file("pkg:just_a_package", config_dir=Path("/nowhere"))
