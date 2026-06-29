"""Issue #281 follow-up P4 — job-queue fan-out for the code-wiki build.

The L0 file cards are the heavy, high-N work; they fan out into per-batch
``code_card`` jobs joined by a ``CodeWikiBuildRun`` CAS row (mirroring the #227
index fan-out). ``plan_card_batches`` decides the unit granularity (Q2): each
batch stays within one directory (coherence — neighbouring files in a package
share context) AND under a token budget (so no single fat directory becomes a
straggler that gates the CAS join under parallel consumers).
"""

from __future__ import annotations

from collections.abc import Iterator

from specstar.types import Binary

from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.llm import ILlm
from workspace_app.kb.wiki.code_wiki import plan_card_batches
from workspace_app.kb.wiki.code_wiki_run import CodeWikiBuildRunStore
from workspace_app.kb.wiki.coordinator import WikiMaintenanceCoordinator
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import Collection, SourceDoc, make_spec


class _Llm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield ("a summary.", False)


class _CountingLlm(ILlm):
    """Counts every LLM call so a test can assert a no-change rebuild is free."""

    def __init__(self) -> None:
        self.calls = 0

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.calls += 1
        yield ("a summary.", False)


class _EchoLlm(ILlm):
    """A content-dependent summariser: distinct input → distinct one-liner, so a
    changed file's summary changes (and its dir's roll-up input with it). Lets a
    test see WHICH dir pages the incremental build actually rebuilds."""

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        import hashlib

        yield (f"summary {hashlib.sha256(prompt.encode()).hexdigest()[:8]}.", False)


class _CardOkRollupBoomLlm(ILlm):
    """Summarises files fine but explodes on the directory / architecture roll-up,
    so a test can drive a finalize-stage failure (cards succeed, finalize fails)."""

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        if "documenting a source file" in prompt:
            yield ("a summary.", False)
            return
        raise RuntimeError("boom on roll-up")


class _NoopRunner:
    async def run(self, prompt, ctx):  # the code build path never uses the runner
        if False:  # pragma: no cover
            yield


def _jobs(spec) -> list:
    from specstar import QB

    from workspace_app.kb.wiki.jobs import WikiMaintenanceJob

    return [
        r.data
        for r in spec.get_resource_manager(WikiMaintenanceJob).list_resources(QB.all().build())
    ]


def _code_collection(spec) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(Collection(name="repo", git_url="https://git.example/r.git", use_wiki=True))
        .resource_id
    )


def _add(spec, cid: str, path: str, src: str = "def f():\n    pass\n") -> str:
    spec.get_resource_manager(SourceDoc).create(
        SourceDoc(
            collection_id=cid,
            path=path,
            content=Binary(data=src.encode()),
            text=src,
            status="ready",
        ),
        resource_id=encode_doc_id(cid, path),
    )
    return encode_doc_id(cid, path)


def test_files_in_one_directory_under_budget_form_a_single_batch():
    paths = ["app/a.py", "app/b.py"]
    sizes = {"app/a.py": 100, "app/b.py": 100}
    assert plan_card_batches(paths, sizes, budget=1000) == [["app/a.py", "app/b.py"]]


def test_a_directory_over_budget_splits_into_multiple_batches():
    paths = ["app/a.py", "app/b.py", "app/c.py"]
    sizes = {"app/a.py": 600, "app/b.py": 600, "app/c.py": 600}
    # 600+600 > 1000 → b starts a new batch; c likewise.
    assert plan_card_batches(paths, sizes, budget=1000) == [
        ["app/a.py"],
        ["app/b.py"],
        ["app/c.py"],
    ]


def test_batches_never_cross_a_directory_boundary():
    paths = ["app/a.py", "lib/b.py"]
    sizes = {"app/a.py": 10, "lib/b.py": 10}
    # Tiny files, but different dirs → never coalesced (coherence over packing).
    assert plan_card_batches(paths, sizes, budget=1000) == [["app/a.py"], ["lib/b.py"]]


def test_a_single_file_larger_than_budget_gets_its_own_batch():
    paths = ["app/huge.py"]
    sizes = {"app/huge.py": 99999}
    assert plan_card_batches(paths, sizes, budget=1000) == [["app/huge.py"]]


def test_no_paths_yields_no_batches():
    assert plan_card_batches([], {}, budget=1000) == []


# ── fan-out behaviour (split → card jobs → CAS join → finalize) ──────────────


