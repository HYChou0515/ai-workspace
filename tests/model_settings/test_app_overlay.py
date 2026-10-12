"""docs/plan-model-settings.md P5 — every reader of an app.json or a profile sees
the published patch, through the two loaders all 32 of them call, not by each
learning about the page."""

from __future__ import annotations

from typing import Any

import pytest

from workspace_app.apps import overlay
from workspace_app.apps.manifest import load_app_manifest
from workspace_app.apps.profiles import load_profile


@pytest.fixture(autouse=True)
def no_source():
    overlay.set_source(None)
    yield
    overlay.set_source(None)


PICKER = {
    "apps": {"playground": {"agent": {"picker": [{"preset": "openai-mini", "name": "Mini"}]}}}
}


def test_without_a_source_the_shipped_files_are_read():
    assert [p.preset for p in load_app_manifest("playground").agent.picker] == ["qwen3-local"]


def test_the_published_patch_reaches_every_reader_of_an_app():
    overlay.set_source(lambda: ("v1", PICKER))

    assert [p.name for p in load_app_manifest("playground").agent.picker] == ["Mini"]


def test_a_new_version_is_seen_and_an_unchanged_one_is_not_rebuilt():
    state: dict[str, Any] = {"version": "v1", "document": PICKER}
    overlay.set_source(lambda: (state["version"], state["document"]))
    first = load_app_manifest("playground")

    assert load_app_manifest("playground") is first  # same version: the same object

    state.update(version="v2", document={})
    assert [p.preset for p in load_app_manifest("playground").agent.picker] == ["qwen3-local"]


def test_the_published_patch_reaches_every_reader_of_a_profile():
    overlay.set_source(
        lambda: ("v1", {"profiles": {"playground": {"default": {"default_preset": "openai-mini"}}}})
    )

    assert load_profile("playground", "default").default_preset == "openai-mini"
