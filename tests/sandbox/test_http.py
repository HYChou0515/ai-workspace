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

import asyncio
import base64
import json
import socket
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from httpx import ASGITransport

from workspace_app.sandbox.http_client import (
    HttpSandbox,
    IoRetryPolicy,
    _decode_handle,
    _encode_handle,
)
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import (
    EnforcedLimits,
    SandboxBusy,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
)

_ADVERTISE = "http://sandbox-host-pod:8000"


def _fake_host(backend: MockSandbox, advertise_url: str) -> FastAPI:
    """A minimal ASGI mirror of the sandbox-host wire contract, backed by an
    in-memory `MockSandbox`. Independent of the real host package — it exists so
    the client can be exercised against the contract the app defines."""
    app = FastAPI()
    app.state.created_item_ids = []  # #492: item_ids seen by create
    app.state.persisted = []  # #492: (rid, delete) seen by persist
    app.state.created_tools = []  # #674: `{name: sha}` seen by create
    # The resource ceilings the host reads off a create. Modelled here because a
    # test that only asserted "our side sent something" would be immune to the
    # one regression that matters — the app and the host disagreeing about the
    # field names. These are the names `sandbox_host.app._CreateBody` declares.
    app.state.created_limits = []
    app.state.resolved = []  # #674: tool declarations seen by /tools/resolve

    @app.exception_handler(SandboxNotFound)
    async def _nf(_r: Request, exc: SandboxNotFound) -> JSONResponse:
        return _err(exc)

    @app.exception_handler(FileNotFoundError)
    async def _fnf(_r: Request, exc: FileNotFoundError) -> JSONResponse:
        return _err(exc)

    @app.post("/sandboxes")
    async def create(body: dict) -> dict[str, str]:
        app.state.created_item_ids.append(body.get("item_id"))  # #492: capture for assertions
        app.state.created_tools.append(body.get("tools"))  # #674
        app.state.created_limits.append(
            (body.get("cpu_cores"), body.get("memory_bytes"), body.get("pids_max"))
        )
        h = await backend.create(SandboxSpec())
        return {"pod_url": advertise_url, "remote_id": h.id}

    @app.post("/tools/resolve")
    async def resolve_tools(body: dict) -> dict:
        app.state.resolved.append(body.get("tools"))
        return {
            "tools": {
                name: {
                    "sha": "a" * 64,
                    "version": "1.4.2",
                    "stale": False,
                    "commands": [],
                }
                for name in body.get("tools", {})
            },
            "refused": {},
        }

    @app.post("/sandboxes/{rid}/persist", status_code=204)
    async def persist(rid: str, body: dict) -> None:
        # #492: record (rid, delete) so the client's persist call can be asserted.
        backend._require(SandboxHandle(id=rid))  # raise SandboxNotFound for a dead handle
        app.state.persisted.append((rid, bool(body.get("delete", False))))

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

    @app.post("/sandboxes/{rid}/mark-ready", status_code=204)
    async def mark_ready(rid: str) -> None:
        await backend.mark_ready(SandboxHandle(id=rid))

    @app.get("/sandboxes/{rid}/ready")
    async def is_ready(rid: str) -> dict[str, bool]:
        return {"ready": await backend.is_ready(SandboxHandle(id=rid))}

    @app.get("/sandboxes/{rid}/walk")
    async def walk(rid: str, root: str) -> dict[str, list]:
        walked = await backend.walk(SandboxHandle(id=rid), root)
        return {
            "entries": [
                {"path": e.path, "size": e.size, "version": e.version} for e in walked.files
            ],
            "dirs": walked.dirs,
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
                    SandboxHandle(id=rid),
                    body["cmd"],
                    on_output=chunks.append,
                    # The real host reads this key too; a mirror that ignored it
                    # would let the client "send" an env nothing ever applied.
                    env=body.get("env"),
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

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        # The host publishes the ceilings it applies to a sandbox whose spec
        # states nothing — its own `SANDBOX_HOST_*`. Only the host knows them,
        # and without them the app charges an owner nothing for a sandbox the
        # host really did cap.
        return {
            "status": "ok",
            "capabilities": ["resource-defaults"],
            "defaults": {"cpu_cores": 1.5, "memory_bytes": 768 * 1024**2},
        }

    return app


def _err(exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": type(exc).__name__, "detail": str(exc)})


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


async def test_an_unstated_ceiling_is_charged_at_what_the_host_enforces(
    http_sandbox: HttpSandbox,
):
    """`None` in the spec means "host, use your own" — and it does. The app
    cannot read another service's environment, so the host advertises it; before
    that, an owner was charged nothing for a sandbox really held under a cgroup."""
    assert await http_sandbox.effective_limits(SandboxSpec()) == EnforcedLimits(
        cpu_cores=1.5, memory_bytes=768 * 1024**2
    )


@pytest.fixture
async def sandbox_and_backend():
    """Like `http_sandbox` but also hands back the backend the fake host wraps,
    so a test can assert what actually landed on the far side."""
    backend = MockSandbox()
    app = _fake_host(backend, _ADVERTISE)
    async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
        yield HttpSandbox(base_url=_ADVERTISE, client=client), backend


@pytest.fixture
async def host_and_client():
    """Like `http_sandbox` but also hands back the fake host app so #492 tests
    can assert what item_id create sent and what persist recorded."""
    backend = MockSandbox()
    app = _fake_host(backend, _ADVERTISE)
    async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
        yield HttpSandbox(base_url=_ADVERTISE, client=client), app


async def test_create_passes_the_item_id_to_the_host(host_and_client):
    """#492: the item id (sandbox_id) is forwarded as item_id so the host can
    restore/persist that item's durable archive."""
    sandbox, app = host_and_client
    await sandbox.create(SandboxSpec(), sandbox_id="item-42")
    assert app.state.created_item_ids == ["item-42"]


async def test_create_without_item_id_sends_none(host_and_client):
    sandbox, app = host_and_client
    await sandbox.create(SandboxSpec())
    assert app.state.created_item_ids == [None]


async def test_persist_posts_delete_flag(host_and_client):
    sandbox, app = host_and_client
    h = await sandbox.create(SandboxSpec(), sandbox_id="item-42")
    await sandbox.persist(h, delete=True)
    await sandbox.persist(h, delete=False)
    _, remote_id = _decode_handle(h)
    assert app.state.persisted == [(remote_id, True), (remote_id, False)]


async def test_persist_on_dead_handle_raises_sandbox_not_found(host_and_client):
    sandbox, app = host_and_client
    h = await sandbox.create(SandboxSpec(), sandbox_id="item-42")
    await sandbox.kill(h)
    with pytest.raises(SandboxNotFound):
        await sandbox.persist(h, delete=True)


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


async def test_mark_ready_then_is_ready_roundtrip_366(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    assert await http_sandbox.is_ready(h) is False
    await http_sandbox.mark_ready(h)
    assert await http_sandbox.is_ready(h) is True


async def test_walk_lists_files_with_versions(http_sandbox: HttpSandbox):
    h = await http_sandbox.create(SandboxSpec())
    await http_sandbox.upload(h, b"aaa", "/dir/a.txt")
    await http_sandbox.upload(h, b"bb", "/dir/b.txt")
    entries = (await http_sandbox.walk(h, "/dir")).files
    by_path = {e.path: e for e in entries}
    assert set(by_path) == {"/dir/a.txt", "/dir/b.txt"}
    assert by_path["/dir/a.txt"].size == 3
    assert by_path["/dir/a.txt"].version  # non-empty change-stamp (mirror diff)


async def test_walk_carries_the_directory_half_over_the_wire(http_sandbox: HttpSandbox):
    """An empty folder reaches the app ONLY through this half — it appears in no
    file path, so nothing on the far side can reconstruct it from `entries`."""
    h = await http_sandbox.create(SandboxSpec())
    await http_sandbox.mkdir(h, "/empty/deep")

    walked = await http_sandbox.walk(h, "/")

    assert walked.files == []
    assert walked.dirs == ["/empty", "/empty/deep"]


async def test_walk_degrades_when_the_host_predates_the_directory_half():
    """A host that has not been redeployed omits `dirs` entirely. That must read
    as "this host cannot report folders" — the old files-only behaviour — not as
    a KeyError that takes the whole file tree down."""

    class _OldHost(HttpSandbox):
        async def _io_request(self, handle, method, suffix, **kwargs):  # type: ignore[override]
            class _Resp:
                @staticmethod
                def json():
                    return {"entries": [{"path": "/a.txt", "size": 1, "version": "v"}]}

            return _Resp()

    walked = await _OldHost(base_url="http://unused").walk(SandboxHandle(id="x"), "/")

    assert [e.path for e in walked.files] == ["/a.txt"]
    assert walked.dirs == []


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


# ---- #492: busy (timeout) vs gone (connect-fail/404) on the idempotent ops ----

# A small, fast policy so the escalation is easy to read in the assertions.
_FAST_IO = IoRetryPolicy(
    attempts=3,
    timeout_base_s=1.0,
    timeout_factor=2.0,
    timeout_cap_s=3.0,
    backoff_base_s=0.5,
    backoff_factor=2.0,
    backoff_cap_s=2.0,
)


def _rid_handle() -> SandboxHandle:
    return SandboxHandle(id=_encode_handle(_ADVERTISE, "rid"))


async def test_busy_op_retries_with_escalating_deadline_then_raises_busy():
    """A read timeout means the host is BUSY, not gone: the op retries with a
    longer per-attempt read deadline (so the busy host gets room, not another
    short hammer) and a longer backoff, both capped; after `attempts` the last
    SandboxBusy propagates so the caller fails loud (never rebuild / cold-write)."""
    reads: list[float | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reads.append(request.extensions["timeout"]["read"])
        raise httpx.ReadTimeout("slow", request=request)

    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client, io_retry=_FAST_IO, sleep=_sleep)
        with pytest.raises(SandboxBusy):
            await sb.exists(_rid_handle(), "/")

    assert reads == [1.0, 2.0, 3.0]  # escalating, capped at timeout_cap_s
    assert sleeps == [0.5, 1.0]  # escalating backoff between the 3 attempts


async def test_busy_op_recovers_on_a_later_attempt():
    """If the host stops being busy, a retry succeeds — no error surfaces."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"exists": True})

    async def _sleep(_s: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client, io_retry=_FAST_IO, sleep=_sleep)
        assert await sb.exists(_rid_handle(), "/") is True
    assert calls["n"] == 3


async def test_connect_failure_is_not_retried_and_maps_to_not_found():
    """A connection failure means the pod is GONE (deleted): map straight to
    SandboxNotFound so the caller rebuilds — do NOT burn the busy-retry budget on
    it (retrying a dead pod is pointless)."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client, io_retry=_FAST_IO)
        with pytest.raises(SandboxNotFound):
            await sb.exists(_rid_handle(), "/")
    assert calls["n"] == 1  # gone → rebuild, never retried


async def test_exec_read_timeout_is_busy_not_a_missing_sandbox():
    """A read timeout during `exec` means the host is BUSY, not gone.

    `_request` already gets this right by catching `httpx.TimeoutException`
    BEFORE `TransportError` (it is a subclass). `exec` did not, so an operator
    who set a non-zero `read_timeout` shorter than the host's `exec_timeout` got
    `SandboxNotFound` — which `registry` treats as "the sandbox is gone, rebuild
    it". That is the split-brain the `SandboxBusy` docstring exists to forbid:
    a fresh sandbox is built while the original command runs on to completion in
    the old one.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("still running", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client)
        with pytest.raises(SandboxBusy):
            await sb.exec(_rid_handle(), ["sleep", "600"])


async def test_exec_transport_failure_is_still_a_missing_sandbox():
    """A genuine transport failure (the pod went away) must keep mapping to
    SandboxNotFound, so the rebuild path still works."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("pod gone", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client)
        with pytest.raises(SandboxNotFound):
            await sb.exec(_rid_handle(), ["echo", "hi"])


async def test_create_carries_the_tools_the_sandbox_should_mount(host_and_client):
    """#674: the shas resolved at the start of this turn travel with `create`,
    so the bundle the sandbox mounts is the one the model was told about."""
    sandbox, app = host_and_client

    await sandbox.create(SandboxSpec(tools={"wafer-history": "a" * 64}), "item-1")

    assert app.state.created_tools == [{"wafer-history": "a" * 64}]


async def test_resolve_tools_asks_the_host_and_hands_back_its_answer(host_and_client):
    # The app holds no artifact-store credential: this call is the only way it
    # learns a third-party tool's sha or its schema.
    sandbox, app = host_and_client

    answer = await sandbox.resolve_tools({"wafer-history": "https://gitlab/m"})

    assert app.state.resolved == [{"wafer-history": "https://gitlab/m"}]
    assert answer["tools"]["wafer-history"]["sha"] == "a" * 64
    assert answer["refused"] == {}


async def test_the_hosted_backend_advertises_that_it_can_resolve_tools(http_sandbox):
    # How `resolve_external_tools` tells a deployment with an artifact store
    # from one without, so local dev reports "unavailable" instead of silence.
    assert http_sandbox.resolves_tools is True


async def test_exec_carries_the_callers_env_across_the_hop():
    """The client names the item's variables per call; they have to survive the
    HTTP hop or a hosted deployment gets the feature with nothing in it. This is
    the app's half — `sandbox-host/tests/test_wire.py` asserts the host's."""
    backend = MockSandbox()
    app = _fake_host(backend, _ADVERTISE)
    async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client)
        h = await sb.create(SandboxSpec())
        await sb.exec(h, ["true"], env={"API_KEY": "sk-1"})
    assert backend.exec_envs[-1] == {"API_KEY": "sk-1"}


async def test_no_env_sends_the_same_request_it_always_did():
    """An older host must keep working: omit the key entirely rather than send
    an empty object it would have to know to ignore."""
    seen: list[dict] = []
    backend = MockSandbox()
    app = _fake_host(backend, _ADVERTISE)

    @app.middleware("http")
    async def _capture(request, call_next):
        if request.url.path.endswith("/exec"):
            seen.append(json.loads(await request.body()))
        return await call_next(request)

    async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client)
        h = await sb.create(SandboxSpec())
        await sb.exec(h, ["true"])
    assert seen[-1] == {"cmd": ["true"]}


async def test_create_sends_the_items_resource_ceilings():
    """P3: the App-resolved ceilings travel with `create`, under the names the
    real host reads (`sandbox_host.app._CreateBody`)."""
    backend = MockSandbox()
    app = _fake_host(backend, _ADVERTISE)
    async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client)
        await sb.create(SandboxSpec(cpu_cores=2.0, memory_bytes=1024, pids_max=64))
    assert app.state.created_limits == [(2.0, 1024, 64)]


