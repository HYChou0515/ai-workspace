"""WikiMaintenanceCoordinator (#50 P3) — the ingest hook.

After a doc finishes indexing, a collection with ``use_wiki`` on should fold
that source into its wiki via one maintainer run. Bursty uploads to the same
collection coalesce: the coordinator serialises runs per collection (one at a
time), so the in-process view never races. Cross-worker safety rides on
specstar CAS at the page-write layer (future hardening); this layer guarantees
single-process serialisation.

A scripted runner stands in for the maintainer LLM and drives the real wiki
tools, proving the hook wires the right context end-to-end.
"""

from __future__ import annotations

from agents import RunContextWrapper
from specstar import QB
from specstar.types import Binary

from workspace_app.agent.tools import read_new_source_impl, write_file_impl
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.wiki.coordinator import WikiMaintenanceCoordinator
from workspace_app.kb.wiki.jobs import WikiMaintenanceJob
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import Collection, SourceDoc, make_spec


def _add_source(spec, collection_id: str, path: str, text: str) -> str:
    """Create a ready SourceDoc and return its id (what `index` flips to ready)."""
    rm = spec.get_resource_manager(SourceDoc)
    rev = rm.create(
        SourceDoc(
            collection_id=collection_id,
            path=path,
            content=Binary(data=text.encode()),
            text=text,
            status="ready",
        )
    )
    return rev.resource_id


def _add_source_as(spec, collection_id: str, user: str, path: str, text: str) -> str:
    """Create a ready SourceDoc with the real natural-key id
    (``{collection}/{path}``), created_by ``user`` — exactly as the Ingestor
    does. Used to set up a doc with a specific uploader."""
    rm = spec.get_resource_manager(SourceDoc)
    rev = rm.create(
        SourceDoc(
            collection_id=collection_id,
            path=path,
            content=Binary(data=text.encode()),
            text=text,
            status="ready",
        ),
        resource_id=encode_doc_id(collection_id, path),
    )
    return rev.resource_id


class _RecordingRunner:
    """Stands in for the maintainer LLM: reads the new source and writes a
    page per run, recording every new-source text it was handed."""

    def __init__(self) -> None:
        self.sources_seen: list[str] = []

    async def run(self, prompt, ctx):
        wrapped = RunContextWrapper(ctx)
        new = await read_new_source_impl(wrapped)
        self.sources_seen.append(new)
        await write_file_impl(
            wrapped,
            f"/entities/page-{len(self.sources_seen)}.md",
            f"{new}\n\nSources: see above\n",
        )
        if False:
            yield  # pragma: no cover — make this an async generator


