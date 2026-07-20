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

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from .protocol import (
    ExecResult,
    FileEntry,
    OutputSink,
    SandboxBusy,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
)

logger = logging.getLogger(__name__)

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


@dataclass(frozen=True)
class IoRetryPolicy:
    """How the idempotent file/probe ops retry a BUSY host (#492).

    A busy host (a read timeout, `SandboxBusy`) is retried with an ESCALATING
    per-attempt read deadline — a busy host needs MORE time, and hammering it
    with the same short deadline only piles on load — and an escalating backoff
    between tries, both capped so a genuinely-stuck host still fails in bounded
    time rather than hanging (the original #492 symptom was an UNBOUNDED read).
    After `attempts` the last `SandboxBusy` propagates and the caller fails loud
    (it must NOT rebuild — that busy sandbox is alive — nor cold-write). Tunable
    from config; a `ConnectError`/404 (gone/reaped) is never retried here.

    The read deadline for attempt *n* (1-based) is
    ``min(timeout_base_s * timeout_factor**(n-1), timeout_cap_s)`` and the wait
    after a failed attempt is ``min(backoff_base_s * backoff_factor**(n-1),
    backoff_cap_s)``."""

    attempts: int = 4
    timeout_base_s: float = 10.0
    timeout_factor: float = 2.0
    timeout_cap_s: float = 40.0
    backoff_base_s: float = 1.0
    backoff_factor: float = 2.0
    backoff_cap_s: float = 8.0
    connect_timeout_s: float = 10.0
    write_timeout_s: float = 30.0
    pool_timeout_s: float = 10.0