async def test_code_build_fans_out_per_directory_batch_and_completes():
    """A build splits into one card batch per directory (small files, default
    budget), each card job records into the CAS run, and finalize completes it —
    the whole pipeline drains via the consumer with the wiki pages written."""
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    a = _add(spec, cid, "app/a.py")
    _add(spec, cid, "app/b.py")
    _add(spec, cid, "lib/c.py")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.on_doc_indexed(a)  # one coalesced build
    await coord.aclose()  # drains split → 2 card jobs → finalize

    run = CodeWikiBuildRunStore(spec).get(cid)
    assert run is not None
    assert run.status == "done"
    assert run.total == 2  # app/ (a.py+b.py, one batch) and lib/ (c.py) — dir-coherent
    assert sorted(run.done) == [0, 1]
    store = WikiFileStore(spec)
    assert await store.exists(cid, "/files/app/a.py.md")
    assert await store.exists(cid, "/dirs/app.md")
    assert await store.exists(cid, "/architecture.md")


async def test_rebuild_prunes_the_card_of_a_deleted_source():
    """Q4b: a deleted source's ``/files`` card is pruned by the NEXT build's
    finalize reconcile (deletes don't auto-rebuild; a later sync / manual rebuild
    cleans up)."""
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    a = _add(spec, cid, "app/a.py")
    _add(spec, cid, "app/b.py")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.on_doc_indexed(a)
    await coord.aclose()
    store = WikiFileStore(spec)
    assert await store.exists(cid, "/files/app/b.py.md")  # built first time

    # Delete b.py's source, then rebuild — the orphaned card must be pruned.
    spec.get_resource_manager(SourceDoc).permanently_delete(encode_doc_id(cid, "app/b.py"))
    await coord.trigger_code_build(cid)
    await coord.aclose()

    assert await store.exists(cid, "/files/app/a.py.md")
    assert not await store.exists(cid, "/files/app/b.py.md")  # orphan pruned


async def test_status_reports_code_build_progress_from_the_run():
    """#281 P4 / Q4c: a code collection's build status comes from the CAS run
    (card jobs are partition_key=None, invisible to the per-collection job count)."""
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    a = _add(spec, cid, "app/a.py")
    _add(spec, cid, "lib/c.py")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.on_doc_indexed(a)
    await coord.aclose()

    st = coord.status(cid)
    assert not st.building
    assert st.total == 2  # two dir-coherent batches
    assert st.done == 2  # both recorded done in the run
    assert st.errors == 0


async def test_a_no_change_rebuild_makes_no_llm_calls():
    """#281 P5 / Q3: a rebuild with no source change must be free — the L0 cards
    skip on their content hash AND the L1 directory pages + L2 architecture/topics
    skip on their input hash. Without per-page input-hashing the fan-out finalize
    re-runs every dir + architecture LLM call on every rebuild."""
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    a = _add(spec, cid, "app/a.py")
    _add(spec, cid, "app/b.py")
    _add(spec, cid, "lib/c.py")

    llm = _CountingLlm()
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=llm)
    await coord.on_doc_indexed(a)
    await coord.aclose()
    assert llm.calls > 0  # the first build did real work (cards + dirs + arch)

    llm.calls = 0
    await coord.trigger_code_build(cid)  # nothing changed since the last build
    await coord.aclose()
    assert llm.calls == 0  # every page hash-skipped — a free re-pull


async def test_changing_one_file_rebuilds_only_its_dir_chain():
    """#281 P5 / Q3: editing a file in app/ rebuilds app/'s directory page but
    leaves an unrelated lib/ page untouched (its input hash is unchanged)."""
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    a_id = _add(spec, cid, "app/a.py", "def a():\n    return 1\n")
    _add(spec, cid, "lib/c.py", "def c():\n    return 3\n")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_EchoLlm())
    await coord.on_doc_indexed(a_id)
    await coord.aclose()

    store = WikiFileStore(spec)
    lib_before = (await store.read_with_etag(cid, "/dirs/lib.md"))[1]  # ty: ignore[not-subscriptable]
    app_before = (await store.read_with_etag(cid, "/dirs/app.md"))[1]  # ty: ignore[not-subscriptable]

    # Edit app/a.py (new bytes → new content hash → its card + summary change).
    spec.get_resource_manager(SourceDoc).create_or_update(
        a_id,
        SourceDoc(
            collection_id=cid,
            path="app/a.py",
            content=Binary(data=b"def a():\n    return 42  # changed\n"),
            text="def a():\n    return 42  # changed\n",
            status="ready",
        ),
    )
    await coord.trigger_code_build(cid)
    await coord.aclose()

    lib_after = (await store.read_with_etag(cid, "/dirs/lib.md"))[1]  # ty: ignore[not-subscriptable]
    app_after = (await store.read_with_etag(cid, "/dirs/app.md"))[1]  # ty: ignore[not-subscriptable]
    assert lib_after == lib_before  # unrelated dir page not rewritten
    assert app_after != app_before  # the affected dir chain was rebuilt


