"""HttpSandbox — a `Sandbox` that delegates to a remote sandbox host over HTTP.

A faithful HTTP wrapper of `LocalProcessSandbox`: the host runs each command in
its own pod, this client just marshals the 12 protocol methods over the wire.

Routing (HPA-ready, stateless): `create` hits the host's ClusterIP Service; the
chosen pod replies with its OWN directly-addressable URL + a local handle id,
which this client packs into the opaque `SandboxHandle.id`. Every other method
decodes that and connects straight to the owning pod (bypassing the LB), so any
app replica routes correctly with no shared state. A dead pod surfaces as
`SandboxNotFound`, and the caller recreates the sandbox from the FileStore.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx

from .protocol import (
    ExecResult,
    FileEntry,
    OutputSink,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
)

# Maps the host's structured `{"error": <type>}` discriminator back to the
# exception type the Sandbox Protocol promises callers.
_ERRORS: dict[str, type[Exception]] = {
    "SandboxNotFound": SandboxNotFound,
    "FileNotFoundError": FileNotFoundError,
}


def _encode_handle(pod_url: str, remote_id: str) -> str:
    raw = json.dumps({"u": pod_url, "r": remote_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_handle(handle: SandboxHandle) -> tuple[str, str]:
    raw = base64.urlsafe_b64decode(handle.id.encode())
    data = json.loads(raw)
    return data["u"], data["r"]


class HttpSandbox:
    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        read_timeout: float = 0.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # read_timeout <= 0 ⇒ no read deadline; the host's own exec/idle timeout
        # is the real bound (a long command must not trip an HTTP read timeout).
        read = None if read_timeout <= 0 else read_timeout
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=read, write=30.0, pool=10.0)
        )

    async def _request(
        self, handle: SandboxHandle, method: str, suffix: str, **kwargs: Any
    ) -> httpx.Response:
        """Decode the handle, connect straight to the owning pod, and map errors.

        A dead pod (connection failure) is indistinguishable from a killed
        sandbox to the caller — both mean "this handle is gone" — so both
        surface as `SandboxNotFound`, prompting recreation from the snapshot.
        """
        pod_url, remote_id = _decode_handle(handle)
        url = f"{pod_url}/sandboxes/{remote_id}{suffix}"
        try:
            resp = await self._client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise SandboxNotFound(handle.id) from exc
        if resp.status_code == 404:
            self._raise_mapped(resp, handle)
        resp.raise_for_status()
        return resp

    @staticmethod
    def _raise_mapped(resp: httpx.Response, handle: SandboxHandle) -> None:
        body = resp.json()
        exc_type = _ERRORS.get(body.get("error", ""), SandboxNotFound)
        message = body.get("detail") or handle.id
        raise exc_type(message)

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        # #345 `sandbox_id` is the local-sandbox-on-shared-vol affordance; the
        # HTTP host owns its OWN per-sandbox lifecycle + storage and mints the
        # handle (pod_url+remote_id), so the hint does not apply here — accepted
        # for protocol compatibility and ignored.
        del sandbox_id
        resp = await self._client.post(
            f"{self._base_url}/sandboxes",
            json={
                "image": spec.image,
                "env": spec.env,
                "exposed_ports": list(spec.exposed_ports),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return SandboxHandle(id=_encode_handle(data["pod_url"], data["remote_id"]))

    async def kill(self, handle: SandboxHandle) -> None:
        await self._request(handle, "DELETE", "")

    async def exec(
        self, handle: SandboxHandle, cmd: list[str], on_output: OutputSink | None = None
    ) -> ExecResult:
        pod_url, remote_id = _decode_handle(handle)
        url = f"{pod_url}/sandboxes/{remote_id}/exec"
        try:
            async with self._client.stream("POST", url, json={"cmd": cmd}) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    frame = json.loads(line)
                    if "o" in frame:
                        chunk = base64.b64decode(frame["o"])
                        if on_output is not None:
                            on_output(chunk)
                    elif "error" in frame:
                        exc_type = _ERRORS.get(frame["error"], SandboxNotFound)
                        raise exc_type(frame.get("detail") or handle.id)
                    else:  # final {"exit","out","err"} frame
                        return ExecResult(
                            exit_code=frame["exit"],
                            stdout=base64.b64decode(frame["out"]),
                            stderr=base64.b64decode(frame["err"]),
                        )
        except httpx.TransportError as exc:
            raise SandboxNotFound(handle.id) from exc
        # Stream closed before the final frame ⇒ the pod died mid-exec.
        raise SandboxNotFound(handle.id)

    async def upload(self, handle: SandboxHandle, data: bytes, remote_path: str) -> None:
        await self._request(handle, "PUT", "/file", params={"path": remote_path}, content=data)

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        resp = await self._request(handle, "GET", "/file", params={"path": remote_path})
        return resp.content

    async def upload_file(self, handle: SandboxHandle, local_path: Path, remote_path: str) -> None:
        # The host's /file endpoint takes a whole body; HttpSandbox doesn't yet
        # stream over the wire (a host-protocol change), so this satisfies the
        # #219 contract by reading the staged file and PUTting it. The default
        # Local/Docker backends stream for real.
        await self.upload(handle, local_path.read_bytes(), remote_path)

    async def download_to_file(
        self, handle: SandboxHandle, remote_path: str, local_path: Path
    ) -> None:
        local_path.write_bytes(await self.download(handle, remote_path))

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        resp = await self._request(handle, "GET", "/exists", params={"path": path})
        return bool(resp.json()["exists"])

    async def walk(self, handle: SandboxHandle, root: str) -> list[FileEntry]:
        resp = await self._request(handle, "GET", "/walk", params={"root": root})
        return [
            FileEntry(path=e["path"], size=e["size"], version=e["version"])
            for e in resp.json()["entries"]
        ]

    async def delete(self, handle: SandboxHandle, path: str) -> None:
        await self._request(handle, "DELETE", "/file", params={"path": path})

    async def mkdir(self, handle: SandboxHandle, path: str) -> None:
        await self._request(handle, "POST", "/mkdir", json={"path": path})

    async def rmdir(self, handle: SandboxHandle, path: str) -> None:
        await self._request(handle, "DELETE", "/dir", params={"path": path})

    async def rename(self, handle: SandboxHandle, src: str, dst: str) -> None:
        await self._request(handle, "POST", "/rename", json={"src": src, "dst": dst})

    async def expose_port(self, handle: SandboxHandle, container_port: int) -> tuple[str, int]:
        # No in-sandbox network-service consumer exists in v1 (no Jupyter kernel
        # in the sandbox); implement the (pod_ip, port) mapping when one does.
        raise NotImplementedError("HttpSandbox does not support expose_port in v1")
