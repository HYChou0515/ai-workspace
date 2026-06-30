"""DockerSandbox — runs each sandbox as its own Docker container.

**Deprecated** (#252): production sandboxes now run in their own pod via
the sandbox-host service (`sandbox.kind: http`, backed by
IsolatedProcessSandbox under a cgroup), which the Docker-per-sandbox model
predates. This adapter is kept working for local one-off use but is no
longer maintained — its image (`docker/Dockerfile.workspace`) is not kept
in sync with the python-stack tool bundle. Prefer `sandbox.kind: http`.

Requires a Docker daemon reachable via `docker.from_env()`. Container
lifecycle: `create` starts a long-lived container running `sleep
infinity`; `exec` uses `container.exec_run`; `kill` removes the container.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import shutil
import tarfile
import tempfile
import uuid
import warnings
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from .protocol import (
    ExecResult,
    FileEntry,
    OutputSink,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
)

if TYPE_CHECKING:
    from docker.models.containers import Container

    from docker import DockerClient


_WORKDIR = "/workspace"


class DockerSandbox:
    def __init__(self, *, client: DockerClient | None = None) -> None:
        warnings.warn(
            "DockerSandbox is deprecated (#252); run sandboxes via the "
            "sandbox-host service instead (sandbox.kind: http).",
            DeprecationWarning,
            stacklevel=2,
        )
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

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        # DEPRECATED backend (#252); `sandbox_id` (the #345 shared-vol stable-id
        # affordance) does not apply to per-container sandboxes — accepted for
        # protocol compatibility and ignored.
        del sandbox_id
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

    async def exec(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        on_output: OutputSink | None = None,
    ) -> ExecResult:
        container = self._require(handle)
        result: Any = await asyncio.to_thread(container.exec_run, cmd, demux=True, workdir=_WORKDIR)
        exit_code = result.exit_code if result.exit_code is not None else -1
        stdout_b, stderr_b = result.output
        # This adapter doesn't stream incrementally; hand the whole stdout to
        # the sink at the end so callers still see the output.
        if on_output is not None and stdout_b:
            on_output(stdout_b)
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

    async def upload_file(self, handle: SandboxHandle, local_path: Path, remote_path: str) -> None:
        container = self._require(handle)
        target = PurePosixPath(_WORKDIR) / remote_path.lstrip("/")
        parent = target.parent
        await asyncio.to_thread(self._mkdir_p, container, str(parent))
        await asyncio.to_thread(
            _put_archive_from_file, container, str(parent), target.name, local_path
        )

    async def download_to_file(
        self, handle: SandboxHandle, remote_path: str, local_path: Path
    ) -> None:
        container = self._require(handle)
        target = PurePosixPath(_WORKDIR) / remote_path.lstrip("/")
        try:
            stream, _stat = await asyncio.to_thread(container.get_archive, str(target))
        except Exception as exc:  # noqa: BLE001
            raise FileNotFoundError(remote_path) from exc
        await asyncio.to_thread(_extract_tar_stream_to_file, stream, target.name, local_path)

    def _target(self, remote_path: str) -> str:
        return str(PurePosixPath(_WORKDIR) / remote_path.lstrip("/"))

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        container = self._require(handle)
        r = await asyncio.to_thread(container.exec_run, ["test", "-f", self._target(path)])
        return r.exit_code == 0

    async def delete(self, handle: SandboxHandle, path: str) -> None:
        if not await self.exists(handle, path):
            raise FileNotFoundError(path)
        container = self._require(handle)
        await asyncio.to_thread(container.exec_run, ["rm", "-f", self._target(path)])

    async def mkdir(self, handle: SandboxHandle, path: str) -> None:
        container = self._require(handle)
        await asyncio.to_thread(container.exec_run, ["mkdir", "-p", self._target(path)])

    async def rmdir(self, handle: SandboxHandle, path: str) -> None:
        container = self._require(handle)
        target = self._target(path)
        r = await asyncio.to_thread(container.exec_run, ["test", "-d", target])
        if r.exit_code != 0:
            raise FileNotFoundError(path)
        await asyncio.to_thread(container.exec_run, ["rm", "-rf", target])

    async def rename(self, handle: SandboxHandle, src: str, dst: str) -> None:
        container = self._require(handle)
        s, d = self._target(src), self._target(dst)
        r = await asyncio.to_thread(container.exec_run, ["test", "-e", s])
        if r.exit_code != 0:
            raise FileNotFoundError(src)
        parent = str(PurePosixPath(d).parent)
        script = f"mkdir -p {parent} && mv {s} {d}"
        await asyncio.to_thread(container.exec_run, ["sh", "-c", script])

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


def _put_archive_from_file(container: Container, parent: str, name: str, local_path: Path) -> None:
    """Tar `local_path` to a temp file on disk (streaming the source in chunks),
    then stream that tar into the container — so a big upload never sits whole in
    RAM (issue #219)."""
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tf:
        tarpath = tf.name
    try:
        with tarfile.open(tarpath, mode="w") as tar:
            info = tarfile.TarInfo(name=name)
            info.size = local_path.stat().st_size
            info.mode = 0o644
            with open(local_path, "rb") as src:
                tar.addfile(info, src)
        with open(tarpath, "rb") as stream:
            ok = container.put_archive(parent, stream)
        if not ok:  # pragma: no cover — docker SDK edge case, no reliable trigger
            raise RuntimeError(f"docker put_archive failed for {name}")
    finally:
        os.unlink(tarpath)


def _extract_tar_stream_to_file(stream: Any, name: str, local_path: Path) -> None:
    """Spool the get_archive tar stream to disk, then stream the single member
    out to `local_path` — the reverse of `_put_archive_from_file`, RAM-free."""
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tf:
        tarpath = tf.name
    try:
        with open(tarpath, "wb") as f:
            for chunk in stream:
                f.write(chunk)
        with tarfile.open(tarpath, mode="r") as tar:
            member = tar.getmember(name)
            src = tar.extractfile(member)
            if src is None:  # pragma: no cover — only for non-regular members
                raise FileNotFoundError(name)
            with open(local_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
    finally:
        os.unlink(tarpath)


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
            version=f"{mtime_b.decode()}-{size_b.decode()}",
        )
