"""`run_consumers` gate (#312): the API process can run as a pure *producer*.

When `run_consumers=True` (the default — local dev / tests stay all-in-one) the
lifespan starts every job consumer in-process, exactly as before. When a deploy
sets it `False`, the API still ENQUEUES jobs (producer) but consumes nothing —
dedicated worker pods drain the shared queues instead, each under its own HPA.
"""

from __future__ import annotations

from asgi_lifespan import LifespanManager
from specstar.types import Binary

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import Collection, SourceDoc, make_spec
from workspace_app.sandbox.mock import MockSandbox


def _app(*, run_consumers: bool = True):
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        run_consumers=run_consumers,
    )
    return app, spec


async def test_consumers_run_in_process_by_default():
    app, _ = _app()
    async with LifespanManager(app):
        assert app.state.index_coordinator.consuming
        assert app.state.wiki_coordinator.consuming
        assert app.state.card_gen_coordinator.consuming


async def test_run_consumers_false_disables_consumers_but_keeps_the_producer():
    app, spec = _app(run_consumers=False)
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    doc_id = (
        spec.get_resource_manager(SourceDoc)
        .create(
            SourceDoc(collection_id=cid, path="a.md", content=Binary(data=b"x"), status="indexing")
        )
        .resource_id
    )
    async with LifespanManager(app):
        # No consumer threads started on this (producer-only) process.
        assert not app.state.index_coordinator.consuming
        assert not app.state.wiki_coordinator.consuming
        assert not app.state.card_gen_coordinator.consuming
        # …but enqueue still works: a job is created and left for a worker pod.
        assert app.state.index_coordinator.enqueue(doc_id, cid) is True
        assert not app.state.index_coordinator.consuming  # enqueue never starts a consumer
