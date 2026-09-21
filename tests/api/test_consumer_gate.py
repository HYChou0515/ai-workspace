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


def _app(*, run_consumers: bool | list[str] = True):
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
        assert app.state.kb_import_coordinator.consuming
        # plan-chat-video-export P5: the all-in-one pod renders its own videos.
        assert app.state.chat_video_coordinator.consuming
    # …and the lifespan's drain list names it too, or a SIGTERM would leave its
    # consumer thread running past the loop it captured.
    assert not app.state.chat_video_coordinator.consuming


async def test_a_list_consumes_only_the_jobtypes_it_names(caplog):
    """plan-run-consumers-list: one key, three shapes. A single machine that
    wants everything except the heavy kinds lists what it wants; the rest is
    left in the queue for a worker — and SAID at boot, because a job that sits
    `pending` looks to its caller exactly like a queue that never moves."""
    app, spec = _app(run_consumers=["index", "card-gen"])
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    with caplog.at_level("INFO", logger="workspace_app.api.lifecycle"):
        async with LifespanManager(app):
            assert app.state.index_coordinator.consuming
            assert app.state.card_gen_coordinator.consuming
            assert not app.state.wiki_coordinator.consuming
            assert not app.state.kb_import_coordinator.consuming
            assert not app.state.blob_gc_coordinator.consuming
            assert not app.state.chat_video_coordinator.consuming
            # The producer half is untouched for what is not consumed here.
            assert app.state.wiki_coordinator.enqueue_rebuild(cid) is True
            assert not app.state.wiki_coordinator.consuming
    said = [r.getMessage() for r in caplog.records if "NOT consumed" in r.getMessage()]
    assert len(said) == 1, caplog.text
    for absent in ("blob-gc", "chat-video", "kb-import", "wiki"):
        assert absent in said[0]
    for present in ("index", "card-gen"):
        assert present not in said[0].split("NOT consumed", 1)[1]


async def test_an_empty_list_consumes_nothing_and_says_so(caplog):
    app, _ = _app(run_consumers=[])
    with caplog.at_level("INFO", logger="workspace_app.api.lifecycle"):
        async with LifespanManager(app):
            assert not app.state.index_coordinator.consuming
            assert not app.state.card_gen_coordinator.consuming
    said = [r.getMessage() for r in caplog.records if "NOT consumed" in r.getMessage()]
    assert len(said) == 1
    # Every WIRED JobType is named; one this app never wired (`sanity`, `eval`
    # and `graph` are None here) is not a choice the operator made, so it is
    # neither started nor reported.
    from workspace_app.worker import _JOBTYPE_ATTR

    wired = {
        jt
        for jt, attr in _JOBTYPE_ATTR.items()
        if getattr(app.state, f"{attr}_coordinator", None) is not None
    }
    assert wired >= {"index", "wiki", "card-gen", "kb-import", "blob-gc", "chat-video"}
    named = said[0].split("NOT consumed on this process: ", 1)[1].split(" (", 1)[0]
    assert set(named.split(", ")) == wired


async def test_shutdown_drains_only_what_this_process_consumed(monkeypatch):
    """The pure-producer rule (#312: `aclose()` STARTS a consumer on a
    coordinator that never consumed, and would run every pending job on the
    way out) applied per coordinator: a list that left `wiki` to a worker must
    not drain wiki's queue at shutdown either."""
    app, _ = _app(run_consumers=["index"])
    drained: list[str] = []

    async def spy(name, real):
        drained.append(name)
        await real()

    async with LifespanManager(app):
        for name in ("index_coordinator", "wiki_coordinator", "card_gen_coordinator"):
            coordinator = getattr(app.state, name)
            real = coordinator.aclose
            monkeypatch.setattr(coordinator, "aclose", lambda name=name, real=real: spy(name, real))
    assert drained == ["index_coordinator"]


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
        assert not app.state.kb_import_coordinator.consuming
        assert not app.state.chat_video_coordinator.consuming
        # …but enqueue still works: a job is created and left for a worker pod.
        assert app.state.index_coordinator.enqueue(doc_id, cid) is True
        assert not app.state.index_coordinator.consuming  # enqueue never starts a consumer