async def test_deleting_a_source_runs_an_unfold_pass_with_its_snapshot():
    """#43 S3: deleting a source enqueues an un-fold remove-pass. The agent is
    handed the removed source's SNAPSHOT (label + text) captured before the row
    was deleted — so the pass still works after the SourceDoc is hard-gone, and
    can scrub the wiki of content/citations that came from it."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc = _add_source_as(spec, cid, "alice", "report.md", "ALICE secret fact")

    runner = _RecordingRunner()
    coord = WikiMaintenanceCoordinator(spec, runner)
    await coord.on_doc_deleted(doc)  # snapshot the source into the unfold job
    spec.get_resource_manager(SourceDoc).permanently_delete(doc)  # row is now gone
    await coord.aclose()  # the remove-pass runs off the snapshot, not a re-read

    seen = "\n".join(runner.sources_seen)
    assert "ALICE secret fact" in seen  # removed content reached the remove-pass
    assert "report.md" in seen  # …labelled so the agent can grep pages for it


async def test_fold_stamps_the_pages_with_the_source_uploader_not_the_worker():
    """#83: the wiki fold runs in a job pod with no request user. Its page writes
    must be credited to the SOURCE's last updater (the uploader), not the bare
    default the worker would otherwise stamp."""
    from workspace_app.kb.wiki.store import _rid
    from workspace_app.resources import WikiPage

    who = {"u": "alice"}
    spec = make_spec(default_user=lambda: who["u"])
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc = _add_source_as(spec, cid, "alice", "report.md", "ALICE: zone 3 setpoint 245C")

    who["u"] = "index-worker"  # the job pod has no request user
    coord = WikiMaintenanceCoordinator(spec, _RecordingRunner())
    await coord.on_doc_indexed(doc)
    await coord.aclose()

    page = spec.get_resource_manager(WikiPage).get(_rid(cid, "/entities/page-1.md"))
    assert page.info.updated_by == "alice"  # credited to the uploader, not the worker


async def test_unfold_stamps_the_pages_with_the_deleter_not_the_worker():
    """#83: the source is gone at unfold time, so there's no updater to preserve.
    The scrub is credited to whoever triggered it — the job's creator
    (``job.info.created_by``), i.e. the user who deleted the source."""
    from workspace_app.kb.wiki.store import _rid
    from workspace_app.resources import WikiPage

    who = {"u": "alice"}
    spec = make_spec(default_user=lambda: who["u"])
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc = _add_source_as(spec, cid, "alice", "report.md", "ALICE secret fact")

    who["u"] = "deleter-bob"  # bob presses delete (request context)
    coord = WikiMaintenanceCoordinator(spec, _RecordingRunner())
    await coord.on_doc_deleted(doc)  # the unfold job is created BY bob
    spec.get_resource_manager(SourceDoc).permanently_delete(doc)

    who["u"] = "wiki-worker"  # the unfold runs later in a job pod
    await coord.aclose()

    page = spec.get_resource_manager(WikiPage).get(_rid(cid, "/entities/page-1.md"))
    assert page.info.updated_by == "deleter-bob"  # the deleter who triggered the scrub


async def test_fold_credits_the_job_and_build_state_to_the_run_requester():
    """#186: the maintenance JOB and the build-state row are derived artifacts of
    the index run, credited to its requester (handed in by the index worker, which
    has no request). Both must survive the worker's own status writes — the
    WikiPage, separately, still goes to the source's uploader (#83)."""
    from specstar.types import TaskStatus

    from workspace_app.kb.wiki.store import _rid
    from workspace_app.resources import WikiBuildState, WikiPage

    who = {"u": "alice"}
    spec = make_spec(default_user=lambda: who["u"])
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc = _add_source_as(spec, cid, "alice", "report.md", "ALICE: zone 3 setpoint 245C")

    who["u"] = "wiki-worker"  # the consumer runs with no request user
    coord = WikiMaintenanceCoordinator(spec, _RecordingRunner())
    # The index worker triggers the fold AS the run's requester (bob reindexed).
    await coord.on_doc_indexed(doc, requested_by="bob")
    await coord.aclose()

    jrm = spec.get_resource_manager(WikiMaintenanceJob)
    jobs = list(jrm.list_resources(QB["status"].eq(TaskStatus.COMPLETED).build()))
    assert jobs  # the fold job ran to completion
    # #186: the job's audit is the requester, not the worker default.
    assert {j.info.created_by for j in jobs} == {"bob"}  # ty: ignore[unresolved-attribute]
    assert {j.info.updated_by for j in jobs} == {"bob"}  # ty: ignore[unresolved-attribute]
    state = spec.get_resource_manager(WikiBuildState).get(cid)
    assert state.info.updated_by == "bob"  # #186: build-state credited to the requester
    page = spec.get_resource_manager(WikiPage).get(_rid(cid, "/entities/page-1.md"))
    assert page.info.updated_by == "alice"  # WikiPage stays the uploader (#83)


async def test_unfold_credits_the_build_state_to_the_deleter():
    """#186: an unfold's build-state writes are credited to the deleter (the job's
    creator), matching the WikiPage scrub credit."""
    from workspace_app.resources import WikiBuildState

    who = {"u": "alice"}
    spec = make_spec(default_user=lambda: who["u"])
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc = _add_source_as(spec, cid, "alice", "report.md", "ALICE secret fact")

    who["u"] = "deleter-bob"  # bob presses delete (request context)
    coord = WikiMaintenanceCoordinator(spec, _RecordingRunner())
    await coord.on_doc_deleted(doc)
    spec.get_resource_manager(SourceDoc).permanently_delete(doc)

    who["u"] = "wiki-worker"  # the unfold runs later in a job pod
    await coord.aclose()

    state = spec.get_resource_manager(WikiBuildState).get(cid)
    assert state.info.updated_by == "deleter-bob"


