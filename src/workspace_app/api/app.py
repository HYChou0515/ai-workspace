from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from specstar import SpecStar

from ..agent.context import AgentToolContext
from ..filestore.protocol import FileNotFound, FileStore
from ..resources import Conversation, Message, register_all
from ..sandbox.protocol import Sandbox, SandboxSpec
from ..sync import SandboxSync
from .events import RunError, to_sse
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
) -> FastAPI:
    if spec is None:
        spec = SpecStar()
        spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    register_all(spec)

    app = FastAPI(title="workspace-app")
    spec.apply(app)

    conv_rm = spec.get_resource_manager(Conversation)
    sync = SandboxSync(filestore=filestore, sandbox=sandbox)

    def _conversation_for(workspace_id: str) -> tuple[str, Conversation]:
        # Linear scan over all Conversation resources for this workspace.
        # Acceptable at v1 scale; swap to indexed lookup when N grows.
        from specstar import QB

        for r in conv_rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
            data = r.data
            assert isinstance(data, Conversation)
            if data.workspace_id == workspace_id:
                return r.info.resource_id, data  # ty: ignore[unresolved-attribute]
        rev = conv_rm.create(Conversation(workspace_id=workspace_id))
        got = conv_rm.get(rev.resource_id).data
        assert isinstance(got, Conversation)
        return rev.resource_id, got

    @app.post("/workspaces/{workspace_id}/messages")
    async def send_message(workspace_id: str, body: _MessageBody) -> StreamingResponse:
        rid, conv = _conversation_for(workspace_id)
        conv.messages.append(Message(role="user", content=body.content))
        conv_rm.update(rid, conv)

        ctx = AgentToolContext(
            workspace_id=workspace_id,
            sandbox=sandbox,
            filestore=filestore,
            sync=sync,
            sandbox_spec=SandboxSpec(),
        )

        async def gen() -> AsyncIterator[str]:
            try:
                async for event in runner.run(body.content, ctx):
                    yield to_sse(event)
            except Exception as exc:  # noqa: BLE001 — emit any failure as an event
                yield to_sse(RunError(message=f"{type(exc).__name__}: {exc}"))

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ---- Files API (plan-backend §3.8) ----

    @app.get("/workspaces/{workspace_id}/files")
    async def list_files(workspace_id: str, prefix: str = "") -> list[dict]:
        paths = await filestore.ls(workspace_id, prefix)
        out: list[dict] = []
        for p in sorted(paths):
            data = await filestore.read(workspace_id, p)
            out.append({"path": p, "size": len(data)})
        return out

    @app.get("/workspaces/{workspace_id}/files/{path:path}")
    async def read_file(workspace_id: str, path: str) -> Response:
        try:
            data = await filestore.read(workspace_id, "/" + path.lstrip("/"))
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
