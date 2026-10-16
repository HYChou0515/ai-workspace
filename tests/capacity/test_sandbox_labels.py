"""The app labels each sandbox with whose it is (docs/plan-capacity-usage.md
§6.5), through the door a person uses — waking an item's sandbox — and with
the same rule the quota charges by (`ItemLocator.owner_of`)."""

from __future__ import annotations

from workspace_app.config.schema import PerUserResources

from ..quota.test_item_resources import FOUR_CORES, _app, _mk, _wake


def test_a_sandbox_is_labelled_with_its_items_owner_and_app():
    with _app(PerUserResources(), app_resources={"rca": FOUR_CORES}) as (client, spec, sandbox):
        item = _mk(spec, "alice")
        _wake(client, item)

    assert sandbox.specs[-1].labels == {"user": "alice", "app": "rca"}
