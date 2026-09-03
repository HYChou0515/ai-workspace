"""`DELETE /a/{slug}/items/{item_id}` — deleting an item deletes what it owns.

The generic specstar delete routes remove only the item ROW and orphan
everything the item owns — worst of all the disk-ledger row, frozen and
charged to the owner forever (plan-delete-item-cascade). This route owns the
ordered cascade; the item row goes LAST, because every earlier step needs it
(owner resolution) and a partial failure must stay retryable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from specstar.types import ResourceIDNotFoundError

from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield RunDone()


def _build():
    spec = make_spec(default_user="default-user")
    filestore = MemoryFileStore()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=filestore,
        runner=_Runner(),
        agent_config_catalog=AgentConfigCatalog(),
    )
    return app, spec, filestore


def _create_item(client: TestClient, **fields: object) -> str:
    resp = client.post("/a/rca/items", json={"title": "doomed", **fields})
    assert resp.status_code == 200, resp.text
    return resp.json()["resource_id"]


async def test_deleting_an_item_removes_the_row_and_its_durable_files():
    """The tracer: the route exists, the item row is PERMANENTLY gone (not
    soft-deleted — a soft row keeps revisions, hence blobs, forever), and the
    durable file snapshot is empty afterwards."""
    app, spec, filestore = _build()
    client = TestClient(app)
    item_id = _create_item(client)
    await filestore.write(item_id, "/data/big.csv", b"x" * 1024)
    assert await filestore.ls(item_id) != []  # seeded + written: files exist

    resp = client.delete(f"/a/rca/items/{item_id}")

    assert resp.status_code == 204, resp.text
    rm = spec.get_resource_manager(RcaInvestigation)
    # Permanently gone: not found — NOT "is deleted" (that would be the soft
    # path, which strands blobs and the ledger).
    with pytest.raises(ResourceIDNotFoundError):
        rm.get(item_id)
    assert await filestore.ls(item_id) == []


async def test_deleting_an_item_refunds_its_disk_quota():
    """THE reason this feature exists: the ledger row must go with the item.
    Before the cascade, it froze at its last measurement and charged the owner
    forever — `DiskLedger.forget` existed with no caller, and once the row was
    gone nothing could resolve the owner to fix it."""
    from workspace_app.quota.disk_ledger import DiskLedger

    app, spec, _fs = _build()
    client = TestClient(app)
    item_id = _create_item(client)
    ledger = DiskLedger(spec)
    await ledger.record(item_id, "default-user", 5_000_000)
    assert await ledger.total_for("default-user") == 5_000_000

    resp = client.delete(f"/a/rca/items/{item_id}")

    assert resp.status_code == 204, resp.text
    assert await ledger.total_for("default-user") == 0
    assert await ledger.per_item_for("default-user") == []  # no ghost row


async def test_deleting_an_item_takes_its_conversations_and_workflow_runs():
    """Conversations and runs are unreachable without their item — leaving them
    is dead weight the blob GC and every item_id-indexed query pays for. Gone
    PERMANENTLY (a soft delete keeps revisions alive)."""
    from workspace_app.resources.conversation import Conversation
    from workspace_app.workflow.run import WorkflowRun

    app, spec, _fs = _build()
    client = TestClient(app)
    item_id = _create_item(client)
    conv_rm = spec.get_resource_manager(Conversation)
    run_rm = spec.get_resource_manager(WorkflowRun)
    conv_id = conv_rm.create(Conversation(item_id=item_id, title="chat")).resource_id
    run_id = run_rm.create(WorkflowRun(item_id=item_id, captured_user="default-user")).resource_id
    # A bystander item's records must survive.
    other = _create_item(client)
    other_conv = conv_rm.create(Conversation(item_id=other, title="keep me")).resource_id

    resp = client.delete(f"/a/rca/items/{item_id}")

    assert resp.status_code == 204, resp.text
    with pytest.raises(ResourceIDNotFoundError):
        conv_rm.get(conv_id)
    with pytest.raises(ResourceIDNotFoundError):
        run_rm.get(run_id)
    assert conv_rm.get(other_conv).data.title == "keep me"


async def test_deleting_an_item_tears_down_its_environment_and_forgets_the_address(monkeypatch):
    """The live-environment half: the session is closed (via `close_session`,
    write-back included — the purge that follows wipes it, and reusing the one
    teardown keeps the #345 lock discipline), and the ADDRESS row is forgotten. `close` deliberately
    keeps the address (a stale row is harmless and deleting one risks erasing a
    peer's live rebuild); delete must clear it — after the cascade there is no
    item for any peer to legitimately rebuild."""
    import workspace_app.api.app as app_mod
    from workspace_app.agent.config_catalog import AgentConfigCatalog as _Cat
    from workspace_app.api.registry import InvestigationRegistry

    captured: dict[str, InvestigationRegistry] = {}
    real = app_mod.InvestigationRegistry

    def _capture(*a, **kw):
        r = real(*a, **kw)
        captured["registry"] = r
        return r

    monkeypatch.setattr(app_mod, "InvestigationRegistry", _capture)
    spec = make_spec(default_user="default-user")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        agent_config_catalog=_Cat(),
    )
    client = TestClient(app)
    item_id = _create_item(client)

    registry = captured["registry"]
    closed: list[str] = []
    real_close = registry.close_session

    async def spy_close(inv_id: str) -> None:
        closed.append(inv_id)
        await real_close(inv_id)

    monkeypatch.setattr(registry, "close_session", spy_close)

    from workspace_app.api.sandbox_address import IAddressStore

    class _AddressSpy(IAddressStore):
        def __init__(self) -> None:
            self.forgotten: list[str] = []

        async def get(self, item_id: str):
            return None

        async def claim(self, item_id: str, handle):
            return handle

        async def swap(self, item_id: str, expected, new):
            return new

        async def forget(self, item_id: str) -> None:
            self.forgotten.append(item_id)

    spy = _AddressSpy()
    registry.address = spy

    resp = client.delete(f"/a/rca/items/{item_id}")

    assert resp.status_code == 204, resp.text
    assert closed == [item_id]
    assert spy.forgotten == [item_id]


async def test_only_the_owner_or_a_superuser_may_delete():
    """Delete is a lifecycle action: owner or superuser, nobody else — the
    same line the permission handler draws for the raw resource actions. A
    member with write access still may not destroy the workspace; an outsider
    gets 404, not 403 (no existence leak)."""
    holder = {"id": "owner-user"}
    spec = make_spec(default_user=lambda: holder["id"], superusers=frozenset({"root"}))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        agent_config_catalog=AgentConfigCatalog(),
        get_user_id=lambda: holder["id"],
        superusers=frozenset({"root"}),
    )
    client = TestClient(app)
    item_id = _create_item(client)
    # Share it with a member (meta+content) so the 403 below is authorization,
    # not visibility.
    resp = client.put(
        f"/a/rca/items/{item_id}/permission",
        json={
            "visibility": "restricted",
            "read_meta": ["user:member-user"],
            "write_meta": ["user:member-user"],
            "read_content": ["user:member-user"],
        },
    )
    assert resp.status_code == 200, resp.text

    holder["id"] = "member-user"
    assert client.delete(f"/a/rca/items/{item_id}").status_code == 403

    holder["id"] = "stranger"
    assert client.delete(f"/a/rca/items/{item_id}").status_code == 404

    holder["id"] = "root"  # a superuser may
    assert client.delete(f"/a/rca/items/{item_id}").status_code == 204

    holder["id"] = "owner-user"
    assert client.delete("/a/rca/items/rca-investigation:nope").status_code == 404


async def test_nothing_resurrects_after_the_delete(monkeypatch):
    """The #366/#492 lesson pointed at deletion: records must not outlive
    reality. A WARM sandbox holding a file is deleted through the route; the
    durable snapshot must end EMPTY (the teardown's write-back lands before the
    purge — reversed order would resurrect the file), and a later mirror sweep
    must find nothing to re-create."""
    import workspace_app.api.app as app_mod
    from workspace_app.api.registry import InvestigationRegistry

    captured: dict[str, InvestigationRegistry] = {}
    real = app_mod.InvestigationRegistry

    def _capture(*a, **kw):
        r = real(*a, **kw)
        captured["registry"] = r
        return r

    monkeypatch.setattr(app_mod, "InvestigationRegistry", _capture)
    spec = make_spec(default_user="default-user")
    filestore = MemoryFileStore()
    sandbox = MockSandbox()
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=filestore,
        runner=_Runner(),
        agent_config_catalog=AgentConfigCatalog(),
    )
    client = TestClient(app)
    item_id = _create_item(client)
    registry = captured["registry"]
    session = await registry.session(item_id)
    await registry.ensure_handle(session)
    assert session.handle is not None
    await sandbox.upload(session.handle, b"live leftovers", "/leftover.txt")

    resp = client.delete(f"/a/rca/items/{item_id}")

    assert resp.status_code == 204, resp.text
    assert await filestore.ls(item_id) == []  # write-back happened BEFORE purge
    mirrored = await registry.mirror_warm()
    assert item_id not in mirrored  # the session is truly gone
    assert await filestore.ls(item_id) == []  # and the sweep re-created nothing


async def test_a_failed_sweep_leaves_the_row_so_the_delete_can_be_retried(monkeypatch):
    """The item row is the transaction marker: it goes LAST, so a sweep that
    dies midway leaves a retryable item — never a rowless orphan (the exact
    mechanism that stranded ledgers before this route existed). The retry
    resumes and finishes."""
    app, spec, filestore = _build()
    client = TestClient(app)
    item_id = _create_item(client)
    await filestore.write(item_id, "/data/big.csv", b"x" * 128)

    real_purge = filestore.purge
    boom = {"armed": True}

    async def flaky_purge(workspace_id: str) -> None:
        if boom["armed"]:
            boom["armed"] = False
            raise RuntimeError("durable store hiccup")
        await real_purge(workspace_id)

    monkeypatch.setattr(filestore, "purge", flaky_purge)

    assert client.delete(f"/a/rca/items/{item_id}").status_code == 500
    rm = spec.get_resource_manager(RcaInvestigation)
    assert rm.get(item_id) is not None  # the row survived — retryable

    assert client.delete(f"/a/rca/items/{item_id}").status_code == 204
    with pytest.raises(ResourceIDNotFoundError):
        rm.get(item_id)
    assert await filestore.ls(item_id) == []
