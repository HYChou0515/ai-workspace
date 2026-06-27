"""#280: an item's `collections.json` can group collections into priority tiers,
and the RCA agent walks them via `ask_knowledge_base(rank)`. These exercise the
real message turn → bridge → KB sub-agent chain (not just the tool in isolation):

- the turn parses `collections.json` once into both the flat union (`collection_ids`)
  and the rank-ordered tiers (`collection_tiers`) on the run context;
- `ask_knowledge_base(question, rank=N)` scopes the spawned KB sub-agent to tier N's
  collection subset (exclusive), so the sub-agent's `collection_ids` is just that tier.
"""

from __future__ import annotations

import asyncio
import json

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Collection, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


def _seed_collections_json(fs: MemoryFileStore, item_id: str, entries: list[dict]) -> None:
    asyncio.run(fs.write(item_id, "/collections.json", json.dumps(entries).encode()))


def test_turn_parses_collections_json_into_flat_ids_and_ranked_tiers():
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    crm = spec.get_resource_manager(Collection)
    a, b, d = (crm.create(Collection(name=n)).resource_id for n in ("A", "B", "D"))
    fs = MemoryFileStore()
    # Sparse tier ints (0, 10) so the operator can insert between later.
    _seed_collections_json(
        fs,
        iid,
        [
            {"id": a, "name": "A", "tier": 0},
            {"id": b, "name": "B", "tier": 0},
            {"id": d, "name": "D", "tier": 10},
        ],
    )
    seen: dict[str, object] = {}

    class _Runner:
        async def run(self, prompt, ctx):  # noqa: ANN001 — test double
            seen["ids"] = list(ctx.collection_ids)
            seen["tiers"] = [list(t) for t in ctx.collection_tiers]
            yield MessageDelta(text="ok")

    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=fs, runner=_Runner())
    TestClient(app).post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    assert seen["ids"] == [a, b, d]  # flat union, file order (glossary scope)
    assert seen["tiers"] == [[a, b], [d]]  # rank 0 = tier 0, rank 1 = tier 10


def test_item_creation_seeds_collections_json_from_profile_default(monkeypatch):
    """#280: a profile's declared default collection set (by name + tier) is
    resolved to live ids and seeded into the new item's collections.json; an
    unresolvable name is skipped (Q9), never blocking creation."""
    from workspace_app.apps import profiles as profiles_mod
    from workspace_app.apps.profiles import ProfileCollection, ProfileManifest

    spec = make_spec(default_user="u")
    crm = spec.get_resource_manager(Collection)
    fab = crm.create(Collection(name="Fab Docs")).resource_id

    monkeypatch.setattr(
        profiles_mod,
        "load_profile",
        lambda slug, name: ProfileManifest(
            collections=[
                ProfileCollection(name="Fab Docs", tier=0),
                ProfileCollection(name="ghost", tier=10),  # unresolvable → skipped
            ]
        ),
    )
    fs = MemoryFileStore()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=fs,
        runner=ScriptedAgentRunner([MessageDelta(text="ok"), RunDone()]),
    )
    client = TestClient(app)
    iid = client.post("/a/rca/items", json={"title": "t"}).json()["resource_id"]

    rows = json.loads(asyncio.run(fs.read(iid, "/collections.json")))
    assert rows == [{"id": fab, "name": "Fab Docs", "tier": 0}]


def test_malformed_collections_json_is_tolerated_as_no_scope():
    """A hand-edited / unparseable collections.json must not crash the turn — it
    degrades to no scope (empty ids + tiers), so the agent searches the whole KB."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    fs = MemoryFileStore()
    asyncio.run(fs.write(iid, "/collections.json", b"{not valid json"))
    seen: dict[str, object] = {}

    class _Runner:
        async def run(self, prompt, ctx):  # noqa: ANN001 — test double
            seen["ids"] = list(ctx.collection_ids)
            seen["tiers"] = [list(t) for t in ctx.collection_tiers]
            yield MessageDelta(text="ok")

    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=fs, runner=_Runner())
    TestClient(app).post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    assert seen["ids"] == []
    assert seen["tiers"] == []


def test_ask_knowledge_base_rank_scopes_the_subagent_to_that_tier():
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    crm = spec.get_resource_manager(Collection)
    a, b, d = (crm.create(Collection(name=n)).resource_id for n in ("A", "B", "D"))
    fs = MemoryFileStore()
    _seed_collections_json(
        fs,
        iid,
        [
            {"id": a, "name": "A", "tier": 0},
            {"id": b, "name": "B", "tier": 0},
            {"id": d, "name": "D", "tier": 10},
        ],
    )
    seen: dict[str, object] = {}

    class _Runner:
        async def run(self, prompt, ctx):  # noqa: ANN001 — test double
            if ctx.sandbox is None:  # the spawned KB sub-agent turn
                seen["colls"] = list(ctx.collection_ids)
                yield MessageDelta(text="kb answer")
                return
            from agents import RunContextWrapper

            from workspace_app.agent import ask_knowledge_base_impl

            # rank 1 = the second-priority tier (tier 10) = ONLY [d] (exclusive).
            seen["out"] = await ask_knowledge_base_impl(RunContextWrapper(ctx), "q", rank=1)
            yield MessageDelta(text="done")

    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=fs, runner=_Runner())
    TestClient(app).post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    assert seen["colls"] == [d]  # exclusive: rank 1 searched ONLY tier 10
    assert "no more tiers" in str(seen["out"]).lower()  # rank 1 is the last tier