async def test_create_states_nothing_when_the_app_has_no_limits():
    """An unstated ceiling must go on the wire as null, not as 0: the host reads
    null as "use my default" and 0 as "explicitly unbounded", and sending the
    wrong one would hand every sandbox unlimited memory."""
    backend = MockSandbox()
    app = _fake_host(backend, _ADVERTISE)
    async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client)
        await sb.create(SandboxSpec())
    assert app.state.created_limits == [(None, None, None)]


# ── the host-defaults cache (review round 1) ──────────────────────────────


def _flaky_host(fail_first: int, cpu: float = 1.5, mem: int = 768 * 1024**2):
    """A host whose `/healthz` fails the first `fail_first` times. Records how
    many times it was actually asked."""
    state = {"calls": 0, "cpu": cpu, "mem": mem}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] <= fail_first:
            raise httpx.ConnectError("host down", request=request)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "capabilities": ["resource-defaults"],
                "defaults": {"cpu_cores": state["cpu"], "memory_bytes": state["mem"]},
            },
        )

    return state, handler


async def test_a_momentary_host_blip_does_not_charge_zero_for_ever():
    """The failure must NOT be remembered.

    Caching it pinned the whole process to "this sandbox costs nothing" — the
    exact defect this backend reports `effective_limits` to fix, except silent
    and only curable by restarting the pod. The blip is likeliest during the
    sandbox-host rollout this change requires."""
    state, handler = _flaky_host(fail_first=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client)
        first = await sb.effective_limits(SandboxSpec())
        second = await sb.effective_limits(SandboxSpec())

    assert first == EnforcedLimits(cpu_cores=None, memory_bytes=None)  # nothing invented
    assert second == EnforcedLimits(cpu_cores=1.5, memory_bytes=768 * 1024**2)
    assert state["calls"] == 2  # it asked again rather than trusting the failure


