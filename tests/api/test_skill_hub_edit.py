"""「修改」— where the owner goes to edit a published skill (plan P8).

An entry remembers the item it was published from (`source_item`). Editing
means going back there, because the skill's files, the chat that wrote them
and the AI that reviews the re-publish all live in an item. Four branches,
decided server-side from that item's real state:

| the source item is…                                   | the answer            |
|--------------------------------------------------------|-----------------------|
| readable, `.skill/<name>/` still there                 | `open` it             |
| readable, the folder is gone                           | install, then `open`  |
| unreadable (transferred away), deleted, or CLOSED      | `new_item`            |

"Closed" is the App's own definition — `lifecycle.closing_states` from its
`app.json` — never a hard-coded status name. Owner-only, like every
management route.
"""

from __future__ import annotations

import pytest

from workspace_app.apps.rca.model import RcaInvestigation, Status
from workspace_app.apps.skill_hub import SkillHubReview, SkillHubStore
from workspace_app.perm import Permission

from .conftest import Harness, register_rca_item

VIEWER = "default-user"  # make_spec()'s default user — what the harness's requests run as


def _md(name: str = "triage") -> bytes:
    return f"---\nname: {name}\ndescription: d\n---\n\nbody\n".encode()


def _hub(harness: Harness) -> SkillHubStore:
    return harness.spa_client.app.state.skill_hub  # ty: ignore[unresolved-attribute]


async def _published_from(hub: SkillHubStore, item_id: str, *, owner: str = VIEWER) -> str:
    return await hub.publish(
        owner=owner,
        name="triage",
        description="d",
        source_item=item_id,
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md(), "references/g.md": b"g"},
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )


def _edit(harness: Harness, entry_id: str):
    return harness.client.post(f"/skill-hub/entries/{entry_id}/edit")


async def test_a_readable_item_that_still_holds_the_folder_is_simply_opened(harness: Harness):
    hub = _hub(harness)
    await harness.filestore.write(harness.iid, "/.skill/triage/SKILL.md", _md())
    entry = await _published_from(hub, harness.iid)

    res = _edit(harness, entry)

    assert res.status_code == 200, res.text
    assert res.json() == {
        "action": "open",
        "app": "rca",
        "profile": "default",
        "item_id": harness.iid,
        "reason": "",
    }


async def test_a_readable_item_that_lost_the_folder_gets_it_back_then_opens(harness: Harness):
    """The folder was deleted in the source item after publishing. The entry's
    files are the latest published version, so they go back in — as a copy
    with `.origin`, like any install — and the item opens."""
    hub = _hub(harness)
    entry = await _published_from(hub, harness.iid)
    assert await harness.filestore.ls(harness.iid, "/.skill/") == []

    res = _edit(harness, entry)

    assert res.status_code == 200, res.text
    assert res.json()["action"] == "open" and res.json()["item_id"] == harness.iid
    assert await harness.filestore.read(harness.iid, "/.skill/triage/references/g.md") == b"g"
    assert await harness.filestore.exists(harness.iid, "/.skill/triage/.origin")


@pytest.mark.parametrize("state", list(Status))
async def test_closed_is_the_apps_own_definition_not_a_status_name(harness: Harness, state: Status):
    """`rca`'s `lifecycle.closing_states` are `resolved` and `abandoned`; the
    others are open. Read from app.json here, so the table above is checked
    against the manifest and not against a list somebody remembered."""
    from workspace_app.apps.manifest import load_app_manifest

    lifecycle = load_app_manifest("rca").lifecycle
    assert lifecycle is not None, "rca declares a lifecycle — the premise of this test"
    closing = set(lifecycle.closing_states)
    hub = _hub(harness)
    item = register_rca_item(harness.spec, status=state)
    await harness.filestore.write(item, "/.skill/triage/SKILL.md", _md())
    entry = await _published_from(hub, item)

    body = _edit(harness, entry).json()

    if state.value in closing:
        assert (body["action"], body["reason"], body["item_id"]) == ("new_item", "closed", "")
    else:
        assert body["action"] == "open"