async def test_deleting_a_source_without_use_wiki_does_not_unfold():
    """No wiki on the collection ⇒ nothing to scrub, no unfold pass."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=False))
        .resource_id
    )
    doc = _add_source(spec, cid, "x.md", "hello")

    runner = _RecordingRunner()
    coord = WikiMaintenanceCoordinator(spec, runner)
    await coord.on_doc_deleted(doc)
    await coord.aclose()
    assert runner.sources_seen == []


async def test_on_doc_deleted_is_a_noop_for_unknown_doc_or_missing_collection():
    spec = make_spec(default_user="u")
    runner = _RecordingRunner()
    coord = WikiMaintenanceCoordinator(spec, runner)
    await coord.on_doc_deleted("does-not-exist")  # doc gone
    ghost = _add_source(spec, "ghost-collection", "x.md", "hi")  # collection gone
    await coord.on_doc_deleted(ghost)
    await coord.aclose()
    assert runner.sources_seen == []


async def test_a_failing_unfold_run_is_recorded_and_does_not_wedge_the_queue():
    """An unfold pass that raises is recorded (errors/last_error) and swallowed,
    so the partition keeps draining and the build ends idle."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc = _add_source_as(spec, cid, "alice", "report.md", "secret")

    class _Boom:
        async def run(self, prompt, ctx):
            raise RuntimeError("unfolder exploded")
            yield  # pragma: no cover — makes this an async generator

    coord = WikiMaintenanceCoordinator(spec, _Boom())
    await coord.on_doc_deleted(doc)
    await coord.aclose()

    st = coord.status(cid)
    assert st.errors == 1 and st.last_error == "the unfold run failed"
    assert not st.building


async def test_indexed_doc_in_a_wiki_collection_triggers_a_maintainer_run():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc_id = _add_source(spec, cid, "reflow-spec.md", "Zone 3 setpoint 245C.")

    runner = _RecordingRunner()
    coord = WikiMaintenanceCoordinator(spec, runner)
    await coord.on_doc_indexed(doc_id)
    await coord.aclose()

    store = WikiFileStore(spec)
    # The schema was seeded and a page derived from the new source landed.
    assert await store.exists(cid, "/WIKI.md")
    pages = await store.ls(cid, "/entities/")
    assert pages
    bodies = [(await store.read(cid, p)).decode() for p in pages]
    assert any("245C" in b for b in bodies)
    # The source's path is handed to the maintainer (for `Sources:` provenance).
    assert any("reflow-spec.md" in s for s in runner.sources_seen)


class _ConfigCapturingRunner:
    """Records the system prompt of the agent config each run was driven with."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def run(self, prompt, ctx):
        self.prompts.append(ctx.agent_config.system_prompt)
        if False:
            yield  # pragma: no cover — make this an async generator


async def test_fold_appends_the_collection_maintainer_guidance_to_the_bundled_prompt():
    """#90: a fold run is driven with the bundled maintainer prompt PLUS the
    collection's own maintainer guidance appended — the machinery stays, the
    operator's domain/organisation guidance rides on top."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(
            Collection(
                name="c", use_wiki=True, wiki_maintainer_guidance="Organize pages by reflow zone."
            )
        )
        .resource_id
    )
    doc_id = _add_source(spec, cid, "a.md", "alpha")

    runner = _ConfigCapturingRunner()
    coord = WikiMaintenanceCoordinator(spec, runner)
    await coord.on_doc_indexed(doc_id)
    await coord.aclose()

    (sys_prompt,) = runner.prompts
    assert "Organize pages by reflow zone." in sys_prompt  # the collection's guidance
    assert "## Collection-specific guidance" in sys_prompt  # …as an appended block
    assert "knowledge wiki" in sys_prompt.lower()  # …on top of the bundled base


