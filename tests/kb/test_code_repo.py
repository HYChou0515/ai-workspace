"""P3.0: clone a git remote into an ephemeral checkout and ingest each
tracked source file into the Collection.

`CodeRepoIngestor` is the public entry point. Tests use file-based
`file://` remotes (no network, no auth) — git protocol stays real, only
the transport is swapped.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from specstar import QB, SpecStar

from workspace_app.kb.code_repo import CodeRepoIngestor, CodeRepoSyncError
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.resources.kb import EMBED_DIM, Collection, DocChunk, SourceDoc


def _git(cwd: Path, *args: str) -> None:
    """Tiny helper — run git with a deterministic identity so commits
    don't depend on the host's gitconfig."""
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "Test",
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
def fake_remote(tmp_path: Path) -> str:
    """A bare-bones local git repo with a couple of `.py` + a `.md` file.
    Returns a `file://` URL CodeRepoIngestor can clone."""
    work = tmp_path / "repo"
    work.mkdir()
    (work / "auth.py").write_text(
        "def login(user: str, pw: str) -> bool:\n    return bool(user and pw)\n"
    )
    (work / "scoring.py").write_text(
        "def score(items: list[int]) -> float:\n"
        "    if not items:\n"
        "        return 0.0\n"
        "    return sum(items) / len(items)\n"
    )
    (work / "README.md").write_text("# Repo\n\nSome project.\n")
    _git(work, "init")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "initial")
    return work.as_uri()  # file:///tmp/.../repo


def _new_code_collection(spec: SpecStar, git_url: str) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(Collection(name="my-repo", git_url=git_url))
        .resource_id
    )


def test_sync_clones_remote_and_ingests_python_files(spec: SpecStar, fake_remote: str):
    """`sync(collection_id)` clones the remote, walks it, and feeds each
    code file through the Ingestor → SourceDoc + chunks land in the
    collection at paths matching the repo layout."""
    cid = _new_code_collection(spec, fake_remote)
    pipeline = build_doc_pipeline(embedder=HashEmbedder(dim=EMBED_DIM))
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=HashEmbedder(dim=EMBED_DIM))
    repo = CodeRepoIngestor(spec, ingestor=ingestor)

    repo.sync(collection_id=cid, user="alice")

    # Both .py files became SourceDocs at their repo-relative paths.
    sd_rm = spec.get_resource_manager(SourceDoc)
    ids = {
        encode_doc_id(cid, "alice", "auth.py"),
        encode_doc_id(cid, "alice", "scoring.py"),
    }
    for doc_id in ids:
        doc = sd_rm.get(doc_id).data
        assert doc.collection_id == cid
        assert doc.status == "ready"
    # README.md is markdown, also welcome — exercised via the existing pipeline.
    md_id = encode_doc_id(cid, "alice", "README.md")
    assert sd_rm.get(md_id).data.status == "ready"

    # Each .py produced at least one chunk (CodeSplitter routed via dispatch).
    chrm = spec.get_resource_manager(DocChunk)
    for doc_id in ids:
        chunks = list(chrm.list_resources((QB["source_doc_id"] == doc_id).build()))
        assert chunks, f"expected chunks for {doc_id}"


def test_sync_persists_last_sha_on_the_collection(spec: SpecStar, fake_remote: str):
    """After a successful sync, the Collection's `git_last_sha` is the
    HEAD of the cloned remote — used downstream for incremental re-sync
    decisions and for showing "synced at commit …" in the FE."""
    cid = _new_code_collection(spec, fake_remote)
    embedder = HashEmbedder(dim=EMBED_DIM)
    pipeline = build_doc_pipeline(embedder=embedder)
    repo = CodeRepoIngestor(
        spec, ingestor=Ingestor(spec, pipeline=pipeline, embedder=embedder)
    )
    repo.sync(collection_id=cid, user="alice")

    cdata = spec.get_resource_manager(Collection).get(cid).data
    assert cdata.git_last_sha
    assert len(cdata.git_last_sha) == 40  # full git sha


def test_sync_skips_when_collection_has_no_git_url(spec: SpecStar):
    """A non-code Collection (no `git_url`) is a no-op; `sync` returns
    cleanly so the scheduler can run blindly over every Collection."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="docs")).resource_id
    embedder = HashEmbedder(dim=EMBED_DIM)
    pipeline = build_doc_pipeline(embedder=embedder)
    repo = CodeRepoIngestor(
        spec, ingestor=Ingestor(spec, pipeline=pipeline, embedder=embedder)
    )
    # Returns without raising; nothing got written.
    repo.sync(collection_id=cid, user="alice")
    sd_rm = spec.get_resource_manager(SourceDoc)
    assert not list(sd_rm.list_resources(QB.all()))


def test_sync_raises_when_clone_fails(spec: SpecStar, tmp_path: Path):
    """A bogus URL (no remote, no creds) bubbles a typed
    `CodeRepoSyncError` so the API layer can return a clean 502."""
    bogus = (tmp_path / "does-not-exist").as_uri()
    cid = _new_code_collection(spec, bogus)
    embedder = HashEmbedder(dim=EMBED_DIM)
    pipeline = build_doc_pipeline(embedder=embedder)
    repo = CodeRepoIngestor(
        spec, ingestor=Ingestor(spec, pipeline=pipeline, embedder=embedder)
    )
    with pytest.raises(CodeRepoSyncError):
        repo.sync(collection_id=cid, user="alice")
