"""Per-item user environment variables — the stored half.

The variables live on the ITEM (`WorkItemBase.env_vars`), beside
`attached_tool_prefs` / `attached_skill_prefs`, rather than in a resource of
their own: they have no lifecycle apart from the item, and the item already
brings storage, permission and a PATCH route.

They are deliberately NOT kept in the sandbox. `Sandbox.kill` rmtrees the whole
sandbox root and the idle reaper fires it, and `NfsArchive.persist/restore` only
carry the *workspace* — so a sandbox-only file would lose a user's keys over a
lunch break. The sandbox copy is a per-turn delivery artefact; this is the
source of truth.
"""

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.base import WorkItemBase
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _client() -> TestClient:
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
    )
    return TestClient(app)


def test_every_app_item_carries_env_vars():
    # Tier 1, declared once on the base — an App does not opt in, because any
    # App can run tools and any tool may need a key.
    assert "env_vars" in WorkItemBase.__struct_fields__
    for cls in (RcaInvestigation, PlaygroundItem):
        assert "env_vars" in cls.__struct_fields__


def test_env_vars_default_to_empty():
    # An item that never set one must not be distinguishable from one that set
    # none — the delivery file is then simply empty.
    assert RcaInvestigation(title="t", owner="u").env_vars == {}


def test_env_vars_round_trip_through_the_item_patch_route():
    client = _client()
    rid = client.post("/a/rca/items", json={"title": "t"}).json()["resource_id"]

    r = client.patch(
        f"/rca-investigation/{rid}",
        json=[{"op": "replace", "path": "/env_vars", "value": {"API_KEY": "sk-1", "REGION": "tw"}}],
    )
    assert r.status_code == 200

    data = client.get(f"/rca-investigation/{rid}").json()
    data = data.get("data", data)
    assert data["env_vars"] == {"API_KEY": "sk-1", "REGION": "tw"}


def test_editing_another_field_leaves_env_vars_alone():
    # #587: the item save once shipped the whole cached record with PUT
    # semantics, so an omitted field was read as "clear it" — and a workspace
    # quietly went public. This route is JSON-Patch, which can only touch the
    # paths it names; this test is the guard that it stays that way.
    client = _client()
    rid = client.post("/a/rca/items", json={"title": "t"}).json()["resource_id"]
    client.patch(
        f"/rca-investigation/{rid}",
        json=[{"op": "replace", "path": "/env_vars", "value": {"API_KEY": "sk-1"}}],
    )

    client.patch(
        f"/rca-investigation/{rid}",
        json=[{"op": "replace", "path": "/title", "value": "renamed"}],
    )

    data = client.get(f"/rca-investigation/{rid}").json()
    data = data.get("data", data)
    assert data["title"] == "renamed"
    assert data["env_vars"] == {"API_KEY": "sk-1"}


def test_a_value_may_carry_the_characters_a_key_actually_contains():
    # Keys are not identifiers: base64 padding (`=`), URLs, and JSON blobs all
    # turn up. Storage must not be the layer that mangles them.
    client = _client()
    rid = client.post("/a/rca/items", json={"title": "t"}).json()["resource_id"]
    tricky = "a=b c#d$e`f'g\"h"
    client.patch(
        f"/rca-investigation/{rid}",
        json=[{"op": "replace", "path": "/env_vars", "value": {"TOKEN": tricky}}],
    )

    data = client.get(f"/rca-investigation/{rid}").json()
    data = data.get("data", data)
    assert data["env_vars"]["TOKEN"] == tricky
