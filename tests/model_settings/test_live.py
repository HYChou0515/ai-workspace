"""docs/plan-model-settings.md P4 — what a process runs with, read when used.

Each use compares one small string (the published version) and reloads only when
it changed (M4); the string itself is read at most once a second per process,
so a request that reads the settings ten times does not ask the database ten
times. A failed read or a failed build keeps the last good settings (M14).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.config.loader import load
from workspace_app.model_settings.live import LiveSettings
from workspace_app.model_settings.store import SettingsStore, register_settings_models
from workspace_app.resources import make_spec


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  port: 9090\n", encoding="utf-8")
    return path


@pytest.fixture
def store() -> SettingsStore:
    spec = make_spec()
    register_settings_models(spec)
    return SettingsStore(spec)


def _live(store, config, clock, **kw):
    boot = load(config_path=config, env={})
    return LiveSettings(store, config_path=config, env={}, boot=boot, clock=clock, **kw)


MINI = {"config": {"agents": {"presets": {"openai-mini": {"model": "openai/gpt-4.1-mini"}}}}}


def test_with_nothing_published_it_runs_with_the_deploy_defaults(store, config):
    live = _live(store, config, Clock())

    assert live.settings().agents.presets["openai-mini"].model != "openai/gpt-4.1-mini"
    assert live.version() is None


def test_an_apply_is_picked_up_once_the_version_is_read_again(store, config):
    clock = Clock()
    live = _live(store, config, clock)
    live.settings()

    store.publish(MINI, by="root")
    assert (
        live.settings().agents.presets["openai-mini"].model != "openai/gpt-4.1-mini"
    )  # within 1 s

    clock.now += 1.5
    assert live.settings().agents.presets["openai-mini"].model == "openai/gpt-4.1-mini"


def test_an_unchanged_version_does_not_rebuild(store, config):
    clock, builds = Clock(), []
    live = _live(store, config, clock, on_build=builds.append)
    store.publish(MINI, by="root")

    for _ in range(5):
        clock.now += 2
        live.settings()

    assert len(builds) == 1


def test_a_key_set_and_applied_reaches_the_settings(store, config):
    clock = Clock()
    live = _live(store, config, clock)
    store.set_key("config.agents.presets.openai-mini.llm.api_key", "sk-9", by="root")
    store.publish(MINI, by="root")

    clock.now += 2
    assert live.settings().agents.presets["openai-mini"].llm.api_key == "sk-9"


def test_a_failed_read_keeps_the_last_good_settings(store, config, monkeypatch):
    clock = Clock()
    live = _live(store, config, clock)
    store.publish(MINI, by="root")
    clock.now += 2
    good = live.settings()

    def broken():
        raise ConnectionError("database away")

    monkeypatch.setattr(store, "current_version", broken)
    clock.now += 2

    assert live.settings() is good


def test_a_published_version_that_cannot_build_keeps_the_last_good(store, config):
    clock = Clock()
    live = _live(store, config, clock)
    store.publish(MINI, by="root")
    clock.now += 2
    good = live.settings()

    # Apply refuses this (P3); a key cleared afterwards, or a hand edit, could
    # still leave the published document broken — the process must not fall over.
    store.publish(
        {"config": {"agents": {"presets": {"openai-mini": {"fallbacks": ["x9"]}}}}}, by="root"
    )
    clock.now += 2

    assert live.settings() is good


def test_without_a_store_it_runs_with_the_boot_settings_until_one_is_attached(store, config):
    # The store's models are registered after `spec.apply`, inside create_app —
    # later than the composition that hands `live` to every consumer.
    clock = Clock()
    boot = load(config_path=config, env={})
    live = LiveSettings(None, config_path=config, env={}, boot=boot, clock=clock)
    store.publish(MINI, by="root")
    assert live.settings() is boot

    live.attach(store)
    assert live.settings().agents.presets["openai-mini"].model == "openai/gpt-4.1-mini"


def test_polling_keeps_the_database_off_the_request_path(store, config, monkeypatch):
    # P10: one version read is ~17 ms on Postgres. A request that crossed the
    # one-second window paid it, synchronously, on the event loop; with the poll
    # running, a use only ever reads memory and an Apply still lands within it.
    import threading
    import time

    reads: list[str] = []
    real = store.current_version

    def counted():
        reads.append(threading.current_thread().name)
        return real()

    monkeypatch.setattr(store, "current_version", counted)
    boot = load(config_path=config, env={})
    live = LiveSettings(store, config_path=config, env={}, boot=boot)
    stop = live.start(interval_s=0.01)
    try:
        store.publish(MINI, by="root")
        deadline = time.monotonic() + 5
        while live.settings().agents.presets["openai-mini"].model != "openai/gpt-4.1-mini":
            assert time.monotonic() < deadline, "the poll never picked the Apply up"
            time.sleep(0.01)
    finally:
        stop()

    assert reads and threading.main_thread().name not in reads


def test_a_derived_value_is_built_once_per_version(store, config):
    clock, made = Clock(), []
    live = _live(store, config, clock)

    def make(settings):
        made.append(settings.agents.presets["openai-mini"].model)
        return object()

    first = live.derive("x", make)
    assert live.derive("x", make) is first
    store.publish(MINI, by="root")
    clock.now += 2
    assert live.derive("x", make) is not first
    assert made[-1] == "openai/gpt-4.1-mini"
    assert len(made) == 2