class HttpSandbox:
    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        read_timeout: float = 0.0,
        io_retry: IoRetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # read_timeout <= 0 ⇒ no read deadline; the host's own exec/idle timeout
        # is the real bound (a long command must not trip an HTTP read timeout).
        # This is the default for `exec` (long commands); the idempotent file ops
        # override it per-attempt via `_io_request` (a FINITE, escalating deadline
        # so a busy host is detected + retried instead of hanging forever, #492).
        read = None if read_timeout <= 0 else read_timeout
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=read, write=30.0, pool=10.0)
        )
        self._io_retry = io_retry or IoRetryPolicy()
        self._sleep = sleep or asyncio.sleep

    async def _request(
        self, handle: SandboxHandle, method: str, suffix: str, **kwargs: Any
    ) -> httpx.Response:
        """Decode the handle, connect straight to the owning pod, and map errors.

        Two failure classes, deliberately kept apart (#492):

        - a **timeout** means the pod is reachable but SLOW (overloaded, or a big
          transfer mid-flight) → `SandboxBusy`. The sandbox is alive, so it must
          not be rebuilt (split-brain) nor cold-written; the idempotent ops retry
          it with a longer deadline (`_io_request`), everything else fails loud.
        - any other transport failure (connection refused/reset = the pod is
          GONE) → `SandboxNotFound`, indistinguishable from a killed sandbox, so
          the caller rebuilds from the durable archive. A 404 (the host is up but
          has no such sandbox = reaped) maps the same way via `_raise_mapped`.
        """
        pod_url, remote_id = _decode_handle(handle)
        url = f"{pod_url}/sandboxes/{remote_id}{suffix}"
        try:
            resp = await self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:  # subclass of TransportError — catch FIRST
            logger.warning(
                "sandbox-http: %s %s busy (timeout) -> SandboxBusy %s", method, suffix, handle.id
            )
            raise SandboxBusy(handle.id) from exc
        except httpx.TransportError as exc:
            logger.warning(
                "sandbox-http: %s %s transport error -> SandboxNotFound %s",
                method,
                suffix,
                handle.id,
            )
            raise SandboxNotFound(handle.id) from exc
        if resp.status_code == 404:
            self._raise_mapped(resp, handle)
        resp.raise_for_status()
        logger.debug("sandbox-http: %s %s -> %d", method, suffix, resp.status_code)
        return resp

    async def _io_request(
        self, handle: SandboxHandle, method: str, suffix: str, **kwargs: Any
    ) -> httpx.Response:
        """`_request` for the idempotent file/probe ops, wrapped in an escalating
        retry of a BUSY host (`SandboxBusy`): each attempt gets a longer read
        deadline + a longer backoff, capped, so a slow host is given room rather
        than hammered, and a stuck one still fails in bounded time. A
        `SandboxNotFound` (gone/reaped) is not retried — it propagates so the
        caller rebuilds. NEVER wrap `create` (non-idempotent — a retry would mint
        a second sandbox), `persist` (a long rsync), or `exec` (its own deadline)."""
        p = self._io_retry
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(p.attempts),
            wait=wait_exponential(
                multiplier=p.backoff_base_s, exp_base=p.backoff_factor, max=p.backoff_cap_s
            ),
            retry=retry_if_exception_type(SandboxBusy),
            reraise=True,
            sleep=self._sleep,
        ):
            with attempt:
                n = attempt.retry_state.attempt_number
                read = min(p.timeout_base_s * p.timeout_factor ** (n - 1), p.timeout_cap_s)
                timeout = httpx.Timeout(
                    connect=p.connect_timeout_s,
                    read=read,
                    write=p.write_timeout_s,
                    pool=p.pool_timeout_s,
                )
                return await self._request(handle, method, suffix, timeout=timeout, **kwargs)
        raise AssertionError("unreachable")  # pragma: no cover — AsyncRetrying returns or raises

    @staticmethod
    def _raise_mapped(resp: httpx.Response, handle: SandboxHandle) -> None:
        body = resp.json()
        exc_type = _ERRORS.get(body.get("error", ""), SandboxNotFound)
        message = body.get("detail") or handle.id
        logger.warning(
            "sandbox-http: 404 for sandbox %s -> %s (rebuild)", handle.id, exc_type.__name__
        )
        raise exc_type(message)

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        # #492: `sandbox_id` is the workspace item id. The host now uses it to
        # restore the item's durable working dir from the NFS archive into the
        # fresh sandbox (and later persists it back), so pass it through as
        # `item_id`. A host with no archive configured simply ignores it, and an
        # older host ignores the extra field — so this stays backward-compatible.
        resp = await self._client.post(
            f"{self._base_url}/sandboxes",
            json={
                "image": spec.image,
                "env": spec.env,
                "exposed_ports": list(spec.exposed_ports),
                "item_id": sandbox_id,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("sandbox-http: created sandbox for item %s", sandbox_id)
        return SandboxHandle(id=_encode_handle(data["pod_url"], data["remote_id"]))

    async def persist(self, handle: SandboxHandle, *, delete: bool) -> None:
        # #492: ask the host to rsync this sandbox's live working dir → the
        # durable NFS archive. Host-local, so the bulk copy never crosses this
        # app↔host connection (it can't hang the way the old per-file mirror
        # did). `delete` ⇒ --delete reconcile at a quiesced turn-end / reap;
        # False ⇒ additive-only mid-turn checkpoint.
        logger.info("sandbox-http: persist sandbox %s delete=%s", handle.id, delete)
        await self._request(handle, "POST", "/persist", json={"delete": delete})

    def handle_for_id(self, sandbox_id: str) -> SandboxHandle | None:
        # The HTTP host owns its own per-sandbox lifecycle and mints handles
        # (pod_url+remote_id); it does not address by a caller-stable id, so
        # there is nothing to derive (#345). A pod with no session reads the
        # durable snapshot, as before.
        return None

    async def kill(self, handle: SandboxHandle) -> None:
        logger.info("sandbox-http: kill sandbox %s", handle.id)
        await self._request(handle, "DELETE", "")

    async def exec(
        self, handle: SandboxHandle, cmd: list[str], on_output: OutputSink | None = None
    ) -> ExecResult:
        pod_url, remote_id = _decode_handle(handle)
        url = f"{pod_url}/sandboxes/{remote_id}/exec"
        logger.debug("sandbox-http: exec sandbox %s cmd=%s", handle.id, cmd)
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
                        logger.warning(
                            "sandbox-http: exec sandbox %s host error %s", handle.id, frame["error"]
                        )
                        raise exc_type(frame.get("detail") or handle.id)
                    else:  # final {"exit","out","err"} frame
                        logger.info(
                            "sandbox-http: exec sandbox %s exit=%s", handle.id, frame["exit"]
                        )
                        return ExecResult(
                            exit_code=frame["exit"],
                            stdout=base64.b64decode(frame["out"]),
                            stderr=base64.b64decode(frame["err"]),
                        )
        except httpx.TransportError as exc:
            logger.warning(
                "sandbox-http: exec sandbox %s transport error -> SandboxNotFound", handle.id
            )
            raise SandboxNotFound(handle.id) from exc
        # Stream closed before the final frame ⇒ the pod died mid-exec.
        logger.warning("sandbox-http: exec sandbox %s stream closed before final frame", handle.id)
        raise SandboxNotFound(handle.id)

    async def upload(self, handle: SandboxHandle, data: bytes, remote_path: str) -> None:
        await self._io_request(handle, "PUT", "/file", params={"path": remote_path}, content=data)

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        resp = await self._io_request(handle, "GET", "/file", params={"path": remote_path})
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
        resp = await self._io_request(handle, "GET", "/exists", params={"path": path})
        return bool(resp.json()["exists"])

    async def disk_usage(self, handle: SandboxHandle) -> int:
        resp = await self._io_request(handle, "GET", "/disk-usage")
        return int(resp.json()["bytes"])

    async def size_of(self, handle: SandboxHandle, path: str) -> int | None:
        resp = await self._io_request(handle, "GET", "/size", params={"path": path})
        size = resp.json()["size"]
        return None if size is None else int(size)

    async def mark_ready(self, handle: SandboxHandle) -> None:
        await self._io_request(handle, "POST", "/mark-ready")

    async def is_ready(self, handle: SandboxHandle) -> bool:
        resp = await self._io_request(handle, "GET", "/ready")
        return bool(resp.json()["ready"])

    async def walk(self, handle: SandboxHandle, root: str) -> list[FileEntry]:
        resp = await self._io_request(handle, "GET", "/walk", params={"root": root})
        return [
            FileEntry(path=e["path"], size=e["size"], version=e["version"])
            for e in resp.json()["entries"]
        ]

    async def delete(self, handle: SandboxHandle, path: str) -> None:
        await self._io_request(handle, "DELETE", "/file", params={"path": path})

    async def mkdir(self, handle: SandboxHandle, path: str) -> None:
        await self._io_request(handle, "POST", "/mkdir", json={"path": path})

    async def rmdir(self, handle: SandboxHandle, path: str) -> None:
        await self._io_request(handle, "DELETE", "/dir", params={"path": path})

    async def rename(self, handle: SandboxHandle, src: str, dst: str) -> None:
        await self._io_request(handle, "POST", "/rename", json={"src": src, "dst": dst})

    async def expose_port(self, handle: SandboxHandle, container_port: int) -> tuple[str, int]:
        # No in-sandbox network-service consumer exists in v1 (no Jupyter kernel
        # in the sandbox); implement the (pod_ip, port) mapping when one does.
        raise NotImplementedError("HttpSandbox does not support expose_port in v1")
