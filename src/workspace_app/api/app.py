from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from specstar import SpecStar

from ..agent.context import AgentToolContext
from ..filestore.protocol import FileNotFound, FileStore
from ..resources import Conversation, Message, register_all
from ..sandbox.protocol import Sandbox, SandboxSpec
from ..sync import SandboxSync
from .events import AgentEvent, RunCancelled, RunError, to_sse
from .registry import InvestigationRegistry, InvestigationSession
from .runner import AgentRunner


class _MessageBody(BaseModel):
    content: str


def create_app(
    *,
    spec: SpecStar | None = None,
    sandbox: Sandbox,
    filestore: FileStore,
    runner: AgentRunner,
    spa_dist: Path | None = None,
    idle_timeout: timedelta = timedelta(minutes=15),
    idle_check_interval: timedelta = timedelta(seconds=60),
) -> FastAPI:
    if spec is None:
        spec = SpecStar()
        spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    register_all(spec)

    sync = SandboxSync(filestore=filestore, sandbox=sandbox)
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)

    async def _idle_killer() -> None:
        """Periodically reap sandboxes whose last_active is past the
        threshold. The reaper sleeps the check_interval between sweeps
        — short for tests, ~60 s in production."""
        try:
            while True:
                await asyncio.sleep(idle_check_interval.total_seconds())
                await registry.kill_idle(idle_timeout)
        except asyncio.CancelledError:
            return

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        killer = asyncio.create_task(_idle_killer())
        try:
            yield
        finally:
            killer.cancel()
            with contextlib.suppress(BaseException):
                await killer
            await registry.close_all()

    app = FastAPI(title="RCA 3.0", lifespan=lifespan)
    spec.apply(app)

    conv_rm = spec.get_resource_manager(Conversation)

    def _conversation_for(investigation_id: str) -> tuple[str, Conversation]:
        # Linear scan over all Conversation resources for this
        # investigation. Acceptable at v1 scale; swap to indexed lookup
        # when N grows.
        from specstar import QB

        for r in conv_rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
            data = r.data
            assert isinstance(data, Conversation)
            if data.investigation_id == investigation_id:
                return r.info.resource_id, data  # ty: ignore[unresolved-attribute]
        rev = conv_rm.create(Conversation(investigation_id=investigation_id))
        got = conv_rm.get(rev.resource_id).data
        assert isinstance(got, Conversation)
        return rev.resource_id, got

    async def _cancel_prior_turn(session: InvestigationSession) -> None:
        """Cancel the session's in-flight turn and wait for it to wind down.

        Called inside session.lock so the cancel→replace transition is
        serialized. The cancelled task drains its own event queue and
        emits RunCancelled to its subscriber (the old StreamingResponse)
        before exiting.
        """
        prev = session.current_turn
        if prev is None or prev.done():
            return
        prev.cancel()
        with contextlib.suppress(BaseException):
            await prev

    async def _drive_run(
        content: str,
        ctx: AgentToolContext,
        queue: asyncio.Queue[AgentEvent | None],
    ) -> None:
        """Pump events from runner.run into the per-turn queue. Translate
        cancellation and any other failure into a terminal event so the
        subscriber stream always closes cleanly."""
        try:
            async for ev in runner.run(content, ctx):
                await queue.put(ev)
        except asyncio.CancelledError:
            await queue.put(RunCancelled())
            raise
        except Exception as exc:  # noqa: BLE001
            await queue.put(RunError(message=f"{type(exc).__name__}: {exc}"))
        finally:
            await queue.put(None)  # sentinel: stream closed

    @app.post("/investigations/{investigation_id}/messages")
    async def send_message(investigation_id: str, body: _MessageBody) -> StreamingResponse:
        rid, conv = _conversation_for(investigation_id)
        conv.messages.append(Message(role="user", content=body.content))
        conv_rm.update(rid, conv)

        session = await registry.session(investigation_id)
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

        async with session.lock:
            await _cancel_prior_turn(session)
            ctx = AgentToolContext(
                investigation_id=investigation_id,
                sandbox=sandbox,
                filestore=filestore,
                sync=sync,
                sandbox_spec=SandboxSpec(),
                handle=session.handle,
                # Route lazy-create through the registry so session.handle
                # is set (so idle-kill/close_all can find it) and the
                # restore-after-create hook fires.
                ensure_sandbox_via=lambda: registry.ensure_handle(session),
            )
            session.current_turn = asyncio.create_task(_drive_run(body.content, ctx, queue))

        async def gen() -> AsyncIterator[str]:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield to_sse(item)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.delete(
        "/investigations/{investigation_id}/messages/current",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_message(investigation_id: str) -> Response:
        session = await registry.session(investigation_id)
        async with session.lock:
            await _cancel_prior_turn(session)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---- Files API (plan-backend §3.8) ----

    @app.get("/investigations/{investigation_id}/files")
    async def list_files(investigation_id: str, prefix: str = "") -> list[dict]:
        paths = await filestore.ls(investigation_id, prefix)
        out: list[dict] = []
        for p in sorted(paths):
            data = await filestore.read(investigation_id, p)
            out.append({"path": p, "size": len(data)})
        return out

    @app.get("/investigations/{investigation_id}/files/{path:path}")
    async def read_file(investigation_id: str, path: str) -> Response:
        try:
            data = await filestore.read(investigation_id, "/" + path.lstrip("/"))
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Best-effort text/plain when valid UTF-8; otherwise octet-stream.
        try:
            data.decode("utf-8")
            media_type = "text/plain; charset=utf-8"
        except UnicodeDecodeError:
            media_type = "application/octet-stream"
        return Response(content=data, media_type=media_type)

    # Mount the built SPA last so API routes registered above take precedence
    # over the catch-all static handler. If no build exists, skip silently —
    # the API alone is still usable (e.g. via curl or the specstar admin UI).
    if spa_dist is None:
        spa_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if spa_dist.is_dir() and (spa_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=spa_dist, html=True), name="spa")

    return app
