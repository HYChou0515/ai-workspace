"""#700 — a work item remembers which external records it has absorbed.

A legacy system hands an analysis over by creating (or adding to) a work item.
`create_app_item` mints a fresh uuid every call, so without a record of what has
already been handed over, one real-world problem sprawls into N items. The record
is a plain list of opaque `<system>:<record-id>` strings on `WorkItemBase` —
deliberately NOT indexed, because nothing may query it (see the plan: an
unindexed `.contains` degrades to substring LIKE on SQL backends while the
in-memory test backend keeps exact membership, so a query would be green in CI
and wrong in production). The caller fetches a page of items and filters the
records it already holds.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from httpx import ASGITransport

from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient, TestClient


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield RunDone()


def _app_and_spec():
    spec = make_spec(default_user="u")
    return (
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=_Runner(),
            agent_config_catalog=AgentConfigCatalog(),
        ),
        spec,
    )


def test_create_item_keeps_the_external_refs_it_was_handed():
    """The handoff's whole point: an item created FROM a legacy analysis carries
    that analysis's id, so a later handoff can tell it was already absorbed."""
    app, spec = _app_and_spec()

    r = TestClient(app).post(
        "/a/rca/items",
        json={"title": "Oven drift", "external_refs": ["legacy-rca:12345"]},
    )

    assert r.status_code == 200
    got = spec.get_resource_manager(RcaInvestigation).get(r.json()["resource_id"]).data
    assert got.external_refs == ["legacy-rca:12345"]


def test_listed_records_carry_their_external_refs():
    """The caller decides "already absorbed?" from the page it fetched, so the
    refs must ride along IN the listing. Without this the design collapses into
    a per-ref query — the one thing the un-indexed field forbids."""
    app, _ = _app_and_spec()
    client = TestClient(app)
    client.post("/a/rca/items", json={"title": "From legacy", "external_refs": ["legacy-rca:1"]})
    client.post("/a/rca/items", json={"title": "Opened by hand"})

    rows = client.get("/rca-investigation/data?limit=100").json()

    by_title = {row["title"]: row for row in rows}
    assert by_title["From legacy"]["external_refs"] == ["legacy-rca:1"]
    assert by_title["Opened by hand"]["external_refs"] == []


def test_appending_a_ref_keeps_the_ones_already_there():
    """Handing a SECOND analysis to an item must not erase the first. The
    contract says append via RFC 6902 `add` to `/-` — a diff, never a
    whole-list replace, which is what makes concurrent handoffs survivable."""
    app, spec = _app_and_spec()
    client = TestClient(app)
    item_id = client.post(
        "/a/rca/items",
        json={"title": "Oven drift", "external_refs": ["legacy-rca:1"]},
    ).json()["resource_id"]

    r = client.patch(
        f"/rca-investigation/{item_id}",
        json=[{"op": "add", "path": "/external_refs/-", "value": "legacy-rca:2"}],
    )

    assert r.status_code == 200, r.text
    got = spec.get_resource_manager(RcaInvestigation).get(item_id).data
    assert got.external_refs == ["legacy-rca:1", "legacy-rca:2"]


async def test_concurrent_handoffs_to_one_item_both_survive():
    """Two people hand different analyses to the SAME item at the same moment.
    Both refs must land: losing one silently re-opens the door to a duplicate
    handoff later, since the caller decides "already absorbed?" from this list.

    Scope, stated so this test is not read as proving more than it does: it runs
    on the in-memory backend under a single-threaded event loop, so what it
    actually pins is the CONTRACT — that appending is an RFC 6902 `add` diff and
    not a whole-list replace, which is the shape a future refactor could quietly
    break. A genuine cross-pod lost update against Postgres lives below this
    level and cannot be reproduced here."""
    app, spec = _app_and_spec()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        item_id = (
            await c.post(
                "/a/rca/items",
                json={"title": "Oven drift", "external_refs": ["legacy-rca:0"]},
            )
        ).json()["resource_id"]

        results = await asyncio.gather(
            c.patch(
                f"/rca-investigation/{item_id}",
                json=[{"op": "add", "path": "/external_refs/-", "value": "legacy-rca:1"}],
            ),
            c.patch(
                f"/rca-investigation/{item_id}",
                json=[{"op": "add", "path": "/external_refs/-", "value": "legacy-rca:2"}],
            ),
        )

    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    got = spec.get_resource_manager(RcaInvestigation).get(item_id).data
    assert sorted(got.external_refs) == ["legacy-rca:0", "legacy-rca:1", "legacy-rca:2"]