async def test_unfold_also_appends_the_collection_maintainer_guidance():
    """#90: the unfold (scrub) pass shares the maintainer guidance — it's the
    same write-side guidance, so a deletion respects the wiki's structure too."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(
            Collection(
                name="c", use_wiki=True, wiki_maintainer_guidance="Keep a defect-code index."
            )
        )
        .resource_id
    )
    doc = _add_source_as(spec, cid, "alice", "report.md", "ALICE secret fact")

    runner = _ConfigCapturingRunner()
    coord = WikiMaintenanceCoordinator(spec, runner)
    await coord.on_doc_deleted(doc)
    spec.get_resource_manager(SourceDoc).permanently_delete(doc)
    await coord.aclose()

    (sys_prompt,) = runner.prompts
    assert "Keep a defect-code index." in sys_prompt
    assert "## Collection-specific guidance" in sys_prompt


async def test_indexed_doc_without_use_wiki_does_not_run_the_maintainer():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=False))
        .resource_id
    )
    doc_id = _add_source(spec, cid, "x.md", "hello")

    runner = _RecordingRunner()
    coord = WikiMaintenanceCoordinator(spec, runner)
    await coord.on_doc_indexed(doc_id)
    await coord.aclose()

    assert runner.sources_seen == []
    assert await WikiFileStore(spec).ls(cid) == []


async def test_bursty_uploads_to_one_collection_are_each_integrated():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    d1 = _add_source(spec, cid, "a.md", "alpha fact")
    d2 = _add_source(spec, cid, "b.md", "beta fact")

    runner = _RecordingRunner()
    coord = WikiMaintenanceCoordinator(spec, runner)
    await coord.on_doc_indexed(d1)
    await coord.on_doc_indexed(d2)
    await coord.aclose()

    joined = " ".join(runner.sources_seen)
    assert "alpha fact" in joined and "beta fact" in joined
    assert len(runner.sources_seen) == 2


async def test_unknown_doc_id_is_a_noop():
    spec = make_spec(default_user="u")
    coord = WikiMaintenanceCoordinator(spec, _RecordingRunner())
    await coord.on_doc_indexed("does-not-exist")
    await coord.aclose()  # no raise, no work


async def test_doc_pointing_at_a_missing_collection_is_a_noop():
    spec = make_spec(default_user="u")
    # A source whose collection no longer exists — the hook must not blow up.
    doc_id = _add_source(spec, "ghost-collection", "x.md", "hi")
    runner = _RecordingRunner()
    coord = WikiMaintenanceCoordinator(spec, runner)
    await coord.on_doc_indexed(doc_id)
    await coord.aclose()
    assert runner.sources_seen == []


async def test_build_status_counts_sources_and_ends_idle():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    d1 = _add_source(spec, cid, "a.md", "alpha")
    d2 = _add_source(spec, cid, "b.md", "beta")

    coord = WikiMaintenanceCoordinator(spec, _RecordingRunner())
    await coord.on_doc_indexed(d1)
    await coord.on_doc_indexed(d2)
    assert coord.status(cid).total == 2  # both joined the build batch
    await coord.aclose()

    st = coord.status(cid)
    assert st.building is False and st.done == 2 and st.current is None and st.phase is None


async def test_build_status_phase_reflects_the_live_tool():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc_id = _add_source(spec, cid, "a.md", "alpha")

    from workspace_app.api.events import RunDone, ToolStart

    class _ToolingRunner:
        """Emits a write_file tool call, then reads back the live phase."""

        def __init__(self) -> None:
            self.coord = None
            self.cid = None
            self.phase_at_write = None

        async def run(self, prompt, ctx):
            yield ToolStart(call_id="c1", name="write_file", args={})
            # The coordinator's on_event has now processed the ToolStart.
            self.phase_at_write = self.coord.status(self.cid).phase  # ty: ignore
            # A second tool in the SAME phase (edit_file is also "writing") —
            # the tracker must NOT re-write the status (phase unchanged).
            yield ToolStart(call_id="c2", name="edit_file", args={})
            yield RunDone()

    runner = _ToolingRunner()
    coord = WikiMaintenanceCoordinator(spec, runner)
    runner.coord = coord
    runner.cid = cid
    await coord.on_doc_indexed(doc_id)
    await coord.aclose()
    assert runner.phase_at_write == "writing"


async def test_a_maintainer_that_hits_the_step_limit_surfaces_in_status():
    """A run that ends on MaxTurnsExceeded having written nothing must not be
    silent — the build status records the error + a reason."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc_id = _add_source(spec, cid, "a.md", "alpha")

    from workspace_app.api.events import MaxTurnsExceeded, RunDone

    class _StepLimited:
        async def run(self, prompt, ctx):
            yield MaxTurnsExceeded(turns=ctx.max_turns or 10)
            yield RunDone()

    coord = WikiMaintenanceCoordinator(spec, _StepLimited())
    await coord.on_doc_indexed(doc_id)
    await coord.aclose()

    st = coord.status(cid)
    assert st.errors == 1
    assert st.last_error is not None and "step limit" in st.last_error