async def test_a_redeployed_host_is_picked_up_without_restarting_the_app():
    """`sandbox-host` and the app are separate deployments, so "the next process"
    is not a real cure: rolling the host does not restart the app pods. The
    answer is remembered for a bounded time, not for ever."""
    clock = {"t": 0.0}
    state, handler = _flaky_host(fail_first=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sb = HttpSandbox(
            base_url=_ADVERTISE,
            client=client,
            host_defaults_ttl=60.0,
            monotonic=lambda: clock["t"],
        )
        assert (await sb.effective_limits(SandboxSpec())).cpu_cores == 1.5
        clock["t"] = 30.0
        state["cpu"] = 4.0  # host redeployed with a bigger ceiling
        assert (await sb.effective_limits(SandboxSpec())).cpu_cores == 1.5  # still cached
        assert state["calls"] == 1
        clock["t"] = 61.0
        assert (await sb.effective_limits(SandboxSpec())).cpu_cores == 4.0
        assert state["calls"] == 2


async def test_concurrent_first_calls_ask_once_and_a_failure_cannot_win():
    """Two cold callers raced the one-shot fetch, and whichever finished LAST
    wrote the cache — so a failing probe could overwrite an answer the host had
    already given correctly. A cold pod waking several items at once is exactly
    that shape."""
    state, handler = _flaky_host(fail_first=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sb = HttpSandbox(base_url=_ADVERTISE, client=client)
        got = await asyncio.gather(*(sb.effective_limits(SandboxSpec()) for _ in range(5)))

    assert all(g == EnforcedLimits(cpu_cores=1.5, memory_bytes=768 * 1024**2) for g in got)
    assert state["calls"] == 1  # serialised, not five races
