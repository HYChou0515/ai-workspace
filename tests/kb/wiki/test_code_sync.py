"""#355: the ``code_sync`` wiki-job op — clone a code collection's git_url +
ingest it on the wiki worker (off the API), then chain into the code build.

These exercise the real seam: a tiny local git remote is cloned by a real
``CodeRepoIngestor`` through the consumer, so the test proves the job actually
clones, ingests, and (for a wiki collection) builds — not just that a method was
called. The wiki summariser LLM is a deterministic stub.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from specstar import SpecStar

from workspace_app.kb.code_repo import CodeRepoIngestor
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.llm import ILlm
from workspace_app.kb.wiki.code_wiki_run import CodeWikiBuildRunStore
from workspace_app.kb.wiki.coordinator import WikiMaintenanceCoordinator
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import Collection, SourceDoc
from workspace_app.resources.kb import EMBED_DIM


class _Llm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield ("a summary.", False)


class _NoopRunner:
    async def run(self, prompt, ctx):  # the code build path never uses the runner
        if False:  # pragma: no cover
            yield


def _git(cwd: Path, *args: str) -> None:
    env = {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        check=True,
        env=env,
        capture_output=True,
    )


@pytest.fixture
def remote(tmp_path: Path) -> str:
    work = tmp_path / "r"
    (work / "app").mkdir(parents=True)
    (work / "app" / "x.py").write_text("def f():\n    return 1\n")
    _git(work, "init")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "i")
    return work.as_uri()


def _ingestor(spec: SpecStar) -> Ingestor:
    embedder = HashEmbedder(dim=EMBED_DIM)
    return Ingestor(spec, pipeline=build_doc_pipeline(embedder=embedder), embedder=embedder)


def _coll(spec: SpecStar, git_url: str, *, use_wiki: bool = True) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(Collection(name="repo", git_url=git_url, use_wiki=use_wiki, embedder_id=1))
        .resource_id
    )


def _coord(spec: SpecStar, *, llm: ILlm | None) -> WikiMaintenanceCoordinator:
    return WikiMaintenanceCoordinator(
        spec,
        _NoopRunner(),
        code_wiki_llm=llm,
        code_repo=CodeRepoIngestor(spec, ingestor=_ingestor(spec)),
    )


async def test_code_sync_clones_ingests_and_builds_wiki(spec: SpecStar, remote: str):
    """A code_sync on a wiki collection clones the remote, ingests each tracked
    file (a SourceDoc appears), stamps git_last_sha, and chains into the code
    build so the wiki pages are written and the run finishes done."""
    cid = _coll(spec, remote, use_wiki=True)
    coord = _coord(spec, llm=_Llm())
    coord.enqueue_code_sync(cid)
    await coord.aclose()  # drains code_sync → code_split → card → finalize

    # Ingested: the tracked .py is now a SourceDoc.
    from specstar import QB

    docs = spec.get_resource_manager(SourceDoc).list_resources(
        (QB["collection_id"] == cid).build()
    )
    assert [d.data.path for d in docs] == ["app/x.py"]
    # Synced: HEAD sha stamped on the collection.
    coll = spec.get_resource_manager(Collection).get(cid).data
    assert coll.git_last_sha and len(coll.git_last_sha) == 40
    # Built: run done + wiki pages written.
    run = CodeWikiBuildRunStore(spec).get(cid)
    assert run is not None and run.status == "done"
    store = WikiFileStore(spec)
    assert await store.exists(cid, "/files/app/x.py.md")
    assert await store.exists(cid, "/architecture.md")


async def test_code_sync_records_clone_failure(spec: SpecStar, tmp_path: Path):
    """A bogus git_url surfaces as a build-state last_error (the async stand-in
    for the old synchronous 502) + an error run, with no sha stamped."""
    bogus = (tmp_path / "no-such").as_uri()
    cid = _coll(spec, bogus, use_wiki=True)
    coord = _coord(spec, llm=_Llm())
    coord.enqueue_code_sync(cid)
    await coord.aclose()

    run = CodeWikiBuildRunStore(spec).get(cid)
    assert run is not None and run.status == "error"
    assert coord.status(cid).last_error  # surfaced for the FE strip
    assert spec.get_resource_manager(Collection).get(cid).data.git_last_sha is None


async def test_code_sync_records_misconfig_when_no_wiki_llm(spec: SpecStar, remote: str):
    """A wiki collection synced with no code-wiki LLM still clones + ingests, but
    records the not-configured error instead of silently building nothing."""
    cid = _coll(spec, remote, use_wiki=True)
    coord = _coord(spec, llm=None)  # no code-wiki LLM wired
    coord.enqueue_code_sync(cid)
    await coord.aclose()

    # Ingest still happened.
    from specstar import QB

    docs = spec.get_resource_manager(SourceDoc).list_resources(
        (QB["collection_id"] == cid).build()
    )
    assert [d.data.path for d in docs] == ["app/x.py"]
    assert "not configured" in (coord.status(cid).last_error or "")


async def test_code_sync_on_non_wiki_collection_just_ingests(spec: SpecStar, remote: str):
    """A code collection with use_wiki off syncs (clone + ingest for chunk RAG)
    and closes the run done without building any wiki pages."""
    cid = _coll(spec, remote, use_wiki=False)
    coord = _coord(spec, llm=_Llm())
    coord.enqueue_code_sync(cid)
    await coord.aclose()

    coll = spec.get_resource_manager(Collection).get(cid).data
    assert coll.git_last_sha and len(coll.git_last_sha) == 40
    run = CodeWikiBuildRunStore(spec).get(cid)
    assert run is not None and run.status == "done"
    assert not await WikiFileStore(spec).exists(cid, "/architecture.md")


async def test_enqueue_code_sync_coalesces(spec: SpecStar, remote: str):
    """Two back-to-back syncs collapse to one in-flight job (partition serial +
    the active-build coalescing guard)."""
    cid = _coll(spec, remote, use_wiki=True)
    coord = _coord(spec, llm=_Llm())
    coord.enqueue_code_sync(cid)
    coord.enqueue_code_sync(cid)  # coalesced — no second code_sync queued

    from specstar import QB

    from workspace_app.kb.wiki.jobs import WikiMaintenanceJob

    jobs = spec.get_resource_manager(WikiMaintenanceJob).list_resources(QB.all().build())
    assert sum(j.data.payload.op == "code_sync" for j in jobs) == 1
    await coord.aclose()


async def test_enqueue_code_sync_noop_for_non_code_collection(spec: SpecStar):
    """A plain (no git_url) collection enqueues nothing."""
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="plain", embedder_id=1))
        .resource_id
    )
    coord = _coord(spec, llm=_Llm())
    coord.enqueue_code_sync(cid)
    coord.enqueue_code_sync("missing-id")  # unknown id → no-op

    from specstar import QB

    from workspace_app.kb.wiki.jobs import WikiMaintenanceJob

    jobs = spec.get_resource_manager(WikiMaintenanceJob).list_resources(QB.all().build())
    assert jobs == []