async def test_a_maintainer_run_error_is_recorded_in_status():
    """A RunError (not just step-limit) is surfaced too, so a model/tool failure
    isn't silent."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc_id = _add_source(spec, cid, "a.md", "alpha")

    from workspace_app.api.events import RunDone, RunError

    class _Erroring:
        async def run(self, prompt, ctx):
            yield RunError(message="model unavailable")
            yield RunDone()

    coord = WikiMaintenanceCoordinator(spec, _Erroring())
    await coord.on_doc_indexed(doc_id)
    await coord.aclose()

    st = coord.status(cid)
    assert st.errors == 1 and st.last_error == "model unavailable"


async def test_a_failing_maintainer_run_is_recorded_and_does_not_wedge_the_queue():
    """A maintainer that raises is recorded (errors/last_error) and swallowed —
    the job completes so the partition's queue keeps draining (it doesn't wedge
    on the bad source) and the build ends idle."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc_id = _add_source(spec, cid, "a.md", "alpha")

    class _Boom:
        async def run(self, prompt, ctx):
            raise RuntimeError("maintainer exploded")
            yield  # pragma: no cover — makes this an async generator

    coord = WikiMaintenanceCoordinator(spec, _Boom())
    await coord.on_doc_indexed(doc_id)
    await coord.aclose()  # the exception is swallowed; aclose returns cleanly
    st = coord.status(cid)
    assert st.building is False  # the queue drained — not wedged on the failure
    assert st.errors == 1 and st.last_error is not None


async def test_start_consuming_is_idempotent_and_takes_an_explicit_queue_factory():
    """create_app starts the consumer eagerly via ``start_consuming`` (so idle
    pods help drain) and passes the config-selected queue factory. The call is
    idempotent — a second one is a no-op."""
    from specstar.message_queue import SimpleMessageQueueFactory

    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc_id = _add_source(spec, cid, "a.md", "alpha")
    coord = WikiMaintenanceCoordinator(
        spec, _RecordingRunner(), message_queue_factory=SimpleMessageQueueFactory()
    )
    coord.start_consuming()
    coord.start_consuming()  # idempotent — already consuming
    await coord.on_doc_indexed(doc_id)
    await coord.aclose()
    assert await WikiFileStore(spec).exists(cid, "/WIKI.md")


async def test_a_source_deleted_before_its_run_is_skipped_cleanly():
    """A source removed between enqueue and consume is skipped (its text reads
    as None) without running the maintainer or wedging the queue."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc_id = _add_source(spec, cid, "gone.md", "alpha")
    runner = _RecordingRunner()
    coord = WikiMaintenanceCoordinator(spec, runner)
    await coord.on_doc_indexed(doc_id)  # enqueued (consumer not started yet)
    spec.get_resource_manager(SourceDoc).permanently_delete(doc_id)  # vanishes
    await coord.aclose()  # consumer runs → source text is None → skipped
    assert runner.sources_seen == []  # the maintainer never ran on a missing source
    assert coord.status(cid).building is False  # the job completed; queue drained


async def test_enqueued_jobs_are_partitioned_by_collection():
    """#58: cross-pod per-collection serialisation rides on specstar handing
    out one job per ``partition_key`` at a time. Our job is to stamp the
    collection id as the partition key — assert that, so a second pod's
    consumer serialises against this one. (Consumer NOT started, so we can
    inspect the queued job.)"""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_wiki=True))
        .resource_id
    )
    doc_id = _add_source(spec, cid, "reflow.md", "alpha")
    coord = WikiMaintenanceCoordinator(spec, _RecordingRunner())
    await coord.on_doc_indexed(doc_id)

    jobs = list(spec.get_resource_manager(WikiMaintenanceJob).list_resources(QB.all().build()))
    assert len(jobs) == 1
    job = jobs[0].data
    assert job.partition_key == cid  # ← the cross-pod serialisation key  # ty: ignore
    assert job.payload.collection_id == cid  # ty: ignore[unresolved-attribute]
    assert job.payload.source_path == "reflow.md"  # ty: ignore[unresolved-attribute]

    # NOTE on multipod (#58): a true two-consumer test needs a SHARED backend
    # (in-memory specs don't share). The only test-friendly shared backend, the
    # simple DISK store, isn't safe for concurrent consumers (torn reads while
    # a peer writes), so a disk-backed two-pod test is inherently flaky — that
    # concurrency is what production runs on POSTGRES (transactional) for. We
    # therefore pin the seam WE own (partition_key = collection id) here and
    # rely on specstar's own partitioned-queue guarantees for the rest.
