"""HttpSandbox client wire tests (L1, unit, no isolation, no root).

The sandbox host is now a SEPARATE service (`sandbox-host/`, its own package +
deps) — the app shares no Python modules with it, only the HTTP wire contract
(`docs/sandbox-host-wire.md`). So the client is tested against a **fake host**
defined right here: a minimal ASGI app that mirrors the wire contract over an
in-process transport (`httpx.ASGITransport`). The app owns the contract, so this
fake is its reference of it; the real host has its own conformance tests. The
full client round-trip (serialization, NDJSON exec streaming, raw-byte files,
handle encode/decode, error→exception mapping) is exercised with no network and
no privilege.
"""

from __future__ import annotations

import base64
import json
import socket
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from httpx import ASGITransport

from workspace_app.sandbox.http_client import HttpSandbox, _encode_handle
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxNotFound, SandboxSpec

_ADVERTISE = "http://sandbox-host-pod:8000"


def _err(exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": type(exc).__name__, "detail": str(exc)})


def _fake_host(backend: MockSandbox, advertise_url: str) -> FastAPI:
    """A minimal ASGI mirror of the sandbox-host wire contract, backed by an
    in-memory `MockSandbox`. Independent of the real host package — it exists so
    the client can be exercised against the contract the app defines."""
    app = FastAPI()

    @app.exception_handler(SandboxNotFound)
    async def _nf(_r: Request, exc: SandboxNotFound) -> JSONResponse:
        return _err(exc)

    @app.exception_handler(FileNotFoundError)
    async def _fnf(_r: Request, exc: FileNotFoundError) -> JSONResponse:
        return _err(exc)

    @app.post("/sandboxes")
    async def create(body: dict) -> dict[str, str]:
        h = await backend.create(SandboxSpec())
        return {"pod_url": advertise_url, "remote_id": h.id}

    @app.delete("/sandboxes/{rid}", status_code=204)
    async def kill(rid: str) -> None:
        await backend.kill(SandboxHandle(id=rid))

    @app.put("/sandboxes/{rid}/file", status_code=204)
    async def upload(rid: str, path: str, request: Request) -> None:
        await backend.upload(SandboxHandle(id=rid), await request.body(), path)

    @app.get("/sandboxes/{rid}/file")
    async def download(rid: str, path: str) -> Response:
        data = await backend.download(SandboxHandle(id=rid), path)
        return Response(content=data, media_type="application/octet-stream")

    @app.get("/sandboxes/{rid}/exists")
    async def exists(rid: str, path: str) -> dict[str, bool]:
        return {"exists": await backend.exists(SandboxHandle(id=rid), path)}

    @app.get("/sandboxes/{rid}/walk")
    async def walk(rid: str, root: str) -> dict[str, list[dict]]:
        entries = await backend.walk(SandboxHandle(id=rid), root)
        return {
            "entries": [{"path": e.path, "size": e.size, "version": e.version} for e in entries]
        }

    @app.delete("/sandboxes/{rid}/file", status_code=204)
    async def delete(rid: str, path: str) -> None:
        await backend.delete(SandboxHandle(id=rid), path)

    @app.post("/sandboxes/{rid}/mkdir", status_code=204)
    async def mkdir(rid: str, body: dict) -> None:
        await backend.mkdir(SandboxHandle(id=rid), body["path"])

    @app.delete("/sandboxes/{rid}/dir", status_code=204)
    async def rmdir(rid: str, path: str) -> None:
        await backend.rmdir(SandboxHandle(id=rid), path)

    @app.post("/sandboxes/{rid}/rename", status_code=204)
    async def rename(rid: str, body: dict) -> None:
        await backend.rename(SandboxHandle(id=rid), body["src"], body["dst"])

    @app.post("/sandboxes/{rid}/exec")
    async def exec_(rid: str, body: dict) -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            chunks: list[bytes] = []
            try:
                result = await backend.exec(
                    SandboxHandle(id=rid), body["cmd"], on_output=chunks.append
                )
            except Exception as exc:  # noqa: BLE001 — relayed in-band as an error frame
                yield (
                    json.dumps({"error": type(exc).__name__, "detail": str(exc)}) + "\n"
                ).encode()
                return
            for c in chunks:
                yield (json.dumps({"o": base64.b64encode(c).decode()}) + "\n").encode()
            yield (
                json.dumps(
                    {
                        "exit": result.exit_code,
                        "out": base64.b64encode(result.stdout).decode(),
                        "err": base64.b64encode(result.stderr).decode(),
                    }
                )
                + "\n"
            ).encode()

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    return app


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()  # nothing listens here now ⇒ connection refused
    return port


@pytest.fixture
async def http_sandbox():
    backend = MockSandbox()
    app = _fake_host(backend, _ADVERTISE)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport) as client:
        yield HttpSandbox(base_url=_ADVERTISE, client=client)


