"""docs/plan-model-settings.md P3 — the settings everything runs with: the deploy
defaults with the page's patch and keys over them, and the checks Apply runs.

The document the page edits has three parts: `config` (the config.yaml tree),
`apps` (each App's app.json, by slug) and `profiles` (each profile's
_profile.json, by slug then name).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.model_settings.effective import (
    effective_app,
    effective_profile,
    effective_settings,
    problems,
    with_keys,
)


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  port: 9090\n", encoding="utf-8")
    return path


def test_a_key_is_written_into_its_setting():
    patch = {"config": {"agents": {"presets": {"fast": {"model": "m"}}}}}

    out = with_keys(patch, {"config.agents.presets.fast.llm.api_key": "sk-1"})

    assert out["config"]["agents"]["presets"]["fast"] == {"model": "m", "llm": {"api_key": "sk-1"}}
    assert "llm" not in patch["config"]["agents"]["presets"]["fast"]  # input untouched


def test_the_config_part_and_its_keys_reach_the_settings(config):
    document = with_keys(
        {"config": {"agents": {"presets": {"openai-mini": {"model": "openai/gpt-4.1-mini"}}}}},
        {"config.agents.presets.openai-mini.llm.api_key": "sk-1"},
    )

    settings = effective_settings(document, config_path=config, env={})

    preset = settings.agents.presets["openai-mini"]
    assert (preset.model, preset.llm.api_key) == ("openai/gpt-4.1-mini", "sk-1")


def test_an_apps_patch_changes_its_manifest():
    document = {
        "apps": {"playground": {"agent": {"picker": [{"preset": "openai-mini", "name": "Mini"}]}}}
    }

    manifest = effective_app("playground", document)

    assert [(p.preset, p.name) for p in manifest.agent.picker] == [("openai-mini", "Mini")]
    assert manifest.slug == "playground"  # everything not patched is the shipped app.json


def test_a_profiles_patch_changes_it():
    document = {"profiles": {"playground": {"default": {"default_preset": "qwen3-local"}}}}

    assert effective_profile("playground", "default", document).default_preset == "qwen3-local"


def test_a_sound_document_has_no_problems(config):
    assert problems({}, config_path=config, env={}) == []


@pytest.mark.parametrize(
    ("document", "named"),
    [
        # a picker offering a preset that does not exist — today found only
        # when a turn resolves it
        (
            {"apps": {"playground": {"agent": {"picker": [{"preset": "nope", "name": "N"}]}}}},
            "nope",
        ),
        # a profile defaulting to a preset that does not exist
        ({"profiles": {"playground": {"default": {"default_preset": "gone"}}}}, "gone"),
        # an app.json value of the wrong type
        ({"apps": {"playground": {"agent": {"picker": "not-a-list"}}}}, "picker"),
        # what a boot refuses
        ({"config": {"agents": {"presets": {"openai-mini": {"fallbacks": ["x9"]}}}}}, "x9"),
    ],
)
def test_what_cannot_run_is_named(config, document, named):
    found = problems(document, config_path=config, env={})

    assert any(named in problem for problem in found), found
