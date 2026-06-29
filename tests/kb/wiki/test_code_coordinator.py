"""Issue #281: the ingest→wiki hook routes a CODE collection (one with a
``git_url``) to a single coalesced ``code_build`` job that runs the
hierarchical ``CodeWikiBuilder`` — instead of the per-source ``fold`` a prose
collection gets. A collection with no code-wiki LLM records an error rather
than crashing the partition.
"""

from __future__ import annotations

from collections.abc import Iterator

from specstar import QB
from specstar.types import Binary

from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.llm import ILlm
from workspace_app.kb.wiki.coordinator import WikiMaintenanceCoordinator
from workspace_app.kb.wiki.jobs import WikiMaintenanceJob
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import Collection, SourceDoc, make_spec


class _Llm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield ("a summary.", False)


class _BoomLlm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        raise RuntimeError("model exploded")
        yield ("", False)  # pragma: no cover — unreachable; makes this a generator


class _NoopRunner:
    async def run(self, prompt, ctx):  # the code path never uses the agent runner
        if False:  # pragma: no cover
            yield


def _code_collection(spec, *, use_wiki: bool = True) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(Collection(name="repo", git_url="https://git.example/r.git", use_wiki=use_wiki))
        .resource_id
    )


def _add_code(spec, cid: str, path: str, src: str) -> str:
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


def _jobs(spec) -> list:
    return [
        r.data
        for r in spec.get_resource_manager(WikiMaintenanceJob).list_resources(QB.all().build())
    ]


async def test_code_collection_enqueues_a_code_build_not_a_fold():
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    doc = _add_code(spec, cid, "a.py", "def a():\n    pass\n")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.on_doc_indexed(doc)

    jobs = _jobs(spec)
    assert len(jobs) == 1
    assert jobs[0].payload.op == "code_split"  # head of the #281 P4 fan-out


async def test_bursty_code_indexes_coalesce_to_one_build():
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    a = _add_code(spec, cid, "a.py", "def a():\n    pass\n")
    b = _add_code(spec, cid, "b.py", "def b():\n    pass\n")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.on_doc_indexed(a)
    await coord.on_doc_indexed(b)  # a build is already queued → coalesce

    builds = [j for j in _jobs(spec) if j.payload.op == "code_split"]
    assert len(builds) == 1


async def test_code_build_job_builds_the_hierarchical_wiki():
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    doc = _add_code(spec, cid, "pkg/m.py", "def go():\n    pass\n")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.on_doc_indexed(doc)
    await coord.aclose()  # the consumer runs the code build

    store = WikiFileStore(spec)
    assert b"def go" in await store.read(cid, "/files/pkg/m.py.md")
    await store.read(cid, "/dirs/pkg.md")  # rolled-up directory page exists
    await store.read(cid, "/architecture.md")  # top-down synthesis exists


async def test_code_collection_without_llm_records_error_not_crash():
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    doc = _add_code(spec, cid, "a.py", "def a():\n    pass\n")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=None)
    await coord.on_doc_indexed(doc)

    assert not [j for j in _jobs(spec) if j.payload.op == "code_split"]  # nothing to run
    assert "not configured" in (coord.status(cid).last_error or "")  # surfaced, not silent


async def test_code_build_failure_is_recorded_not_fatal():
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    doc = _add_code(spec, cid, "a.py", "def a():\n    pass\n")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_BoomLlm())
    await coord.on_doc_indexed(doc)
    await coord.aclose()  # build runs, the model explodes mid-card

    status = coord.status(cid)
    assert status.errors >= 1  # recorded
    assert not status.building  # …and the partition is freed (job completed)


async def test_code_collection_delete_does_not_enqueue_prose_unfold():
    """A1: deleting a source from a CODE collection must NOT run the prose
    unfolder — it would garble the hierarchical code wiki (the unfolder is built
    for prose pages). Deletion does not auto-build either (per the grill: a
    rebuild per deleted file is wasteful); the orphaned /files page is pruned by
    the next rebuild's reconcile instead."""
    spec = make_spec(default_user="u")
    cid = _code_collection(spec)
    doc = _add_code(spec, cid, "a.py", "def a():\n    pass\n")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.on_doc_deleted(doc)

    assert not _jobs(spec)  # no unfold job (and no build) enqueued for a code collection


async def test_non_code_collection_still_folds_per_source():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="prose", use_wiki=True))  # no git_url
        .resource_id
    )
    doc = _add_code(spec, cid, "a.md", "hello")

    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.on_doc_indexed(doc)

    jobs = _jobs(spec)
    assert len(jobs) == 1
    assert jobs[0].payload.op == "fold"  # the prose path is unchanged
