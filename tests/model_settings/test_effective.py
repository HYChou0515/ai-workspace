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


@pytest.mark.parametrize(
    "document",
    [
        # a value nothing re-reads after boot: it would be stored, shown as in
        # force, and do nothing until a restart — refused, pointing at config.yaml
        {"config": {"server": {"port": 1}}},
        {"config": {"kb": {"retrieval": {"context_chars": 9}}}},
        # a whole live parent replaced is the same as touching every key under it
        {"config": {"kb": "x"}},
        # null hides a preset (M12) and means nothing else: anywhere else it would
        # delete a key the loader needs — a value goes back to its default by
        # leaving the patch
        {"config": {"kb": {"wiki": {"llm": None}}}},
        # the infer_modules run knobs ride on a live list but are read at boot
        {
            "config": {
                "agents": {"infer_modules": [{"preset": "infer-modules-default", "parallelism": 2}]}
            }
        },
    ],
)
def test_a_value_read_only_at_boot_is_refused(config, document):
    found = problems(document, config_path=config, env={})

    assert any("config.yaml" in problem for problem in found), found


def test_every_model_choice_can_be_changed_here(config):
    document = {
        "config": {
            "llm": {"base_url": "http://gw:4000"},
            "agents": {
                "presets": {"openai-mini": {"model": "openai/gpt-4.1-mini"}},
                "subagent_models": [],
                "kb_chat": [{"preset": "openai-mini", "name": "KB · Mini"}],
            },
            "kb": {
                "retrieval_llm": {"preset": "openai-mini"},
                "card_drafter": {"preset": "openai-mini"},
            },
        }
    }

    assert problems(document, config_path=config, env={}) == []


def test_turning_a_role_on_is_refused(config):
    # Whether a role is on decided which features were wired at boot (M17): the
    # page changes which model a role uses, never whether it exists. (Turning
    # one off needs `null`, refused already — M19.)
    found = problems(
        {"config": {"health": {"judge_llm": {"preset": "openai-mini"}}}},
        config_path=config,
        env={},
    )

    assert any("health.judge_llm" in p and "config.yaml" in p for p in found), found


@pytest.mark.parametrize(
    ("document", "named"),
    [
        # resolved once at boot into the quota ceilings (quota/limits.py) and held
        ({"apps": {"playground": {"resources": {"cpu": "8"}}}}, "apps.playground.resources"),
        # the key that boot dict, and the directory, are found under
        ({"apps": {"playground": {"slug": "other"}}}, "apps.playground.slug"),
        # what boot refuses (validate_workflow_profiles): a workflow with no run.py
        (
            {
                "profiles": {
                    "playground": {
                        "multi": {"workflows": [{"id": "no-such-workflow", "title": "X"}]}
                    }
                }
            },
            "no-such-workflow",
        ),
    ],
)
def test_what_an_app_reads_only_at_boot_is_refused(config, document, named):
    found = problems(document, config_path=config, env={})

    assert any(named in problem for problem in found), found


SECRET_TEXT = {"config": {"agents": {"presets": {"kb-default": {"llm": {"api_key": "sk-typed"}}}}}}


def test_a_secret_typed_into_the_text_is_refused(config):
    # It would sit in the patch — in the diff, in history (M6).
    found = problems(SECRET_TEXT, config_path=config, env={})

    assert any("api_key" in p and "key" in p for p in found), found


def test_the_same_secret_set_as_a_key_is_fine(config):
    keys = {"config.agents.presets.kb-default.llm.api_key": "sk-key"}

    assert problems({}, config_path=config, env={}, keys=keys) == []
