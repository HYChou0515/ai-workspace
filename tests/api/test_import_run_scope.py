"""#715: an import run is private to whoever started it.

The run carries the caller's uploaded archive as a blob and names the collection a
worker will write into. specstar generates auto-CRUD routes for every registered
model, and the spec-level permission default is AllowAll — so a model with no
`access_scope` serves every row to every caller, and the careful gate on the
hand-written route is decorative while the same row is served unscoped one path
over.

Reading someone's staged archive is a straight content leak. Writing to their run
is worse: `_process` stores documents into `run.collection_id`, so changing that
field redirects a victim's upload into a collection of the attacker's choosing.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import AsyncIterator

from specstar import SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover


def _app(
    superusers: frozenset[str] = frozenset(),
) -> tuple[TestClient, SpecStar, dict[str, str]]:
    """The acting user is a mutable holder both specstar and the routes read, which
    is how this suite switches identity mid-test."""
    holder = {"id": "alice"}
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=lambda: holder["id"],
        superusers=superusers,  # MUST match make_spec's set (the route-guard's source)
    )
    return TestClient(app), spec, holder


def _archive() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("secret.md", b"a trade secret")
        zf.writestr(
            ".kb-collection/manifest.json",
            json.dumps({"version": 1, "collection": {"name": "Private"}, "context_cards": []}),
        )
    return buf.getvalue()


def _start(client: TestClient, holder: dict[str, str], user: str) -> dict:
    holder["id"] = user
    r = client.post(
        "/kb/collections/imports",
        files={"file": ("archive.zip", _archive(), "application/zip")},
    )
    assert r.status_code == 202, r.text
    return r.json()


def test_a_stranger_cannot_list_or_read_someone_elses_import():
    client, _, holder = _app()
    started = _start(client, holder, "alice")

    holder["id"] = "mallory"
    rows = client.get("/import-run").json()
    ids = [r["revision_info"]["resource_id"] for r in rows]
    assert started["import_id"] not in ids, "a stranger listed someone else's import"

    row = client.get(f"/import-run/{started['import_id']}")
    assert row.status_code in (403, 404), f"a stranger read the row: {row.text[:200]}"


def test_a_stranger_cannot_redirect_someone_elses_import():
    """The write is the dangerous half: `_process` stores into `run.collection_id`,
    so repointing it sends the victim's documents to the attacker."""
    client, _, holder = _app()
    started = _start(client, holder, "alice")
    holder["id"] = "mallory"
    theirs = client.post("/kb/collections", json={"name": "Mallory"}).json()["resource_id"]

    r = client.patch(f"/import-run/{started['import_id']}", json={"collection_id": theirs})

    assert r.status_code in (403, 404), f"a stranger redirected the import: {r.text[:200]}"


def test_a_stranger_cannot_delete_someone_elses_import():
    """Deleting the run strands the import: every worker step reads it, finds
    nothing, and returns — the upload is accepted and then silently abandoned."""
    client, _, holder = _app()
    started = _start(client, holder, "alice")

    holder["id"] = "mallory"
    r = client.delete(f"/import-run/{started['import_id']}")

    assert r.status_code in (403, 404), f"a stranger deleted the import: {r.text[:200]}"


def test_the_owner_still_sees_their_own_import():
    """The fence must not lock out the person it belongs to."""
    client, _, holder = _app()
    started = _start(client, holder, "alice")

    r = client.get(f"/kb/collections/imports/{started['import_id']}")

    assert r.status_code == 200, r.text
    assert r.json()["import_id"] == started["import_id"]


def test_a_superuser_can_still_reach_someone_elses_import():
    """The fence is owner-only, not owner-exclusive: an operator has to be able to
    see why an import is stuck, and delete a run that is holding an archive."""
    client, _, holder = _app(superusers=frozenset({"root"}))
    started = _start(client, holder, "alice")

    holder["id"] = "root"
    assert client.get(f"/import-run/{started['import_id']}").status_code == 200
    assert client.delete(f"/import-run/{started['import_id']}").status_code in (200, 204)


def test_a_stranger_cannot_read_someone_elses_import_through_the_polling_route():
    """The fence has to hold on the route callers are actually told to poll.

    `owner_only_access_scope` fences the auto-CRUD `/import-run/*` routes, and its
    docstring is explicit that naming a collection does not entitle that
    collection's readers to the row. `GET /kb/collections/imports/{id}` — the one
    the 202 response and the docs point at — read the run UNSCOPED and then asked
    a question about the COLLECTION, so a run into any collection a stranger can
    see was theirs to read: member counts, and `errors`, which spells out the
    document PATHS inside someone else's archive. Two rules for one row is one
    rule that will be wrong.
    """
    client, _, holder = _app()
    started = _start(client, holder, "alice")

    holder["id"] = "mallory"
    r = client.get(f"/kb/collections/imports/{started['import_id']}")

    assert r.status_code in (403, 404), f"a stranger polled someone else's import: {r.text[:200]}"


def test_a_superuser_can_poll_someone_elses_import():
    """Owner-only, not owner-exclusive — the same exception the auto-CRUD fence
    makes, so an operator can still see why an import is stuck."""
    client, _, holder = _app(superusers=frozenset({"root"}))
    started = _start(client, holder, "alice")

    holder["id"] = "root"
    r = client.get(f"/kb/collections/imports/{started['import_id']}")

    assert r.status_code == 200, r.text
