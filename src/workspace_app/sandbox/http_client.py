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
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from .protocol import (
    EnforcedLimits,
    ExecResult,
    FileEntry,
    OutputSink,
    RunningSandbox,
    SandboxBusy,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
    WalkResult,
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
    #: #674: this backend has an artifact store behind it, so third-party tools
    #: can be resolved. Local/mock backends do not set it, and their turns
    #: report such tools as unavailable rather than leaving them silently absent.
    resolves_tools = True

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        read_timeout: float = 0.0,
        io_retry: IoRetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        host_defaults_ttl: float = 60.0,
        host_defaults_retry_after: float = 5.0,
        monotonic: Callable[[], float] | None = None,
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
        # What the host says it enforces when a spec states nothing — a property
        # of the host's DEPLOYMENT, not of any one sandbox, so it is remembered
        # rather than asked on every heartbeat.
        #
        # Remembered for a bounded time, and only when the host actually
        # answered. Both halves were learned the hard way:
        #
        # - Caching a FAILURE pinned the process to "every sandbox is free",
        #   which is precisely the defect `effective_limits` exists to fix —
        #   except silent, and only curable by restarting the pod. One blip on
        #   one `/healthz` was enough, and the staleness probe stays green
        #   because the host is fine; it was this side that gave up.
        # - Caching for ever made "a redeployed host is picked up" false: the
        #   host and the app are SEPARATE deployments, so rolling the host does
        #   not restart the app's pods. A TTL is what makes that sentence true.
        self._host_defaults_ttl = host_defaults_ttl
        self._monotonic = monotonic or time.monotonic
        self._host_defaults: EnforcedLimits | None = None
        self._host_defaults_until = 0.0
        # How long a FAILED ask is remembered — briefly, and only to stop the
        # queue below from forming. Not remembering it at all was the other
        # extreme: the lock made every waiting caller pay a full timeout in
        # turn, so a down host turned the pre-turn gate into a serial queue.
        self._host_defaults_retry_after = host_defaults_retry_after
        self._host_defaults_retry_at = 0.0
        # Serialises the fetch. Without it, cold callers racing the first ask
        # each wrote the cache and the LAST writer won — so a failing probe
        # could overwrite an answer the host had already given correctly.
        self._host_defaults_lock = asyncio.Lock()
        # Same backoff, for the listing. It is on the panel render AND the close
        # path, and it has no cache on purpose — a stale answer to "what is
        # running" is worth much less than a stale ceiling, which only changes
        # when the host is redeployed. What it does need is the FAILURE memory:
        # without it a down host makes every concurrent caller pay its own full
        # timeout, which is the queue `_host_defaults_retry_at` exists to stop.
        self._listing_retry_at = 0.0

    async def effective_limits(self, spec: SandboxSpec) -> EnforcedLimits:
        """What the HOST will really cap this sandbox at.

        The app cannot read another service's environment, so it asks. A host
        too old to advertise, or one that is unreachable right now, leaves the
        request's own `None`s in place — the pre-existing behaviour, which
        charges nothing. That is a visible under-count rather than an invented
        number; `SandboxHostCapabilityCheck` names an image too old to advertise,
        and an unreachable host is retried on the next call rather than believed
        for the rest of the process."""
        host = await self._defaults()
        return EnforcedLimits(
            cpu_cores=host.cpu_cores if spec.cpu_cores is None else spec.cpu_cores,
            memory_bytes=host.memory_bytes if spec.memory_bytes is None else spec.memory_bytes,
        )

    async def _defaults(self) -> EnforcedLimits:
        """The host's ceilings: cached while fresh, re-asked when stale, and —
        when it cannot be reached — the last thing it said rather than nothing."""
        fresh = self._fresh_defaults()
        if fresh is not None:
            return fresh
        if self._monotonic() < self._host_defaults_retry_at:
            return self._last_known()
        async with self._host_defaults_lock:
            # Re-read both under the lock: a caller we queued behind may have
            # just filled the cache (asking again would waste the round trip we
            # waited for) or just failed (asking again would rebuild the queue
            # this lock created).
            fresh = self._fresh_defaults()
            if fresh is not None:
                return fresh
            if self._monotonic() < self._host_defaults_retry_at:
                return self._last_known()
            got = await self._fetch_host_defaults()
            if got is None:
                self._host_defaults_retry_at = self._monotonic() + self._host_defaults_retry_after
                return self._last_known()
            self._host_defaults = got
            self._host_defaults_until = self._monotonic() + self._host_defaults_ttl
            self._host_defaults_retry_at = 0.0
            return got

    def _last_known(self) -> EnforcedLimits:
        """What the host last said, or nothing if it never said anything.

        A stale ceiling beats no ceiling: the numbers change only when the host
        is redeployed, while "no ceiling" charges every sandbox zero — the exact
        defect this mechanism exists to prevent. Forgetting a good answer the
        moment the host goes down would make an answered-then-unreachable host
        worse than one never asked."""
        return self._host_defaults or EnforcedLimits(cpu_cores=None, memory_bytes=None)

    def _fresh_defaults(self) -> EnforcedLimits | None:
        if self._host_defaults is None or self._monotonic() >= self._host_defaults_until:
            return None
        return self._host_defaults

    async def _fetch_host_defaults(self) -> EnforcedLimits | None:
        """The host's advertised ceilings, or `None` when it could not be asked.

        `None` and "the host advertises nothing" are deliberately different: the
        second is an ANSWER (an image too old to carry the field) and is worth
        remembering; the first is our own failure to reach it, and remembering
        that is how one blip became a permanently mis-charged pod.

        Every failure is swallowed rather than raised: this is an accounting
        refinement on a path that also serves the heartbeat, and liveness is
        decided elsewhere. The timeout is short for the same reason — it sits
        in front of a turn, before the user's message is persisted."""
        try:
            resp = await self._client.get(f"{self._base_url}/healthz", timeout=3.0)
            resp.raise_for_status()
            got = resp.json().get("defaults") or {}
        except Exception:  # noqa: BLE001 — an unreachable host must not break the tally
            logger.warning(
                "sandbox-http: could not read the host's resource defaults; "
                "charging nothing for now and retrying on the next call",
                exc_info=True,
            )
            return None
        cpu, mem = got.get("cpu_cores"), got.get("memory_bytes")
        return EnforcedLimits(
            cpu_cores=None if cpu is None else float(cpu),
            memory_bytes=None if mem is None else int(mem),
        )

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
            self._raise_mapped(resp, handle, method, suffix)
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
    def _raise_mapped(
        resp: httpx.Response, handle: SandboxHandle, method: str, suffix: str
    ) -> None:
        body = resp.json()
        exc_type = _ERRORS.get(body.get("error", ""), SandboxNotFound)
        message = body.get("detail") or handle.id
        if "error" not in body:
            # The host answers a real miss with its own `{"error": ...}`. A 404
            # WITHOUT that key is the framework's route-not-found, i.e. this host
            # does not implement this endpoint — an app pod ahead of a host pod
            # during a rollout. Same exception (nothing degrades, by decision:
            # `sandbox-host` ships on this pipeline), but the message has to say
            # which it was: an operator reading "sandbox not found" about a
            # sandbox that is plainly alive learns nothing.
            message = (
                f"{method} {suffix} is not implemented by this sandbox-host "
                f"(404 with no error body) — the host is likely older than this app"
            )
            logger.warning("sandbox-http: %s", message)
            raise exc_type(message)
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
                # #674: the third-party bundles this turn resolved, `{name: sha}`.
                # An older host ignores the extra field and simply mounts none.
                "tools": spec.tools,
                # This item's App-resolved ceilings. `None` means "not stated",
                # and the host then applies its own `SANDBOX_HOST_*` defaults —
                # which is exactly what an OLDER host does with fields it does
                # not know, so the two agree and the rollout order is free.
                "cpu_cores": spec.cpu_cores,
                "memory_bytes": spec.memory_bytes,
                "pids_max": spec.pids_max,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("sandbox-http: created sandbox for item %s", sandbox_id)
        return SandboxHandle(id=_encode_handle(data["pod_url"], data["remote_id"]))

    async def running_sandboxes(self) -> list[RunningSandbox] | None:
        """Ask the host what it is running. See `Sandbox.running_sandboxes`.

        Sent to the SERVICE, so it is answered by one arbitrary pod — hence the
        contract that this is evidence of existence and never of absence. Each
        entry carries the answering pod's own url, so the handle built here
        addresses that pod directly, exactly like the one `create` returns.

        Every failure yields `None`, never `[]`: an unreachable or too-old host
        has told us nothing, and a caller that mistook that for "nothing is
        running" would retire live sandboxes' records on a blip.

        A failure is remembered briefly (`host_defaults_retry_after`, the same
        budget the ceilings use). Not remembering it meant a down host cost every
        concurrent caller a full timeout each, on a page anyone can open — and
        the answer they would all wait for is the same `None`. Successes are NOT
        cached: what is running changes constantly, unlike a ceiling."""
        if self._monotonic() < self._listing_retry_at:
            return None
        try:
            resp = await self._client.get(f"{self._base_url}/sandboxes", timeout=5.0)
            resp.raise_for_status()
            listed = resp.json()["sandboxes"]
            # Decoded INSIDE the try, or the promise above is not kept: a
            # malformed entry would raise `KeyError` out of a method whose whole
            # contract is that it answers `None` instead of failing, and the two
            # callers are a panel render and the close path.
            return [
                RunningSandbox(
                    handle=SandboxHandle(id=_encode_handle(e["pod_url"], e["remote_id"])),
                    item_id=e.get("item_id"),
                )
                for e in listed
            ]
        except Exception:  # noqa: BLE001 — "could not ask" is an answer here, not a failure
            logger.warning("sandbox-http: could not list the host's sandboxes", exc_info=True)
            self._listing_retry_at = self._monotonic() + self._host_defaults_retry_after
            return None

    async def resolve_tools(self, declared: Mapping[str, str]) -> dict[str, Any]:
        """#674: ask the host to make these third-party tools available.

        Not per-sandbox: this runs at the START of a turn, before the sandbox
        may even exist, because its answer decides which tools the model is
        offered. The reply carries a sha per tool (mounted later, at create)
        and that tool's command schemas — one act, so the interface the model
        is shown and the bundle that runs cannot drift apart.

        Only the host can reach the artifact store; the app holds no credential
        for it."""
        resp = await self._client.post(
            f"{self._base_url}/tools/resolve", json={"tools": dict(declared)}
        )
        resp.raise_for_status()
        return resp.json()

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
        self,
        handle: SandboxHandle,
        cmd: list[str],
        on_output: OutputSink | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        pod_url, remote_id = _decode_handle(handle)
        url = f"{pod_url}/sandboxes/{remote_id}/exec"
        logger.debug("sandbox-http: exec sandbox %s cmd=%s", handle.id, cmd)
        body: dict[str, object] = {"cmd": cmd}
        if env:
            # Omitted when empty so the host sees the same request it always
            # has — an older host ignores the key, a newer one applies it.
            body["env"] = dict(env)
        try:
            async with self._client.stream("POST", url, json=body) as resp:
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
        except httpx.TimeoutException as exc:  # subclass of TransportError — catch FIRST
            # A read timeout means the command is still RUNNING on a busy host,
            # not that the sandbox is gone. Mapping it to SandboxNotFound made
            # `registry` rebuild the sandbox while the original command ran on to
            # completion in the old one — the split-brain SandboxBusy exists to
            # forbid. (`_request` has always had this ordering; exec did not.)
            logger.warning("sandbox-http: exec sandbox %s timed out -> SandboxBusy", handle.id)
            raise SandboxBusy(handle.id) from exc
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

    async def download_many(
        self, handle: SandboxHandle, remote_paths: list[str]
    ) -> list[bytes | None]:
        """A batch of paths in ONE round trip — the facade's fast lane, and the
        only backend where it changes anything that matters.

        `None` for a path the sandbox does not have: absent is an answer about
        that path, so the facade raises for a caller that demanded it and skips
        it for a listing that merely named it. POST because the path list is a
        body — a query string of 200 paths is what a proxy truncates.

        There is no old-host fallback here on purpose: `sandbox-host` ships on
        the same pipeline as this app, so designing for version skew would be
        designing for a state the deploy does not produce."""
        import base64

        resp = await self._io_request(handle, "POST", "/files", json={"paths": list(remote_paths)})
        return [None if b is None else base64.b64decode(b) for b in resp.json()["files"]]

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

    async def walk(self, handle: SandboxHandle, root: str) -> WalkResult:
        resp = await self._io_request(handle, "GET", "/walk", params={"root": root})
        body = resp.json()
        return WalkResult(
            files=[
                FileEntry(path=e["path"], size=e["size"], version=e["version"])
                for e in body["entries"]
            ],
            # A host that predates the directory half omits the key entirely, so
            # the file tree degrades to "empty folders are missing" — what it did
            # before — instead of failing. Nothing else reads this half.
            dirs=list(body.get("dirs") or []),
        )

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
