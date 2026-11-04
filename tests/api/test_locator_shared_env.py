"""`ItemLocator.update_shared_env`: the platform's own write of an item's
shared copy (`docs/plan-env-token-refresh.md` D3) is a patch of what the row
holds when it is written, never a copy read earlier."""

from __future__ import annotations

from workspace_app.api.locator import ItemLocator
from workspace_app.apps.catalog import AppCatalog
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.config.schema import Settings
from workspace_app.resources import make_spec


def _item(env: dict[str, str]):  # noqa: ANN202
    spec = make_spec(default_user="u")
    locator = ItemLocator(spec, AppCatalog(presets=Settings().agents.presets))
    rm = spec.get_resource_manager(PlaygroundItem)
    item = PlaygroundItem(title="t", owner="u", profile="echo", env_vars=env)
    item_id = rm.create(item).resource_id
    return locator, rm, item_id


def test_the_patch_is_given_what_the_row_holds_now():
    locator, rm, item_id = _item({"A": "1", "B": "2"})

    out = locator.update_shared_env(item_id, lambda now: {**now, "A": "9"}, actor="system:t")

    assert out == {"A": "9", "B": "2"}
    assert rm.get(item_id).data.env_vars == {"A": "9", "B": "2"}
    assert rm.get_meta(item_id).updated_by == "system:t"


def test_a_patch_that_declines_writes_nothing():
    locator, rm, item_id = _item({"A": "1"})
    before = rm.get(item_id).info.etag

    out = locator.update_shared_env(item_id, lambda _now: None, actor="system:t")

    assert out == {"A": "1"}
    assert rm.get(item_id).info.etag == before


def test_an_unknown_item_has_no_shared_copy_to_write():
    locator, _rm, _item_id = _item({})
    assert locator.update_shared_env("nope", lambda now: {**now, "A": "1"}, actor="system:t") == {}
