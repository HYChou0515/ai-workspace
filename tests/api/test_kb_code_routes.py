"""POST /kb/collections (with git_*) + POST /kb/collections/:id/sync.

P3.0 §2.9 routes. A FE creates a code Collection by POSTing the git_url
+ embedder_id; later it calls /sync to re-clone and re-ingest. Tests use
file:// URLs so no network/auth.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.resources import make_spec
from workspace_app.resources.kb import CODE_EMBED_DIM, EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _git(cwd: Path, *args: str) -> None:
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
def remote(tmp_path: Path) -> str:
    work = tmp_path / "repo"
    work.mkdir()
    (work / "a.py").write_text("def f():\n    return 1\n")
    (work / "README.md").write_text("# r\n\nx\n")
    _git(work, "init")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "i")
    return work.as_uri()


@pytest.fixture
def app(tmp_path: Path):
    spec = make_spec(default_user="u")
    text = HashEmbedder(dim=EMBED_DIM)
    code = HashEmbedder(dim=CODE_EMBED_DIM, doc_prefix="code: ")
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=text,
        kb_code_embedder=code,
        kb_pipeline=build_doc_pipeline(embedder=text),
    )


def test_create_collection_accepts_git_url_and_embedder_id(app):
    """The body now takes git_url / git_branch / git_token / embedder_id
    (per §2.9). On a successful POST the persisted Collection carries them."""
    client = TestClient(app)
    resp = client.post(
        "/kb/collections",
        json={
            "name": "my-repo",
            "git_url": "https://gitlab.example/g/r.git",
            "git_branch": "main",
            "git_token": "glpat-xxx",
            "embedder_id": 1,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["git_url"] == "https://gitlab.example/g/r.git"
    assert body["git_branch"] == "main"
    assert body["embedder_id"] == 1
    # token is write-only — we don't echo it back (it's a secret).
    assert "git_token" not in body or body["git_token"] is None


def test_sync_endpoint_clones_and_ingests(app, remote: str):
    """POST /kb/collections/:id/sync clones the git_url + ingests each file.
    The endpoint returns 200 with the cloned HEAD sha."""
    client = TestClient(app)
    created = client.post(
        "/kb/collections",
        json={"name": "repo", "git_url": remote, "embedder_id": 1},
    ).json()
    cid = created["resource_id"]
    resp = client.post(f"/kb/collections/{cid}/sync")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["git_last_sha"], str) and len(body["git_last_sha"]) == 40
    # Verify side-effect via the documents-list endpoint: the cloned files
    # made it into SourceDocs.
    docs = client.get(f"/kb/collections/{cid}/documents").json()["items"]
    paths = {d["path"] for d in docs}
    assert "a.py" in paths
    assert "README.md" in paths


def test_sync_endpoint_404s_on_unknown_collection(app):
    client = TestClient(app)
    resp = client.post("/kb/collections/does-not-exist/sync")
    assert resp.status_code == 404


def test_sync_endpoint_400s_when_collection_has_no_git_url(app):
    client = TestClient(app)
    cid = client.post("/kb/collections", json={"name": "no-git"}).json()["resource_id"]
    resp = client.post(f"/kb/collections/{cid}/sync")
    assert resp.status_code == 400


def test_lifespan_runs_code_sync_sweeper(remote: str):
    """When create_app(code_sync_check_interval=…) is set, the lifespan
    starts a sweeper task that re-syncs due Collections — proving the
    background hook is actually wired (not just the helper class)."""
    import asyncio
    from datetime import timedelta

    spec = make_spec(default_user="u")
    text = HashEmbedder(dim=EMBED_DIM)
    application = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=text,
        kb_pipeline=build_doc_pipeline(embedder=text),
        # Quick poll so the test isn't slow.
        code_sync_check_interval=timedelta(milliseconds=50),
    )
    client = TestClient(application)
    cid = client.post(
        "/kb/collections",
        json={"name": "r", "git_url": remote, "sync_interval_hours": 1},
    ).json()["resource_id"]
    with client:  # enter lifespan
        # The sweeper ticks once every 50ms; poll for the side-effect.
        async def _wait() -> str | None:
            for _ in range(40):  # ~2s budget
                got = client.get("/kb/collections").json()
                for c in got:
                    if c["resource_id"] == cid and c["git_last_sha"]:
                        return c["git_last_sha"]
                await asyncio.sleep(0.05)
            return None

        sha = asyncio.run(_wait())
    assert sha and len(sha) == 40


def test_sync_endpoint_502s_on_clone_failure(app, tmp_path: Path):
    """A bogus git_url surfaces as 502 (upstream failure), not 500."""
    client = TestClient(app)
    bogus = (tmp_path / "no-such").as_uri()
    cid = client.post(
        "/kb/collections", json={"name": "bad", "git_url": bogus, "embedder_id": 1}
    ).json()["resource_id"]
    resp = client.post(f"/kb/collections/{cid}/sync")
    assert resp.status_code == 502
