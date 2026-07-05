"""#455 P2: an entity record write (create / update) broadcasts a FileChanged on
the item stream so peers refetch the entity list — and the file tree shows an
agent-created record. Human HTTP writes and AI agent-tool writes converge on the
SAME EntityStore write seam, so one broadcast sink covers both. Mirrors the raw
file-save broadcast (test_file_broadcast.py); the engine stream is read directly
(ASGITransport can't read an infinite HTTP response incrementally).
"""

from __future__ import annotations

import asyncio

from httpx import ASGITransport

from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient
from .conftest import register_rca_item

_SCHEMA = (
    b"path: issues\n"
    b"fields:\n"
    b"  title: { role: text, required: true }\n"
    b"  status: { role: status, values: [open, done] }\n"
)
_SKELETON = b"---\ntitle: {{arg.title}}\nstatus: open\n---\n\n{{arg.body?}}\n"


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


async def _ship_schema(c, iid: str) -> None:
    files = ((".entity/issue/schema.yaml", _SCHEMA), (".entity/issue/skeleton.md", _SKELETON))
    for name, body in files:
        r = await c.put(f"/a/rca/items/{iid}/files/{name}", content=body)
        assert r.status_code in (200, 201, 204), r.text


async def test_creating_an_entity_broadcasts_file_changed_for_the_record():
    app, iid = _app("bob")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await _ship_schema(c, iid)
        collector = await _first_event(app, iid)  # subscribe AFTER the schema PUTs
        r = await c.post(f"/a/rca/items/{iid}/entities/issue", json={"args": {"title": "A"}})
        assert r.status_code == 200, r.text
    fc = await asyncio.wait_for(collector, 3)
    assert type(fc).__name__ == "FileChanged"
    assert fc.path == "/issues/1.md" and fc.by == "bob" and fc.kind == "written"


async def test_updating_an_entity_broadcasts_file_changed_for_the_record():
    app, iid = _app("carol")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await _ship_schema(c, iid)
        await c.post(f"/a/rca/items/{iid}/entities/issue", json={"args": {"title": "A"}})
        collector = await _first_event(app, iid)  # after the create's own broadcast
        r = await c.put(f"/a/rca/items/{iid}/entities/issue/1", json={"patch": {"status": "done"}})
        assert r.status_code == 200, r.text
    fc = await asyncio.wait_for(collector, 3)
    assert type(fc).__name__ == "FileChanged"
    assert fc.path == "/issues/1.md" and fc.by == "carol" and fc.kind == "written"
