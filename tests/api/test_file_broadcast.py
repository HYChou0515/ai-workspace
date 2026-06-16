"""#43 P4: human file edits in a shared workspace broadcast a FileChanged event
on the per-investigation stream so other viewers refetch (last-write-wins). The
engine stream is read directly (ASGITransport can't read an infinite HTTP
response incrementally); the HTTP SSE endpoint just wraps it.
"""

from __future__ import annotations

import asyncio

from httpx import ASGITransport, AsyncClient

from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from .conftest import register_rca_item


def _app(user: str):
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([RunDone()]),
        get_user_id=lambda: user,
    )
    return app, register_rca_item(spec)


async def _first_event(app, inv: str):
    sub = app.state.turn_engine.subscribe(inv)  # register before mutating

    async def collect():
        async for ev in sub:
            return ev

    return asyncio.create_task(collect())


async def test_writing_a_file_broadcasts_file_changed_with_author_and_kind():
    app, iid = _app("bob")
    collector = await _first_event(app, iid)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put(f"/a/rca/items/{iid}/files/notes.txt", content=b"hi")
        assert r.status_code == 204
    fc = await asyncio.wait_for(collector, 3)
    assert type(fc).__name__ == "FileChanged"
    assert fc.path == "/notes.txt" and fc.by == "bob" and fc.kind == "written"


async def test_deleting_a_file_broadcasts_file_changed():
    app, iid = _app("carol")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.put(f"/a/rca/items/{iid}/files/gone.txt", content=b"x")
        collector = await _first_event(app, iid)
        r = await c.delete(f"/a/rca/items/{iid}/files/gone.txt")
        assert r.status_code == 204
    fc = await asyncio.wait_for(collector, 3)
    assert type(fc).__name__ == "FileChanged"
    assert fc.path == "/gone.txt" and fc.by == "carol" and fc.kind == "deleted"
