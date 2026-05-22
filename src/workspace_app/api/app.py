from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from specstar import SpecStar

from ..agent.context import AgentToolContext
from ..filestore.protocol import FileStore
from ..resources import Conversation, Message, register_all
from ..sandbox.protocol import Sandbox, SandboxSpec
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
) -> FastAPI:
    if spec is None:
        spec = SpecStar()
        spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    register_all(spec)

    app = FastAPI(title="workspace-app")
    spec.apply(app)

    conv_rm = spec.get_resource_manager(Conversation)

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
            sandbox_spec=SandboxSpec(),
        )

        async def gen() -> AsyncIterator[str]:
            try:
                async for event in runner.run(body.content, ctx):
                    yield to_sse(event)
            except Exception as exc:  # noqa: BLE001 — emit any failure as an event
                yield to_sse(RunError(message=f"{type(exc).__name__}: {exc}"))

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app
