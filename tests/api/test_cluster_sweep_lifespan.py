"""#506 P8: the cluster sweeper is actually wired into the app lifespan.

When the app boots, a background task backfills any pending card-gen proposal that
has no :class:`ClusterMember` yet (e.g. a run finalized before P6) so the grouped
待審核 inbox can cluster it — proving the sweep hook is wired, not just the helper.
"""

from __future__ import annotations

import asyncio

from specstar import QB

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.card_gen import ProposedCard
from workspace_app.kb.card_gen_run import CardGenRunStore
from workspace_app.kb.card_proposal import CardProposalStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.resources import Collection, make_spec
from workspace_app.resources.kb import EMBED_DIM, ClusterMember
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def test_lifespan_runs_the_cluster_sweeper() -> None:
    spec = make_spec(default_user="u")
    text = HashEmbedder(dim=EMBED_DIM)
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1"])
    CardProposalStore(spec).create_from_proposal(
        cid, run_id, ProposedCard(id="0", keys=["RZ3"], title="RZ3")
    )
    store.finish(run_id, status="done")  # a done run whose proposal has no member

    application = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=text,
        kb_pipeline=build_doc_pipeline(embedder=text),
    )
    client = TestClient(application)
    with client:  # enter lifespan → the cluster sweeper ticks at startup

        async def _wait() -> list[ClusterMember]:
            rm = spec.get_resource_manager(ClusterMember)
            for _ in range(40):  # ~2s budget
                rows = [
                    r.data
                    for r in rm.list_resources((QB["collection_id"] == cid).build())
                    if isinstance(r.data, ClusterMember)
                ]
                if rows:
                    return rows
                await asyncio.sleep(0.05)
            return []

        members = asyncio.run(_wait())

    assert any(m.kind == "proposal" and m.ref_id == "0" for m in members)
