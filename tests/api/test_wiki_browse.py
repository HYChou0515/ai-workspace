"""#50 P7 — read-only wiki browse endpoints (list pages / get page / rebuild).

After an upload builds a collection's wiki, the FE browses it read-only: list
the page paths, fetch one page's markdown, and trigger a rebuild. These hit the
same WikiFileStore the maintainer writes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from agents import RunContextWrapper
from httpx import ASGITransport, AsyncClient

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import read_new_source_impl, write_file_impl
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent, RunDone
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox


class _WikiWritingRunner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if ctx.wiki_new_source is not None:
            wrapped = RunContextWrapper(ctx)
            new = await read_new_source_impl(wrapped)
            await write_file_impl(wrapped, "/entities/note.md", f"{new}\n\nSources: note.md\n")
        yield RunDone()


def _app(spec):
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_WikiWritingRunner(),
        get_user_id=lambda: "u",
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=2),
    )


async def _build_wiki(c, app) -> str:
    cid = (await c.post("/kb/collections", json={"name": "c", "use_wiki": True})).json()[
        "resource_id"
    ]
    await c.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("note.md", b"Reflow zone 3 runs at 245C.", "text/markdown")},
    )
    # #82: indexing runs on the index queue; draining it runs index → the
    # index→wiki hook (enqueues the maintenance job) → THEN drain the wiki.
    await app.state.index_coordinator.aclose()
    await app.state.wiki_coordinator.aclose()
    return cid


async def test_list_and_read_wiki_pages():
    spec = make_spec(default_user="u")
    app = _app(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = await _build_wiki(c, app)

        tree = (await c.get(f"/kb/collections/{cid}/wiki")).json()
        assert "/WIKI.md" in tree["pages"]
        assert "/entities/note.md" in tree["pages"]

        page = (
            await c.get(f"/kb/collections/{cid}/wiki/page", params={"path": "/entities/note.md"})
        ).json()
        assert page["path"] == "/entities/note.md"
        assert "245C" in page["content"]

        missing = await c.get(f"/kb/collections/{cid}/wiki/page", params={"path": "/nope.md"})
        assert missing.status_code == 404


async def test_wiki_page_write_move_delete():
    # The wiki is now an editable filesystem (#D): write/create a page, move it,
    # delete it — all via the WikiFileStore the maintainer shares.
    spec = make_spec(default_user="u")
    app = _app(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/collections", json={"name": "c", "use_wiki": True})).json()[
            "resource_id"
        ]
        # write (create)
        w = await c.put(
            f"/kb/collections/{cid}/wiki/page",
            params={"path": "/notes.md"},
            content=b"# Notes\n",
        )
        assert w.status_code == 200
        got = await c.get(f"/kb/collections/{cid}/wiki/page", params={"path": "/notes.md"})
        assert got.json()["content"] == "# Notes\n"

        # move / rename
        m = await c.post(
            f"/kb/collections/{cid}/wiki/move",
            params={"from": "/notes.md", "to": "/sub/renamed.md"},
        )
        assert m.status_code == 200
        gone = await c.get(f"/kb/collections/{cid}/wiki/page", params={"path": "/notes.md"})
        assert gone.status_code == 404
        moved = await c.get(f"/kb/collections/{cid}/wiki/page", params={"path": "/sub/renamed.md"})
        assert moved.json()["content"] == "# Notes\n"

        # delete
        d = await c.delete(f"/kb/collections/{cid}/wiki/page", params={"path": "/sub/renamed.md"})
        assert d.status_code == 200
        after = await c.get(f"/kb/collections/{cid}/wiki/page", params={"path": "/sub/renamed.md"})
        assert after.status_code == 404


async def test_rebuild_queues_the_collections_sources():
    spec = make_spec(default_user="u")
    app = _app(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = await _build_wiki(c, app)
        r = await c.post(f"/kb/collections/{cid}/wiki/rebuild")
        assert r.status_code == 200
        assert r.json()["queued"] == 1
        await app.state.wiki_coordinator.aclose()


async def test_rebuild_is_a_noop_for_a_non_wiki_collection():
    spec = make_spec(default_user="u")
    app = _app(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/collections", json={"name": "c", "use_wiki": False})).json()[
            "resource_id"
        ]
        r = await c.post(f"/kb/collections/{cid}/wiki/rebuild")
        assert r.json() == {"queued": 0, "status": "disabled"}


async def test_wiki_status_reports_progress_then_idle():
    spec = make_spec(default_user="u")
    app = _app(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = await _build_wiki(c, app)  # uploads 1 doc + drains the coordinator

        st = (await c.get(f"/kb/collections/{cid}/wiki/status")).json()
        assert st["building"] is False
        assert st["total"] == 1 and st["done"] == 1
        assert st["current"] is None


async def test_wiki_status_is_idle_for_a_collection_that_never_built():
    spec = make_spec(default_user="u")
    app = _app(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/collections", json={"name": "c", "use_wiki": True})).json()[
            "resource_id"
        ]
        st = (await c.get(f"/kb/collections/{cid}/wiki/status")).json()
        assert st == {
            "building": False,
            "total": 0,
            "done": 0,
            "current": None,
            "phase": None,
            "errors": 0,
            "last_error": None,
        }


async def test_clear_wipes_every_wiki_page():
    spec = make_spec(default_user="u")
    app = _app(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = await _build_wiki(c, app)
        before = (await c.get(f"/kb/collections/{cid}/wiki")).json()["pages"]
        assert before  # has pages (WIKI.md + the note)

        r = await c.delete(f"/kb/collections/{cid}/wiki")
        assert r.status_code == 200
        assert r.json()["cleared"] == len(before)

        after = (await c.get(f"/kb/collections/{cid}/wiki")).json()["pages"]
        assert after == []