# ── edge cases (CAS store + coordinator guards) ─────────────────────────────


def test_run_store_mark_done_is_idempotent():
    spec = make_spec(default_user="u")
    store = CodeWikiBuildRunStore(spec)
    store.start("c1", total=2)
    store.mark_done("c1", 0)
    store.mark_done("c1", 0)  # at-least-once redelivery → recorded once
    run = store.get("c1")
    assert run is not None and run.done == [0]


def test_run_store_mutations_on_a_missing_run_are_noops():
    spec = make_spec(default_user="u")
    store = CodeWikiBuildRunStore(spec)
    store.mark_done("ghost", 0)  # never started — no crash, no row created
    assert store.get("ghost") is None


def test_run_store_claim_finalize_is_won_exactly_once():
    spec = make_spec(default_user="u")
    store = CodeWikiBuildRunStore(spec)
    store.start("c2", total=1)
    store.mark_done("c2", 0)
    assert store.claim_finalize("c2") is True  # gate closed → the one winner
    assert store.claim_finalize("c2") is False  # already claimed


async def test_trigger_code_build_on_a_missing_collection_is_a_noop():
    spec = make_spec(default_user="u")
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.trigger_code_build("does-not-exist")  # collection vanished — no crash
    assert not _jobs(spec)


async def test_empty_code_collection_finalizes_with_no_card_jobs():
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)  # use_wiki + git_url but NO sources yet
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.trigger_code_build(cid)
    await coord.aclose()  # split (0 batches) → enqueues finalize directly → done
    run = CodeWikiBuildRunStore(spec).get(cid)
    assert run is not None and run.total == 0 and run.status == "done"


def test_status_is_idle_for_a_never_built_collection():
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    st = coord.status(cid)  # no run, no build state → idle default
    assert not st.building and st.total == 0 and st.last_error is None


async def test_trigger_on_a_non_code_collection_is_a_noop():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="prose", use_wiki=True))  # use_wiki but no git_url
        .resource_id
    )
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.trigger_code_build(cid)  # not a code-wiki collection → no build
    assert not _jobs(spec)


async def test_trigger_coalesces_onto_an_in_flight_build():
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    _add(spec, cid, "app/a.py")
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    CodeWikiBuildRunStore(spec).start(cid, total=3)  # a build is already running
    await coord.trigger_code_build(cid)  # is_active → coalesce, no fresh split
    assert not [j for j in _jobs(spec) if j.payload.op == "code_split"]


def test_consuming_property_reflects_the_consumer():
    spec = make_spec(default_user="u")
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    assert coord.consuming is False
    coord.start_consuming()
    assert coord.consuming is True  # the #312 observable gate


async def test_split_stage_failure_is_recorded_not_fatal():
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    _add(spec, cid, "app/a.py")
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())

    def _boom(*_a, **_k):
        raise RuntimeError("split exploded")

    coord._code_builder.plan_batches = _boom  # ty: ignore[invalid-assignment]  # force split failure
    await coord.on_doc_indexed(encode_doc_id(cid, "app/a.py"))
    await coord.aclose()
    assert coord.status(cid).errors >= 1  # split failure surfaced, not silent


async def test_finalize_stage_failure_is_recorded_not_fatal():
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    a = _add(spec, cid, "app/a.py")
    _add(spec, cid, "app/b.py")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_CardOkRollupBoomLlm())
    await coord.on_doc_indexed(a)
    await coord.aclose()  # cards succeed, the dir/arch roll-up explodes in finalize

    run = CodeWikiBuildRunStore(spec).get(cid)
    assert run is not None and run.status == "error"  # finalize failure stamped terminal
    assert coord.status(cid).errors >= 1  # surfaced, not silent
    assert not coord.status(cid).building  # the partition is freed
