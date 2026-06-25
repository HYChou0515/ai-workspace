"""FastAPI sandbox host — exposes ONE injected `Sandbox` over HTTP.

The host is backend-agnostic: it holds a single `Sandbox` instance (production
injects `IsolatedProcessSandbox`; tests inject `MockSandbox`) and proxies each
protocol method to it. The matching client is `sandbox.http_client.HttpSandbox`.

`create` returns the host's own directly-addressable URL (`advertise_url`, set
from the pod's `POD_IP`) plus the backend's local handle id; the client encodes
both into its opaque handle so every later call routes straight back to this pod.

Errors are mapped to a structured `{"error": <type>, "detail": <msg>}` body with
HTTP 404 so the client can re-raise the matching exception type (`SandboxNotFound`
vs `FileNotFoundError`).
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..protocol import ExecResult, Sandbox, SandboxHandle, SandboxNotFound, SandboxSpec


class _CreateBody(BaseModel):
    image: str = "python:3.12-slim"
    env: dict[str, str] | None = None
    exposed_ports: tuple[int, ...] = ()


class _CreateReply(BaseModel):
    pod_url: str
    remote_id: str


class _ExistsReply(BaseModel):
    exists: bool


class _FileEntryModel(BaseModel):
    path: str
    size: int
    version: str = ""


class _WalkReply(BaseModel):
    entries: list[_FileEntryModel]


class _MkdirBody(BaseModel):
    path: str


class _RenameBody(BaseModel):
    src: str
    dst: str


class _ExecBody(BaseModel):
    cmd: list[str]


def _frame(obj: dict[str, object]) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode()


async def _exec_ndjson(
    sandbox: Sandbox, handle: SandboxHandle, cmd: list[str]
) -> AsyncIterator[bytes]:
    """Run `exec` and yield NDJSON frames as output arrives.

    `{"o": b64}` per live chunk (forwarded to the caller's `on_output`; stdout
    and stderr interleaved, mirroring `LocalProcessSandbox`'s single sink), then
    a final `{"exit", "out", "err"}` with the separated buffers, or
    `{"error", "detail"}` if `exec` raised (the response status is already 200,
    so errors must travel in-band as a frame). The live bytes are re-sent in the
    final frame so the client can rebuild the separated `ExecResult` — small for
    typical output, and faithful to the protocol's two outputs.
    """
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    def on_output(chunk: bytes) -> None:
        queue.put_nowait(("o", chunk))

    async def run() -> None:
        try:
            result = await sandbox.exec(handle, cmd, on_output=on_output)
            queue.put_nowait(("done", result))
        except Exception as exc:  # noqa: BLE001 — relayed in-band as an error frame
            queue.put_nowait(("error", exc))

    task = asyncio.create_task(run())
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "o":
                assert isinstance(payload, bytes)
                yield _frame({"o": base64.b64encode(payload).decode()})
            elif kind == "done":
                assert isinstance(payload, ExecResult)
                yield _frame(
                    {
                        "exit": payload.exit_code,
                        "out": base64.b64encode(payload.stdout).decode(),
                        "err": base64.b64encode(payload.stderr).decode(),
                    }
                )
                return
            else:  # "error"
                assert isinstance(payload, Exception)
                yield _frame({"error": type(payload).__name__, "detail": str(payload)})
                return
    finally:
        await task


def _error(exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": type(exc).__name__, "detail": str(exc)})


def make_host_app(sandbox: Sandbox, *, advertise_url: str) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(SandboxNotFound)
    async def _not_found(_request: Request, exc: SandboxNotFound) -> JSONResponse:
        return _error(exc)

    @app.exception_handler(FileNotFoundError)
    async def _file_not_found(_request: Request, exc: FileNotFoundError) -> JSONResponse:
        return _error(exc)

    @app.post("/sandboxes")
    async def create(body: _CreateBody) -> _CreateReply:
        spec = SandboxSpec(image=body.image, env=body.env, exposed_ports=tuple(body.exposed_ports))
        handle = await sandbox.create(spec)
        return _CreateReply(pod_url=advertise_url, remote_id=handle.id)

    @app.delete("/sandboxes/{rid}", status_code=204)
    async def kill(rid: str) -> None:
        await sandbox.kill(SandboxHandle(id=rid))

    @app.put("/sandboxes/{rid}/file", status_code=204)
    async def upload(rid: str, path: str, request: Request) -> None:
        data = await request.body()
        await sandbox.upload(SandboxHandle(id=rid), data, path)

    @app.get("/sandboxes/{rid}/file")
    async def download(rid: str, path: str) -> Response:
        data = await sandbox.download(SandboxHandle(id=rid), path)
        return Response(content=data, media_type="application/octet-stream")

    @app.get("/sandboxes/{rid}/exists")
    async def exists(rid: str, path: str) -> _ExistsReply:
        ok = await sandbox.exists(SandboxHandle(id=rid), path)
        return _ExistsReply(exists=ok)

    @app.get("/sandboxes/{rid}/walk")
    async def walk(rid: str, root: str) -> _WalkReply:
        entries = await sandbox.walk(SandboxHandle(id=rid), root)
        return _WalkReply(
            entries=[_FileEntryModel(path=e.path, size=e.size, version=e.version) for e in entries]
        )

    @app.delete("/sandboxes/{rid}/file", status_code=204)
    async def delete(rid: str, path: str) -> None:
        await sandbox.delete(SandboxHandle(id=rid), path)

    @app.post("/sandboxes/{rid}/mkdir", status_code=204)
    async def mkdir(rid: str, body: _MkdirBody) -> None:
        await sandbox.mkdir(SandboxHandle(id=rid), body.path)

    @app.delete("/sandboxes/{rid}/dir", status_code=204)
    async def rmdir(rid: str, path: str) -> None:
        await sandbox.rmdir(SandboxHandle(id=rid), path)

    @app.post("/sandboxes/{rid}/rename", status_code=204)
    async def rename(rid: str, body: _RenameBody) -> None:
        await sandbox.rename(SandboxHandle(id=rid), body.src, body.dst)

    @app.post("/sandboxes/{rid}/exec")
    async def exec_(rid: str, body: _ExecBody) -> StreamingResponse:
        return StreamingResponse(
            _exec_ndjson(sandbox, SandboxHandle(id=rid), body.cmd),
            media_type="application/x-ndjson",
        )

    return app
