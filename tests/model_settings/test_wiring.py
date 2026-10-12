"""docs/plan-model-settings.md P5 — through the app's own composition: an Apply
reaches what a request sees, with nothing restarted."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps import overlay
from workspace_app.apps.manifest import load_app_manifest
from workspace_app.config.loader import load
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.model_settings.consumers import live_agent_config_catalog
from workspace_app.model_settings.live import LiveSettings
from workspace_app.model_settings.store import SettingsStore
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def no_overlay():
    yield
    overlay.set_source(None)


@pytest.fixture
def wired(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text("server:\n  port: 9090\n", encoding="utf-8")
    boot = load(config_path=config, env={})
    clock = Clock()
    live = LiveSettings(None, config_path=config, env={}, boot=boot, clock=clock)
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        agent_config_catalog=live_agent_config_catalog(live),
        live=live,
    )
    return TestClient(app), SettingsStore(spec), clock


def test_an_apply_reaches_the_kb_chat_picker(wired):
    client, store, clock = wired
    first = client.get("/api/kb/agent").json()
    assert first[0]["model"] != "openai/kb-2", first

    store.publish(
        {"config": {"agents": {"presets": {"kb-default": {"model": "openai/kb-2"}}}}}, by="root"
    )
    clock.now += 2

    assert client.get("/api/kb/agent").json()[0]["model"] == "openai/kb-2"


def test_a_worker_job_after_an_apply_uses_the_published_model(tmp_path: Path):
    # P6: the worker composition follows the same published settings — the next
    # wiki job runs on the model the page set, with the worker not restarted.
    from workspace_app.live_value import now
    from workspace_app.worker.__main__ import build_bundle

    config = tmp_path / "config.yaml"
    config.write_text("server:\n  port: 9090\n", encoding="utf-8")
    boot = load(config_path=config, env={})
    clock = Clock()
    live = LiveSettings(None, config_path=config, env={}, boot=boot, clock=clock)
    spec = make_spec(default_user="u")
    bundle = build_bundle(boot, spec, live=live)
    assert now(bundle.wiki._agent_config_of).model != "openai/wiki-2"

    SettingsStore(spec).publish(
        {"config": {"agents": {"presets": {"wiki-default": {"model": "openai/wiki-2"}}}}},
        by="root",
    )
    clock.now += 2

    assert now(bundle.wiki._agent_config_of).model == "openai/wiki-2"


def test_skill_eval_measures_the_published_turn(tmp_path: Path):
    # P6 / M13: a one-shot tool resolves the turn production would run — the
    # published preset and the published picker both.
    from workspace_app.apps.catalog import AppCatalog
    from workspace_app.model_settings.consumers import published_settings
    from workspace_app.model_settings.store import register_settings_models

    config = tmp_path / "config.yaml"
    config.write_text("server:\n  port: 9090\n", encoding="utf-8")
    spec = make_spec()
    register_settings_models(spec)
    SettingsStore(spec).publish(
        {
            "config": {"agents": {"presets": {"new-one": {"model": "openai/new-1"}}}},
            "apps": {"playground": {"agent": {"picker": [{"preset": "new-one", "name": "N"}]}}},
        },
        by="root",
    )

    settings = published_settings(
        load(config_path=config, env={}), config_path=config, env={}, spec=spec
    )

    turn = AppCatalog(presets=settings.agents.presets).resolve(
        app_slug="playground", profile="default"
    )
    assert turn.model == "openai/new-1"


def test_an_apply_reaches_every_app_json_reader(wired):
    _, store, clock = wired

    store.publish(
        {"apps": {"playground": {"agent": {"picker": [{"preset": "kb-default", "name": "K"}]}}}},
        by="root",
    )
    clock.now += 2

    assert [p.name for p in load_app_manifest("playground").agent.picker] == ["K"]
