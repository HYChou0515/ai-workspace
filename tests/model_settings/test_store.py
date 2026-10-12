"""docs/plan-model-settings.md P2 — where the page's changes live.

A draft that changes nothing running (M4), published versions with who and when
(M5), one small "current version" value every user of the settings compares
(M4), and keys that are written and never read back (M6).
"""

from __future__ import annotations

import pytest

from workspace_app.model_settings.store import SettingsStore, register_settings_models
from workspace_app.resources import make_spec


@pytest.fixture
def store() -> SettingsStore:
    spec = make_spec()
    register_settings_models(spec)
    return SettingsStore(spec)


PATCH = {"agents": {"presets": {"fast": {"model": "openai/gpt-4o-mini"}}}}


def test_a_saved_draft_reads_back_and_publishes_nothing(store):
    store.save_draft(PATCH, by="root")

    assert store.draft() == PATCH
    assert store.current_version() is None  # nothing applied yet


def test_publishing_makes_a_version_everyone_compares(store):
    applied = store.publish(PATCH, by="root")

    assert store.current_version() == applied.version
    assert store.published_patch() == PATCH
    (event,) = store.history()
    assert (event.by, event.patch, event.previous) == ("root", PATCH, None)


def test_the_version_is_the_content_so_the_same_settings_do_not_reload(store):
    first = store.publish(PATCH, by="root")
    store.publish({"kb": {}}, by="root")
    back = store.publish(PATCH, by="ann")  # a rollback is publishing an old patch again

    assert back.version == first.version  # nothing new for consumers to load
    assert [e.by for e in store.history()] == ["ann", "root", "root"]  # newest first
    assert store.history()[0].previous == store.history()[1].version


def test_a_key_is_written_and_never_read_back(store):
    store.set_key("agents.presets.fast.llm.api_key", "sk-live-abcd1234", by="root")

    assert store.key_status() == {"agents.presets.fast.llm.api_key": "…1234"}
    assert store.key_values() == {"agents.presets.fast.llm.api_key": "sk-live-abcd1234"}


def test_changing_a_key_changes_the_version_but_history_never_holds_it(store):
    store.set_key("agents.presets.fast.llm.api_key", "sk-old-1111", by="root")
    before = store.publish(PATCH, by="root")
    store.set_key("agents.presets.fast.llm.api_key", "sk-new-2222", by="root")
    after = store.publish(PATCH, by="root")

    assert after.version != before.version  # consumers must pick up the new key
    assert after.keys_changed == ["agents.presets.fast.llm.api_key"]
    assert "sk-new-2222" not in repr(store.history())


def test_clearing_a_key_removes_it(store):
    store.set_key("agents.presets.fast.llm.api_key", "sk-1", by="root")
    store.clear_key("agents.presets.fast.llm.api_key", by="root")

    assert store.key_status() == {}


def test_a_key_set_or_cleared_since_the_last_apply_is_pending(store):
    # The page says which key changes are waiting for Apply (M20), or a key-only
    # change would show "nothing to apply" while Apply would switch the key.
    path, other = "config.llm.api_key", "config.agents.presets.a.llm.api_key"
    store.set_key(path, "sk-1", by="root")
    assert store.pending_keys() == [path]

    store.publish({}, by="root")
    assert store.pending_keys() == []

    store.set_key(other, "sk-2", by="root")
    store.clear_key(path, by="root")
    assert store.pending_keys() == sorted([path, other])


def test_any_version_ever_applied_can_be_found(store):
    # review r1: rollback looked in `history()` (the newest 50), so anything
    # older answered 404 although it was stored.
    first = store.publish({"config": {"llm": {"base_url": "http://first"}}}, by="root")
    for i in range(55):
        store.publish({"config": {"llm": {"base_url": f"http://{i}"}}}, by="root")

    found = store.applied(first.version)

    assert found is not None and found.patch == first.patch
    assert store.applied("never") is None
