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


def test_a_rollback_puts_no_staged_key_in_force(store):
    # review r2 (all five lenses): rollback went through `publish`, which put
    # every staged key in force — while its dialog said keys keep their values.
    path = "config.llm.api_key"
    store.set_key(path, "sk-applied", by="root")
    first = store.publish({"config": {"llm": {"base_url": "http://a"}}}, by="root")
    store.publish({"config": {"llm": {"base_url": "http://b"}}}, by="root")
    store.set_key(path, "sk-staged", by="root")

    rolled = store.publish(first.patch, by="root", apply_keys=False)

    assert store.applied_key_values() == {path: "sk-applied"}
    assert store.pending_keys() == [path] and rolled.keys_changed == []
    # the same patch with the same keys in force is the same version again
    assert rolled.version == first.version


def test_a_version_records_the_keys_it_puts_in_force(store, monkeypatch):
    # review r2: publish read the keys for the version, wrote the version, then
    # read them AGAIN to apply them; a key set in between went into force under
    # a version that did not count it (and stayed "pending" though applied).
    path = "config.llm.api_key"
    store.set_key(path, "sk-1", by="root")
    real_put = store._put

    def put_then_someone_sets_a_key(model, resource_id, record):
        real_put(model, resource_id, record)
        if type(record).__name__ == "_SettingsVersion":
            store.set_key(path, "sk-2", by="root")

    monkeypatch.setattr(store, "_put", put_then_someone_sets_a_key)
    store.publish({}, by="root")
    monkeypatch.setattr(store, "_put", real_put)

    assert store.applied_key_values() == {path: "sk-1"}
    assert store.pending_keys() == [path]


def test_a_short_key_is_shown_as_set_not_by_its_end(store):
    # review r2: "…" + the last four of a four-character value is the value;
    # every preset env value is a key (M7), and those are often short.
    store.set_key("config.agents.presets.a.env.REGION", "us-east-1", by="root")
    store.set_key("config.llm.api_key", "sk-1234567890abcdef", by="root")

    status = store.key_status()

    assert status["config.llm.api_key"] == "…cdef"
    assert "east" not in status["config.agents.presets.a.env.REGION"]
    assert status["config.agents.presets.a.env.REGION"] == "set"
