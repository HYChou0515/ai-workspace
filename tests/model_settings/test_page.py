"""docs/plan-model-settings.md P7 — what the page shows and what it lets through."""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.config.loader import load
from workspace_app.model_settings.page import (
    SET,
    UNSET,
    defaults_document,
    key_path_problem,
    try_connection,
)


@pytest.fixture
def settings(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "agents:\n"
        "  presets:\n"
        "    openai-mini:\n"
        "      llm:\n"
        "        api_key: sk-secret-1234\n"
        "      env:\n"
        "        HF_TOKEN: hf-abc\n",
        encoding="utf-8",
    )
    return load(config_path=config, env={})


def test_the_defaults_are_shown_as_config_yaml_spells_them(settings):
    config = defaults_document(settings)["config"]

    # the usage lists sit under agents.<purpose>, as the patch names them
    assert "kb_chat" in config["agents"] and "sub_agents" not in config["agents"]
    assert config["kb"]["retrieval_llm"]["preset"]
    # nothing the page may not change (M18)
    assert "server" not in config and "retrieval" not in config["kb"]


def test_a_secret_default_is_shown_set_or_unset_and_never_its_value(settings):
    document = defaults_document(settings)
    preset = document["config"]["agents"]["presets"]["openai-mini"]

    assert preset["llm"]["api_key"] == SET
    assert preset["env"]["HF_TOKEN"] == SET
    assert document["config"]["agents"]["presets"]["kb-default"]["llm"]["api_key"] == UNSET
    assert "sk-secret-1234" not in repr(document) and "hf-abc" not in repr(document)


def test_every_app_and_profile_is_shown_as_shipped(settings):
    document = defaults_document(settings)

    assert document["apps"]["playground"]["slug"] == "playground"
    assert "default" in document["profiles"]["playground"]


@pytest.mark.parametrize(
    ("path", "fine"),
    [
        ("config.agents.presets.openai-mini.llm.api_key", True),
        ("config.agents.presets.openai-mini.env.HF_TOKEN", True),
        ("config.llm.api_key", True),
        # not a secret: it goes in the text, where history and diff can show it
        ("config.agents.presets.openai-mini.model", False),
        # a secret the page does not edit (M18)
        ("config.storage.pg_dsn", False),
        ("apps.playground.api_key", False),
        # review r2: secret-NAMED but no such text setting — the loader's refusal
        # of the shape quoted the value back in every check and save response
        ("config.agents.subagent_models.api_key", False),
        ("config.agents.kb_chat.0.api_key", False),
        ("config.agents.presets.api_key", False),
        ("config.agents.foo_token", False),
        ("config.llm.x.api_key", False),
        # role references are text settings
        ("config.kb.retrieval_llm.llm.api_key", True),
        ("config.agents.presets.qwen2.5.env.HF_TOKEN", True),
    ],
)
def test_only_a_live_secret_takes_a_key(path, fine):
    assert (key_path_problem(path) is None) is fine


def test_a_connection_test_reports_the_reply(settings, monkeypatch):
    from workspace_app.kb.llm import LitellmLlm

    monkeypatch.setattr(LitellmLlm, "stream", lambda self, prompt: iter([(" OK ", False)]))

    result = try_connection(settings, "openai-mini")

    assert result.ok and result.reply == "OK" and result.model


def test_a_failed_connection_says_why_without_the_key(settings, monkeypatch):
    from workspace_app.kb.llm import LitellmLlm

    def refuse(self, prompt):
        raise RuntimeError(f"401: bad key {self._api_key}")

    monkeypatch.setattr(LitellmLlm, "stream", refuse)

    result = try_connection(settings, "openai-mini")

    assert not result.ok and "401" in result.error
    assert "sk-secret-1234" not in result.error


def test_an_unknown_preset_is_named(settings):
    assert "nope" in try_connection(settings, "nope").error


def test_a_connection_test_does_not_send_a_deploy_key_to_a_new_endpoint(settings, monkeypatch):
    # final review: the test call sent the deploy's key to the draft's base_url.
    from dataclasses import replace

    from workspace_app.kb.llm import LitellmLlm

    calls = []
    monkeypatch.setattr(LitellmLlm, "stream", lambda self, prompt: calls.append(1) or iter([]))
    preset = settings.agents.presets["openai-mini"]
    moved = replace(preset, llm=replace(preset.llm, base_url="http://mine.example"))
    drafted = replace(
        settings,
        agents=replace(settings.agents, presets={**settings.agents.presets, "openai-mini": moved}),
    )

    result = try_connection(drafted, "openai-mini", deploy=settings, page_keys=set())

    assert not result.ok and "key" in result.error and calls == []
