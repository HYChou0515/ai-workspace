"""The blob-GC sweeper background task (#245): when `gc_interval` is set, the
lifespan seeds the CAS lease and ticks `run_blob_gc` on schedule; when it's None,
no sweeper runs. The reclaim behaviour itself is covered in
tests/filestore/test_blob_gc.py — here we only assert the wiring/scheduling."""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta

from asgi_lifespan import LifespanManager

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


def _app(*, gc_interval):
    spec = make_spec(default_user="u")
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        gc_interval=gc_interval,
    )


async def test_sweeper_ticks_run_blob_gc_when_interval_set(monkeypatch):
    ticked = threading.Event()
    # Spy on the reclaim call (runs in a worker thread via asyncio.to_thread).
    monkeypatch.setattr(
        "workspace_app.api.lifecycle.run_blob_gc",
        lambda spec, **kw: ticked.set(),
    )
    app = _app(gc_interval=timedelta(seconds=0.05))
    async with LifespanManager(app):
        for _ in range(100):
            if ticked.is_set():
                break
            await asyncio.sleep(0.05)
    assert ticked.is_set()


async def test_no_sweeper_when_interval_none(monkeypatch):
    called = threading.Event()
    monkeypatch.setattr(
        "workspace_app.api.lifecycle.run_blob_gc",
        lambda spec, **kw: called.set(),
    )
    app = _app(gc_interval=None)
    async with LifespanManager(app):
        await asyncio.sleep(0.2)
    assert not called.is_set()
