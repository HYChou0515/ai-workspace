"""The host's half of `as_tool` (docs/plan-tool-identity-secrets.md P1): the
`/exec` body carries which third-party tool a command launches, and the host
hands it to the backend that decides the identity. The app's half is
`tests/sandbox/test_http.py`.

Asserted on both halves for the reason `exec_timeout` is: a host that parsed the
key and dropped it would run every third-party tool as the sandbox uid, which
looks exactly like working.
"""

from __future__ import annotations

import httpx
from httpx import ASGITransport

from sandbox_host.app import make_host_app
from sandbox_host.config import SandboxHostSettings
from sandbox_host.mock import MockSandbox
from sandbox_host.service import build_sandbox

_ADVERTISE = "http://sandbox-host-pod:8000"


async def _exec(body: dict[str, object]) -> MockSandbox:
    backend = MockSandbox()
    app = make_host_app(backend, advertise_url=_ADVERTISE)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h") as client:
        rid = (await client.post("/sandboxes", json={})).json()["remote_id"]
        async with client.stream("POST", f"/sandboxes/{rid}/exec", json=body) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_lines():
                pass
    return backend


async def test_exec_hands_the_tool_name_to_the_backend():
    backend = await _exec({"cmd": ["../.tools/erp/launch", "q", "{}"], "as_tool": "erp"})
    assert backend.exec_as_tools[-1] == "erp"


async def test_exec_from_an_older_client_runs_as_the_sandbox():
    backend = await _exec({"cmd": ["true"]})
    assert backend.exec_as_tools[-1] is None


async def test_healthz_says_this_host_runs_tools_as_their_own_identity():
    app = make_host_app(MockSandbox(), advertise_url=_ADVERTISE)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h") as client:
        body = (await client.get("/healthz")).json()
    assert "tool-identity" in body["capabilities"]


def test_the_production_host_reserves_the_pools_first_id_as_the_tool_group(tmp_path):
    """The tool group is a number the operator already set aside — the bottom of
    the sandbox uid range — so it needs no setting and can collide with nothing
    on the host. The pool starts one above it, so no sandbox ever holds it."""
    hs = SandboxHostSettings(
        uid_min=200000, uid_max=200003, cgroup_root=str(tmp_path / "cg"), root=str(tmp_path)
    )
    sb = build_sandbox(hs)
    assert sb._tool_gid == 200000
    handed_out = {sb._pool.alloc()[0] for _ in range(3)}
    assert handed_out == {200001, 200002, 200003}
