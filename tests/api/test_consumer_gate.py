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
        # #715: archive imports. Listed here because this gate is enumerated, so a
        # coordinator that is wired everywhere EXCEPT this line accepts uploads and
        # silently never writes them — which is exactly how it shipped first.
        assert app.state.import_coordinator.consuming
        # plan-chat-video-export P5: the all-in-one pod renders its own videos.
        assert app.state.chat_video_coordinator.consuming
    # …and the lifespan's drain list names it too, or a SIGTERM would leave its
    # consumer thread running past the loop it captured.
    assert not app.state.chat_video_coordinator.consuming


async def test_the_chat_video_coordinator_writes_through_the_apis_own_facade():
    """The job's every file is a workspace file, so the coordinator gets the SAME
    `WorkspaceFiles` the routes use (quota, jail, mirror) — not a second one."""
    app, _ = _app()
    assert app.state.chat_video_coordinator.files is app.state.workspace_files


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
        assert not app.state.import_coordinator.consuming
        assert not app.state.chat_video_coordinator.consuming
        # …but enqueue still works: a job is created and left for a worker pod.
        assert app.state.index_coordinator.enqueue(doc_id, cid) is True
        assert not app.state.index_coordinator.consuming  # enqueue never starts a consumer
