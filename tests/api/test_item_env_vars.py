"""Per-item user environment variables — the stored half.

The variables live on the ITEM (`WorkItemBase.env_vars`), beside
`attached_tool_prefs` / `attached_skill_prefs`, rather than in a resource of
their own: they have no lifecycle apart from the item, and the item already
brings storage, permission and a PATCH route.

They are deliberately NOT kept in the sandbox. `Sandbox.kill` rmtrees the whole
sandbox root and the idle reaper fires it, and `NfsArchive.persist/restore` only
carry the *workspace* — so a sandbox-only file would lose a user's keys over a
lunch break. Nor are they kept there as a copy: since #673 nothing is written
into the sandbox at all, the values being named on the `exec` that dispatches
each tool and nowhere else. This is the only place they live.
"""

from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.base import WorkItemBase
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
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


def _client_as(holder: dict[str, str]) -> tuple[TestClient, SpecStar]:
    """A client that acts as whoever `holder["id"]` currently names, so one test
    can be the owner and then the participant."""
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec


def _participant_item(spec: SpecStar, *, owner: str, guest: str, env: dict[str, str]) -> str:
    """An item owned by `owner`, carrying `env`, on which `guest` holds exactly the
    Participant grants the members roster hands out (`item_routes._PARTICIPANT_VERBS`)."""
    rm = spec.get_resource_manager(RcaInvestigation)
    subject = [f"user:{guest}"]
    with rm.using(owner):
        rid = rm.create(
            RcaInvestigation(
                title="t",
                owner=owner,
                env_vars=env,
                permission=Permission(
                    visibility="restricted",
                    read_meta=subject,
                    read_chat=subject,
                    read_content=subject,
                    converse=subject,
                ),
            )
        ).resource_id
    return rid


def test_every_app_item_carries_env_vars():
    # Tier 1, declared once on the base — an App does not opt in, because any
    # App can run tools and any tool may need a key.
    assert "env_vars" in WorkItemBase.__struct_fields__
    for cls in (RcaInvestigation, PlaygroundItem):
        assert "env_vars" in cls.__struct_fields__


def test_env_vars_default_to_empty():
    # An item that never set one must not be distinguishable from one that set
    # none — the tool is then handed no variables at all.
    assert RcaInvestigation(title="t", owner="u").env_vars == {}


# Who may READ and who may WRITE these are DIFFERENT verbs, and the gap between
# them is wide enough to be worth pinning rather than rediscovering. The two
# tests below are the contract `useItemAccess.canWriteMeta` mirrors on the FE:
# they characterise what is already true, so that if the backend rule moves, the
# mirror is not left silently claiming the old one.


def test_a_participant_cannot_write_the_env_vars():
    """WRITE is `write_meta`, which the Participant grants do not include.

    This is the 403 the env panel used to collect in silence: the FE offered the
    panel to anyone who could open the item, and only the network tab said no.
    """
    holder = {"id": "alice"}
    client, spec = _client_as(holder)
    rid = _participant_item(spec, owner="bob", guest="alice", env={"API_KEY": "sk-1"})

    r = client.patch(
        f"/rca-investigation/{rid}",
        json=[{"op": "replace", "path": "/env_vars", "value": {"API_KEY": "stolen"}}],
    )
    assert r.status_code == 403

    holder["id"] = "bob"  # and the owner still can
    data = client.get(f"/rca-investigation/{rid}").json()
    assert data.get("data", data)["env_vars"] == {"API_KEY": "sk-1"}


def test_a_participant_CAN_read_the_env_vars_in_full():
    """READ is `read_meta`, and the values are NOT redacted on the way out.

    Deliberate, and the reason the panel does not mask them: anyone who can open
    the item can read them anyway. Pinned because it is the surprising half — a
    key put here is shared with everyone the item is shared with, so a change of
    mind about that has to be a change to this test, not a quiet one.
    """
    holder = {"id": "alice"}
    client, spec = _client_as(holder)
    rid = _participant_item(spec, owner="bob", guest="alice", env={"API_KEY": "sk-1"})

    data = client.get(f"/rca-investigation/{rid}").json()
    assert data.get("data", data)["env_vars"] == {"API_KEY": "sk-1"}


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


def test_the_store_canonicalises_key_order():
    # Observed, and worth a test so nobody builds on the opposite: what comes
    # back is sorted, not the order it was written in. The store needs a stable
    # key order for its content hash. Nothing may depend on the typed order —
    # a UI that promised to keep it would be promising something undone here.
    client = _client()
    rid = client.post("/a/rca/items", json={"title": "t"}).json()["resource_id"]
    client.patch(
        f"/rca-investigation/{rid}",
        json=[
            {"op": "replace", "path": "/env_vars", "value": {"FOO": "1", "BAZ": "2", "AAA": "3"}}
        ],
    )

    data = client.get(f"/rca-investigation/{rid}").json()
    data = data.get("data", data)
    assert list(data["env_vars"]) == sorted(data["env_vars"])
    assert data["env_vars"] == {"AAA": "3", "BAZ": "2", "FOO": "1"}


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
