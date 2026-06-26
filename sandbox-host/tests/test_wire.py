"""Wire server — every file/exec endpoint of `app.py` over an in-process ASGI
transport with a `MockSandbox` injected. This is the host's half of the wire
contract (`docs/sandbox-host-wire.md`); the app's `HttpSandbox` client is tested
against an independent fake in the app repo. No network, no privilege.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from httpx import ASGITransport

from sandbox_host.app import make_host_app
from sandbox_host.mock import MockSandbox

_ADVERTISE = "http://sandbox-host-pod:8000"


@pytest.fixture
async def client():
    app = make_host_app(MockSandbox(), advertise_url=_ADVERTISE)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h") as c:
        yield c


async def _create(c: httpx.AsyncClient) -> str:
    r = await c.post("/sandboxes", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["pod_url"] == _ADVERTISE  # the pod advertises its own routable URL
    return body["remote_id"]


async def test_create_reply_carries_advertise_url_and_remote_id(client):
    rid = await _create(client)
    assert rid


async def test_kill_then_kill_again_maps_to_404_sandbox_not_found(client):
    rid = await _create(client)
    assert (await client.delete(f"/sandboxes/{rid}")).status_code == 204
    r = await client.delete(f"/sandboxes/{rid}")
    assert r.status_code == 404
    assert r.json()["error"] == "SandboxNotFound"


async def test_upload_download_roundtrip_raw_bytes(client):
    rid = await _create(client)
    payload = b"hello \x00 world"
    assert (
        await client.put(f"/sandboxes/{rid}/file", params={"path": "/x.bin"}, content=payload)
    ).status_code == 204
    r = await client.get(f"/sandboxes/{rid}/file", params={"path": "/x.bin"})
    assert r.status_code == 200
    assert r.content == payload


async def test_download_missing_maps_to_file_not_found(client):
    rid = await _create(client)
    r = await client.get(f"/sandboxes/{rid}/file", params={"path": "/nope.txt"})
    assert r.status_code == 404
    assert r.json()["error"] == "FileNotFoundError"


async def test_exists_reflects_uploaded_file(client):
    rid = await _create(client)
    assert (await client.get(f"/sandboxes/{rid}/exists", params={"path": "/a"})).json() == {
        "exists": False
    }
    await client.put(f"/sandboxes/{rid}/file", params={"path": "/a"}, content=b"x")
    assert (await client.get(f"/sandboxes/{rid}/exists", params={"path": "/a"})).json() == {
        "exists": True
    }


async def test_walk_lists_files_with_versions(client):
    rid = await _create(client)
    await client.put(f"/sandboxes/{rid}/file", params={"path": "/dir/a.txt"}, content=b"aaa")
    r = await client.get(f"/sandboxes/{rid}/walk", params={"root": "/dir"})
    entries = r.json()["entries"]
    assert entries[0]["path"] == "/dir/a.txt"
    assert entries[0]["size"] == 3
    assert entries[0]["version"]


async def test_delete_removes_file_and_missing_is_404(client):
    rid = await _create(client)
    await client.put(f"/sandboxes/{rid}/file", params={"path": "/a"}, content=b"x")
    assert (await client.delete(f"/sandboxes/{rid}/file", params={"path": "/a"})).status_code == 204
    r = await client.delete(f"/sandboxes/{rid}/file", params={"path": "/a"})
    assert r.status_code == 404
    assert r.json()["error"] == "FileNotFoundError"


async def test_mkdir_succeeds(client):
    rid = await _create(client)
    assert (
        await client.post(f"/sandboxes/{rid}/mkdir", json={"path": "/newdir"})
    ).status_code == 204


async def test_rmdir_removes_subtree_and_missing_is_404(client):
    rid = await _create(client)
    await client.put(f"/sandboxes/{rid}/file", params={"path": "/d/a"}, content=b"x")
    assert (await client.delete(f"/sandboxes/{rid}/dir", params={"path": "/d"})).status_code == 204
    r = await client.delete(f"/sandboxes/{rid}/dir", params={"path": "/d"})
    assert r.status_code == 404


async def test_rename_moves_file_and_missing_is_404(client):
    rid = await _create(client)
    await client.put(f"/sandboxes/{rid}/file", params={"path": "/a"}, content=b"x")
    assert (
        await client.post(f"/sandboxes/{rid}/rename", json={"src": "/a", "dst": "/b"})
    ).status_code == 204
    assert (await client.get(f"/sandboxes/{rid}/file", params={"path": "/b"})).content == b"x"
    r = await client.post(f"/sandboxes/{rid}/rename", json={"src": "/nope", "dst": "/c"})
    assert r.status_code == 404


def _frames(body: bytes) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line]


async def test_exec_streams_o_frames_then_final_exit_frame(client):
    rid = await _create(client)
    async with client.stream(
        "POST", f"/sandboxes/{rid}/exec", json={"cmd": ["echo", "hi"]}
    ) as resp:
        assert resp.status_code == 200
        body = b"".join([chunk async for chunk in resp.aiter_bytes()])
    frames = _frames(body)
    # one live `o` frame, then the final separated exit/out/err frame
    assert base64.b64decode(frames[0]["o"]) == b"hi\n"
    assert frames[-1]["exit"] == 0
    assert base64.b64decode(frames[-1]["out"]) == b"hi\n"
    assert base64.b64decode(frames[-1]["err"]) == b""


async def test_exec_nonzero_exit_frame(client):
    rid = await _create(client)
    async with client.stream("POST", f"/sandboxes/{rid}/exec", json={"cmd": ["false"]}) as resp:
        body = b"".join([chunk async for chunk in resp.aiter_bytes()])
    assert _frames(body)[-1]["exit"] == 1


async def test_exec_on_unknown_handle_emits_in_band_error_frame(client):
    # The response status is already 200 (streaming started), so a backend error
    # must travel in-band as a final {"error","detail"} frame.
    rid = await _create(client)
    await client.delete(f"/sandboxes/{rid}")
    async with client.stream(
        "POST", f"/sandboxes/{rid}/exec", json={"cmd": ["echo", "hi"]}
    ) as resp:
        assert resp.status_code == 200
        body = b"".join([chunk async for chunk in resp.aiter_bytes()])
    last = _frames(body)[-1]
    assert last["error"] == "SandboxNotFound"
    assert last["detail"]
