"""DockerSandbox — runs each sandbox as its own Docker container.

Default adapter for production-ish deployments per grill-me Q12. Requires
a Docker daemon reachable via `docker.from_env()`. Container lifecycle:
`create` starts a long-lived container running `sleep infinity`; `exec`
uses `container.exec_run`; `kill` removes the container.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import tarfile
import uuid
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from .protocol import ExecResult, FileEntry, SandboxHandle, SandboxNotFound, SandboxSpec

if TYPE_CHECKING:
    from docker.models.containers import Container

    from docker import DockerClient


_WORKDIR = "/workspace"


class DockerSandbox:
    def __init__(self, *, client: DockerClient | None = None) -> None:
        if client is None:
            import docker

            client = docker.from_env()
        self._client = client
        self._containers: dict[str, Container] = {}

    def _require(self, handle: SandboxHandle) -> Container:
        c = self._containers.get(handle.id)
        if c is None:
            raise SandboxNotFound(handle.id)
        return c

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        handle = SandboxHandle(id=str(uuid.uuid4()))
        container = await asyncio.to_thread(self._start_container, spec)
        self._containers[handle.id] = container
        return handle

    def _start_container(self, spec: SandboxSpec) -> Container:
        # Pre-publish the requested container ports to random host ports.
        # `None` for the host side tells docker to pick a free one; we
        # look it up later via `expose_port`.
        ports = {f"{p}/tcp": None for p in spec.exposed_ports} or None
        return self._client.containers.run(
            spec.image,
            command=["sleep", "infinity"],
            detach=True,
            environment=spec.env or None,
            working_dir=_WORKDIR,
            tty=False,
            auto_remove=False,
            labels={"workspace-app": "1"},
            entrypoint=[],
            ports=ports,
        )

    async def kill(self, handle: SandboxHandle) -> None:
        container = self._require(handle)
        await asyncio.to_thread(self._stop_and_remove, container)
        del self._containers[handle.id]

    @staticmethod
    def _stop_and_remove(container: Container) -> None:
        with contextlib.suppress(Exception):
            container.kill()
        container.remove(force=True)

    async def exec(self, handle: SandboxHandle, cmd: list[str]) -> ExecResult:
        container = self._require(handle)
        result: Any = await asyncio.to_thread(container.exec_run, cmd, demux=True, workdir=_WORKDIR)
        exit_code = result.exit_code if result.exit_code is not None else -1
        stdout_b, stderr_b = result.output
        return ExecResult(
            exit_code=exit_code,
            stdout=stdout_b or b"",
            stderr=stderr_b or b"",
        )

    async def upload(self, handle: SandboxHandle, data: bytes, remote_path: str) -> None:
        container = self._require(handle)
        target = PurePosixPath(_WORKDIR) / remote_path.lstrip("/")
        parent = target.parent
        name = target.name
        await asyncio.to_thread(self._mkdir_p, container, str(parent))
        tar_bytes = _make_single_file_tar(name, data)
        ok = await asyncio.to_thread(container.put_archive, str(parent), tar_bytes)
        if not ok:  # pragma: no cover — docker SDK edge case, no reliable trigger
            raise RuntimeError(f"docker put_archive failed for {remote_path}")

    @staticmethod
    def _mkdir_p(container: Container, path: str) -> None:
        container.exec_run(["mkdir", "-p", path])

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        container = self._require(handle)
        target = PurePosixPath(_WORKDIR) / remote_path.lstrip("/")
        try:
            stream, _stat = await asyncio.to_thread(container.get_archive, str(target))
        except Exception as exc:  # noqa: BLE001
            raise FileNotFoundError(remote_path) from exc
        data = b"".join(stream)
        return _extract_single_file_from_tar(data, target.name)

    async def expose_port(self, handle: SandboxHandle, container_port: int) -> tuple[str, int]:
        container = self._require(handle)
        await asyncio.to_thread(container.reload)
        attrs: Any = container.attrs or {}
        port_map = (attrs.get("NetworkSettings") or {}).get("Ports") or {}
        binds = port_map.get(f"{container_port}/tcp") or []
        if not binds:
            raise ValueError(
                f"port {container_port} not pre-published — declare it in "
                "SandboxSpec.exposed_ports before create()"
            )
        first = binds[0]
        return (first.get("HostIp") or "127.0.0.1", int(first["HostPort"]))

    async def walk(self, handle: SandboxHandle, root: str) -> list[FileEntry]:
        container = self._require(handle)
        target = PurePosixPath(_WORKDIR) / root.lstrip("/")
        # `find -printf` is a GNU extension but debian:12-slim has it; the
        # format yields `<size>\t<mtime>\t<path>` so we can parse without
        # invoking stat per file.
        result = await asyncio.to_thread(
            container.exec_run,
            ["find", str(target), "-type", "f", "-printf", "%s\\t%T@\\t%P\\n"],
        )
        if result.exit_code != 0:  # pragma: no cover — only when find binary missing
            return []
        out = result.output or b""
        if isinstance(out, tuple):  # pragma: no cover — demux=False edge case
            out = out[0] or b""
        return list(_parse_find_output(out, base=str(target)))


def _make_single_file_tar(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _extract_single_file_from_tar(tar_bytes: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
        member = tar.getmember(name)
        f = tar.extractfile(member)
        if f is None:  # pragma: no cover — only triggered for non-regular members
            raise FileNotFoundError(name)
        return f.read()


def _parse_find_output(output: bytes, base: str):
    """Yield FileEntry from `find -printf "%s\\t%T@\\t%P\\n"` output.

    %P is the path relative to the find root. We re-prepend "/" so the
    result mirrors FileStore-style canonical paths (the same shape
    Mock/LocalProcess.walk returns).
    """
    for raw_line in output.splitlines():
        if not raw_line:
            continue
        try:
            size_b, mtime_b, rel_b = raw_line.split(b"\t", 2)
        except ValueError:  # pragma: no cover — malformed line
            continue
        rel = rel_b.decode("utf-8", errors="replace")
        if not rel:  # pragma: no cover — the find root itself, dropped silently
            continue
        yield FileEntry(
            path="/" + rel,
            size=int(size_b),
            mtime=float(mtime_b),
        )
