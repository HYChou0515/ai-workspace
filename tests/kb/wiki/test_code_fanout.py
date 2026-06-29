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


class _NoopRunner:
    async def run(self, prompt, ctx):  # the code build path never uses the runner
        if False:  # pragma: no cover
            yield


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
