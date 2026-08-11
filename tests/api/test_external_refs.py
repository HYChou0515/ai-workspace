"""#700 — a work item remembers which external records it has absorbed.

A legacy system hands an analysis over by creating (or adding to) a work item.
`create_app_item` mints a fresh uuid every call, so without a record of what has
already been handed over, one real-world problem sprawls into N items. The record
is a plain list of opaque `<system>:<record-id>` strings on `WorkItemBase` —
deliberately NOT indexed, because nothing may query it. Measured, and NOT the
`substring LIKE` story an earlier version of this docstring told (that trap needs
the opposite precondition): un-indexed, the predicate has no `indexed_data` to
match, so it returns **zero rows** on every backend. Zero rows is the worst
answer here — "which items already absorbed record X?" answered with nothing
reads as "none", so the caller re-imports and creates the duplicate item this
design exists to prevent. The caller fetches a page of items and filters the
records it already holds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

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
    a per-ref query — the one thing the un-indexed field forbids.

    Asserted against the ENVELOPE listing, the one a picker must actually use:
    `/data` also carries the refs but has no resource id, so a caller cannot act
    on the row it just filtered. Testing the shape nobody may use would leave the
    real one uncovered here."""
    app, _ = _app_and_spec()
    client = TestClient(app)
    client.post("/a/rca/items", json={"title": "From legacy", "external_refs": ["legacy-rca:1"]})
    client.post("/a/rca/items", json={"title": "Opened by hand"})

    rows = client.get("/rca-investigation?limit=100").json()

    by_title = {row["data"]["title"]: row for row in rows}
    assert by_title["From legacy"]["data"]["external_refs"] == ["legacy-rca:1"]
    assert by_title["Opened by hand"]["data"]["external_refs"] == []
    assert by_title["From legacy"]["revision_info"]["resource_id"]


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


def test_no_app_indexes_external_refs():
    """Keep the field out of every index, so the pair of guards stays coherent.

    The real hazard is the sibling test below (nothing may QUERY it). This one
    exists because the two decisions have to move together: indexing the field
    is what would make a query start returning real answers, so an index added
    without deleting the query ban leaves the codebase asserting two things at
    once. Adding it must therefore be a deliberate act that breaks a test and
    gets argued for — not a passing optimisation.
    """
    from workspace_app.apps.registry import _app_models

    def _names(fields) -> list[str]:
        # IndexedFields entries are a bare name, a (name, type) pair, or an
        # IndexableField carrying one.
        out = []
        for f in fields:
            if isinstance(f, str):
                out.append(f)
            elif isinstance(f, tuple):
                out.append(f[0])
            else:
                out.append(getattr(f, "name", ""))
        return out

    offenders = {
        slug: _names(indexed)
        for slug, (_model, indexed) in _app_models().items()
        if "external_refs" in _names(indexed)
    }

    assert not offenders, (
        f"{sorted(offenders)} index external_refs. That is not wrong in itself — an "
        "indexed list[str] field answers .contains exactly — but it only makes sense "
        "together with deleting the no-query guard, so make it one deliberate change."
    )


def test_nothing_in_the_app_queries_external_refs():
    """The guard that matters: a filter on this field returns ZERO ROWS.

    Because it is in no `INDEXED_FIELDS` there is no `indexed_data` for the
    predicate to match, so it returns nothing on every backend; specstar warns
    that such a query "will under-return". Verified against the running API: two
    items, one holding exactly
    `legacy-rca:1`, filtered by `contains legacy-rca:1` → `200` with `[]`, while
    the same query shape over the indexed `severity` returns both rows.

    Zero rows is the worst possible answer here. "Which items already absorbed
    record X?" answered with nothing reads as "none", so the caller re-imports and
    creates the duplicate item this design exists to prevent — with a warning
    nobody reads in production as the only trace.

    A source-text check, deliberately: it costs one mention to trip, and the
    alternative (waiting for someone to notice an empty result) is exactly the
    silence being guarded against.
    """
    src = Path(__file__).resolve().parents[2] / "src" / "workspace_app"

    mentions = sorted(
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if "external_refs" in p.read_text(encoding="utf-8")
    )

    assert mentions == ["apps/base.py"], (
        f"external_refs is referenced in {mentions}. It may be declared, never filtered "
        "on: an un-indexed field yields zero rows, which for a dedupe check reads as "
        "'not absorbed yet' and produces a duplicate item."
    )


def test_every_app_can_be_handed_work_from_outside():
    """Being handed work by an outside system is a platform capability, not one
    App's feature — so it is declared on the base and every App has it, without
    anyone special-casing a slug."""
    from workspace_app.apps.registry import registered_apps

    apps = registered_apps()

    assert apps, "no Apps discovered — this guard would pass vacuously"
    assert all("external_refs" in m.__struct_fields__ for m in apps.values())


def test_one_analysis_may_be_attached_to_a_second_item():
    """Decision 4: uniqueness is PER ITEM, not global. Two investigations may
    legitimately both draw on the same source analysis, so nothing may reject
    the second attachment."""
    app, spec = _app_and_spec()
    client = TestClient(app)
    shared = "legacy-rca:1"

    first = client.post(
        "/a/rca/items", json={"title": "Oven drift", "external_refs": [shared]}
    ).json()["resource_id"]
    second = client.post(
        "/a/rca/items", json={"title": "Coater streaks", "external_refs": [shared]}
    ).json()["resource_id"]

    rm = spec.get_resource_manager(RcaInvestigation)
    assert rm.get(first).data.external_refs == [shared]
    assert rm.get(second).data.external_refs == [shared]