async def test_create_returns_unique_handles(http_sandbox: HttpSandbox):
    h1 = await http_sandbox.create(SandboxSpec())
    h2 = await http_sandbox.create(SandboxSpec())
    assert h1.id != h2.id


def test_handle_for_id_is_none_345(http_sandbox: HttpSandbox):
    # #345: the HTTP host mints its own pod-scoped handles and isn't addressable
    # by a caller-stable id, so there's nothing to derive → None (a pod with no
    # session reads the durable snapshot, the prior behaviour for this backend).
    assert http_sandbox.handle_for_id("anything") is None


async def test_kill_then_reuse_raises_sandbox_not_found(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    await http_sandbox.kill(h)
    # The host's backend no longer knows the handle ⇒ 404 → SandboxNotFound.
    with pytest.raises(SandboxNotFound):
        await http_sandbox.kill(h)


async def test_upload_download_roundtrip(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    await http_sandbox.upload(h, b"hello \x00 world", "/data/x.bin")
    assert await http_sandbox.download(h, "/data/x.bin") == b"hello \x00 world"


async def test_upload_file_download_to_file_roundtrip(http_sandbox: HttpSandbox, tmp_path):
    h = await http_sandbox.create(SandboxSpec())
    src = tmp_path / "src.bin"
    src.write_bytes(b"staged \x00 bytes")
    await http_sandbox.upload_file(h, src, "/data/x.bin")
    assert await http_sandbox.download(h, "/data/x.bin") == b"staged \x00 bytes"
    out = tmp_path / "out.bin"
    await http_sandbox.download_to_file(h, "/data/x.bin", out)
    assert out.read_bytes() == b"staged \x00 bytes"


async def test_download_missing_raises_file_not_found(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    with pytest.raises(FileNotFoundError):
        await http_sandbox.download(h, "/nope.txt")


async def test_exists_reflects_uploaded_file(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    assert await http_sandbox.exists(h, "/a.txt") is False
    await http_sandbox.upload(h, b"x", "/a.txt")
    assert await http_sandbox.exists(h, "/a.txt") is True


async def test_walk_lists_files_with_versions(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    await http_sandbox.upload(h, b"aaa", "/dir/a.txt")
    await http_sandbox.upload(h, b"bb", "/dir/b.txt")
    entries = await http_sandbox.walk(h, "/dir")
    by_path = {e.path: e for e in entries}
    assert set(by_path) == {"/dir/a.txt", "/dir/b.txt"}
    assert by_path["/dir/a.txt"].size == 3
    assert by_path["/dir/a.txt"].version  # non-empty change-stamp (mirror diff)


async def test_delete_removes_file(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    await http_sandbox.upload(h, b"x", "/a.txt")
    await http_sandbox.delete(h, "/a.txt")
    assert await http_sandbox.exists(h, "/a.txt") is False


async def test_delete_missing_raises_file_not_found(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    with pytest.raises(FileNotFoundError):
        await http_sandbox.delete(h, "/nope.txt")


async def test_mkdir_succeeds(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    await http_sandbox.mkdir(h, "/newdir")  # no raise


async def test_rmdir_removes_subtree(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    await http_sandbox.upload(h, b"x", "/d/a.txt")
    await http_sandbox.rmdir(h, "/d")
    assert await http_sandbox.exists(h, "/d/a.txt") is False


async def test_rmdir_missing_raises_file_not_found(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    with pytest.raises(FileNotFoundError):
        await http_sandbox.rmdir(h, "/nope")


async def test_rename_moves_file(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    await http_sandbox.upload(h, b"x", "/a.txt")
    await http_sandbox.rename(h, "/a.txt", "/b.txt")
    assert await http_sandbox.download(h, "/b.txt") == b"x"


async def test_rename_missing_raises_file_not_found(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    with pytest.raises(FileNotFoundError):
        await http_sandbox.rename(h, "/nope.txt", "/b.txt")


async def test_exec_returns_result_and_streams_output(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    chunks: list[bytes] = []
    result = await http_sandbox.exec(h, ["echo", "hi"], on_output=chunks.append)
    assert result.exit_code == 0
    assert result.stdout == b"hi\n"
    assert b"".join(chunks) == b"hi\n"  # forwarded live, chunk by chunk


async def test_exec_nonzero_exit_without_sink(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    result = await http_sandbox.exec(h, ["false"])
    assert result.exit_code == 1


async def test_exec_unknown_handle_raises_via_error_frame(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    await http_sandbox.kill(h)
    with pytest.raises(SandboxNotFound):
        await http_sandbox.exec(h, ["echo", "hi"])


async def test_exec_output_without_sink_is_dropped(http_sandbox: HttpSandbox):
    # An `o` frame arrives but no on_output is given ⇒ chunk is simply not
    # forwarded; the final ExecResult still carries the full stdout.
    h = await http_sandbox.create(SandboxSpec())
    result = await http_sandbox.exec(h, ["echo", "hello"])
    assert result.stdout == b"hello\n"


async def test_expose_port_not_implemented(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    with pytest.raises(NotImplementedError):
        await http_sandbox.expose_port(h, 8080)


async def test_dead_pod_maps_to_sandbox_not_found():
    """A connection failure (scaled-down/crashed host pod) is indistinguishable
    from a killed sandbox — both surface as SandboxNotFound so the caller
    recreates from the snapshot. Covers both the request and the stream path."""
    dead = f"http://127.0.0.1:{_closed_port()}"
    h = SandboxHandle(id=_encode_handle(dead, "rid"))
    async with httpx.AsyncClient() as client:
        sb = HttpSandbox(base_url=dead, client=client)
        with pytest.raises(SandboxNotFound):
            await sb.kill(h)  # _request transport-error path
        with pytest.raises(SandboxNotFound):
            await sb.exec(h, ["echo", "x"])  # stream transport-error path


def _stub_host(stream: bytes) -> FastAPI:
    """A host whose /exec returns a hand-crafted NDJSON body (for edge framing)."""
    app = FastAPI()

    @app.post("/sandboxes")
    async def create() -> dict[str, str]:
        return {"pod_url": _ADVERTISE, "remote_id": "r1"}

    @app.post("/sandboxes/{rid}/exec")
    async def exec_(rid: str) -> StreamingResponse:
        async def gen():
            yield stream

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    return app


async def test_exec_stream_truncated_before_final_frame_raises():
    # Blank line (ignored) + one `o` frame, then EOF with no exit/error frame ⇒
    # the pod died mid-exec ⇒ SandboxNotFound, but the live chunk still arrived.
    body = b"\n" + b'{"o":"' + base64.b64encode(b"partial").decode().encode() + b'"}\n'
    app = _stub_host(body)
    async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client)
        h = await sb.create(SandboxSpec())
        chunks: list[bytes] = []
        with pytest.raises(SandboxNotFound):
            await sb.exec(h, ["x"], on_output=chunks.append)
        assert chunks == [b"partial"]


async def test_constructs_its_own_client_for_both_timeout_modes():
    for read_timeout in (0.0, 120.0):
        sb = HttpSandbox(base_url="http://x", read_timeout=read_timeout)
        assert sb._client is not None
        await sb._client.aclose()