async def test_closed_follows_each_apps_own_manifest(harness: Harness):
    """`pm` closes on `archived`, `rca` on `resolved` / `abandoned`. A resolver
    that hard-coded one App's names would answer the other wrong."""
    from workspace_app.apps.pm.model import PmProject
    from workspace_app.apps.pm.model import Status as PmStatus

    hub = _hub(harness)
    pm_item = (
        harness.spec.get_resource_manager(PmProject)
        .create(PmProject(title="t", owner=VIEWER, status=PmStatus.ARCHIVED))
        .resource_id
    )
    await harness.filestore.write(pm_item, "/.skill/triage/SKILL.md", _md())
    entry = await hub.publish(
        owner=VIEWER,
        name="triage",
        description="d",
        source_item=pm_item,
        source_app="pm",
        source_profile="default",
        payload={"SKILL.md": _md()},
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )

    body = _edit(harness, entry).json()

    assert (body["action"], body["reason"], body["app"]) == ("new_item", "closed", "pm")


async def test_a_deleted_source_item_means_a_new_item(harness: Harness):
    hub = _hub(harness)
    entry = await _published_from(hub, harness.iid)
    harness.spec.get_resource_manager(RcaInvestigation).delete(harness.iid)

    body = _edit(harness, entry).json()

    assert (body["action"], body["reason"], body["app"], body["profile"]) == (
        "new_item",
        "deleted",
        "rca",
        "default",
    )


async def test_a_source_item_the_owner_can_no_longer_edit_means_a_new_item(harness: Harness):
    """Transferred away: the entry is still hers, the item is not. Read-only
    access is not enough either — editing a skill writes to the item."""
    hub = _hub(harness)
    rm = harness.spec.get_resource_manager(RcaInvestigation)
    with rm.using("alice"):  # alice CREATED it — `created_by` is what `authorize` reads as owner
        theirs = rm.create(
            RcaInvestigation(
                title="t",
                owner="alice",
                permission=Permission(visibility="restricted", read_meta=["user:default-user"]),
            )
        ).resource_id
    await harness.filestore.write(theirs, "/.skill/triage/SKILL.md", _md())
    entry = await _published_from(hub, theirs)

    body = _edit(harness, entry).json()

    assert (body["action"], body["reason"]) == ("new_item", "no_access")


async def test_an_unknown_source_item_reads_as_deleted(harness: Harness):
    hub = _hub(harness)
    entry = await _published_from(hub, "never-was")

    body = _edit(harness, entry).json()

    assert (body["action"], body["reason"]) == ("new_item", "deleted")


async def test_only_the_owner_may_ask(harness: Harness):
    hub = _hub(harness)
    entry = await _published_from(hub, harness.iid, owner="alice")

    assert _edit(harness, entry).status_code == 403


async def test_republishing_from_the_new_item_moves_the_source(harness: Harness):
    """The last row of the table: after `new_item`, the person installs into
    the new item and publishes from there — and the entry now points at it.
    Through the real doors: the install route, then the store's publish as
    the tool does it."""
    hub = _hub(harness)
    old_item = register_rca_item(harness.spec, status=Status.RESOLVED)
    entry = await _published_from(hub, old_item)
    assert _edit(harness, entry).json()["action"] == "new_item"

    res = harness.client.post(harness.wpath("/skills/install"), json={"entry_id": entry})
    assert res.status_code == 200, res.text
    again = await hub.publish(
        owner=VIEWER,
        name="triage",
        description="d",
        source_item=harness.iid,
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": _md()},
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )

    assert again == entry
    assert _edit(harness, entry).json()["item_id"] == harness.iid


async def test_a_superuser_owner_edits_through_a_source_item_the_gate_would_let_them_into():
    """Review round 1: the resolver re-implemented the item gate by hand and
    forgot `superusers`, so it diverged from `GET …/skills` on the same item
    (200 there, `no_access` here). It now asks the one gate."""
    from workspace_app.api.app import create_app
    from workspace_app.api.events import RunDone
    from workspace_app.api.runner import ScriptedAgentRunner
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import TestClient as ApiTestClient

    spec = make_spec(default_user="root", superusers=frozenset({"root"}))
    filestore = SpecstarFileStore(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=filestore,
        runner=ScriptedAgentRunner([RunDone()]),
        superusers=frozenset({"root"}),
    )
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        bobs_private = rm.create(
            RcaInvestigation(title="t", owner="bob", permission=Permission(visibility="private"))
        ).resource_id
    await filestore.write(bobs_private, "/.skill/triage/SKILL.md", _md())
    hub: SkillHubStore = app.state.skill_hub
    entry = await _published_from(hub, bobs_private, owner="root")
    client = ApiTestClient(app)

    assert client.get(f"/a/rca/items/{bobs_private}/skills").status_code == 200
    assert client.post(f"/skill-hub/entries/{entry}/edit").json()["action"] == "open"
