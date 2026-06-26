"""Wire-contract conformance over REAL HTTP (integration).

Boots the host's wire server in a uvicorn subprocess (backed by a non-isolating
`LocalProcessSandbox`, so it needs neither root nor cgroups) and drives the full
create → upload → exec → download → walk → kill cycle over a real TCP socket with
httpx — proving the server honours `docs/sandbox-host-wire.md` end-to-end, not
just under the in-process ASGI transport. `@integration`: excluded from CI; the
app's `HttpSandbox` is the production client and is tested app-side against the
same contract.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import sys

import httpx
import pytest

pytestmark = pytest.mark.integration

_SERVER = """
import os, uvicorn
from sandbox_host.app import make_host_app
from sandbox_host.local_process import LocalProcessSandbox

port = int(os.environ["PORT"])
app = make_host_app(
    LocalProcessSandbox(isolate=False), advertise_url=f"http://127.0.0.1:{port}"
)
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture
async def base_url():
    port = _free_port()
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _SERVER,
        env={"PORT": str(port), "PATH": __import__("os").environ.get("PATH", "")},
    )
    url = f"http://127.0.0.1:{port}"
    try:
        # Poll until the server accepts connections (or give up loudly).
        async with httpx.AsyncClient() as c:
            for _ in range(150):
                try:
                    if (await c.get(f"{url}/healthz", timeout=1.0)).status_code == 200:
                        break
                except httpx.TransportError:
                    await asyncio.sleep(0.1)
            else:
                raise RuntimeError("sandbox host subprocess did not become ready")
        yield url
    finally:
        proc.terminate()
        await proc.wait()


def _frames(body: bytes) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line]


async def test_full_cycle_over_real_http(base_url):
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as c:
        # create
        r = await c.post("/sandboxes", json={})
        assert r.status_code == 200
        rid = r.json()["remote_id"]
        assert r.json()["pod_url"] == base_url

        # upload → exists → download (raw bytes round-trip)
        assert (
            await c.put(f"/sandboxes/{rid}/file", params={"path": "/d/x.txt"}, content=b"hi\n")
        ).status_code == 204
        assert (await c.get(f"/sandboxes/{rid}/exists", params={"path": "/d/x.txt"})).json() == {
            "exists": True
        }
        assert (
            await c.get(f"/sandboxes/{rid}/file", params={"path": "/d/x.txt"})
        ).content == b"hi\n"

        # walk
        entries = (await c.get(f"/sandboxes/{rid}/walk", params={"root": "/"})).json()["entries"]
        assert any(e["path"] == "/d/x.txt" and e["size"] == 3 for e in entries)

        # exec: real subprocess, NDJSON streaming over the wire
        async with c.stream(
            "POST", f"/sandboxes/{rid}/exec", json={"cmd": ["cat", "d/x.txt"]}
        ) as resp:
            assert resp.status_code == 200
            body = b"".join([chunk async for chunk in resp.aiter_bytes()])
        frames = _frames(body)
        assert frames[-1]["exit"] == 0
        assert base64.b64decode(frames[-1]["out"]) == b"hi\n"

        # kill, then a second kill maps to 404 SandboxNotFound
        assert (await c.delete(f"/sandboxes/{rid}")).status_code == 204
        gone = await c.delete(f"/sandboxes/{rid}")
        assert gone.status_code == 404
        assert gone.json()["error"] == "SandboxNotFound"
