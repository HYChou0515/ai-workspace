import contextlib

import pytest

pytest.importorskip("docker")

from docker.errors import DockerException  # noqa: E402

import docker  # noqa: E402
from workspace_app.sandbox.docker import DockerSandbox  # noqa: E402
from workspace_app.sandbox.protocol import (  # noqa: E402
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
)


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except (DockerException, OSError):
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="docker daemon unavailable")


# debian:12-slim is already pulled on the test host; switch via env var if
# running elsewhere. Keeps tests off the network for CI/dev.
_IMAGE = "debian:12-slim"


@pytest.fixture
async def sandbox():
    s = DockerSandbox()
    handles: list[SandboxHandle] = []

    orig_create = s.create

    async def create_tracked(spec):
        h = await orig_create(spec)
        handles.append(h)
        return h

    s.create = create_tracked  # ty: ignore[invalid-assignment]
    try:
        yield s
    finally:
        for h in handles:
            with contextlib.suppress(Exception):
                await s.kill(h)


async def test_create_returns_unique_handles(sandbox: DockerSandbox):
    h1 = await sandbox.create(SandboxSpec(image=_IMAGE))
    h2 = await sandbox.create(SandboxSpec(image=_IMAGE))
    assert h1.id != h2.id


async def test_exec_real_echo(sandbox: DockerSandbox):
    h = await sandbox.create(SandboxSpec(image=_IMAGE))
    r = await sandbox.exec(h, ["echo", "hello"])
    assert r.exit_code == 0
    assert r.stdout == b"hello\n"


async def test_exec_false_returns_exit_1(sandbox: DockerSandbox):
    h = await sandbox.create(SandboxSpec(image=_IMAGE))
    r = await sandbox.exec(h, ["false"])
    assert r.exit_code == 1


async def test_exec_forwards_stdout_to_on_output(sandbox: DockerSandbox):
    h = await sandbox.create(SandboxSpec(image=_IMAGE))
    chunks: list[bytes] = []
    r = await sandbox.exec(h, ["echo", "hello"], on_output=chunks.append)
    assert r.exit_code == 0
    assert b"".join(chunks) == b"hello\n"


async def test_upload_download_roundtrip(sandbox: DockerSandbox):
    h = await sandbox.create(SandboxSpec(image=_IMAGE))
    await sandbox.upload(h, b"payload", "/notes.txt")
    assert await sandbox.download(h, "/notes.txt") == b"payload"


async def test_exec_cat_reads_uploaded_file(sandbox: DockerSandbox):
    h = await sandbox.create(SandboxSpec(image=_IMAGE))
    await sandbox.upload(h, b"docker content", "/data.txt")
    r = await sandbox.exec(h, ["cat", "data.txt"])
    assert r.exit_code == 0
    assert r.stdout == b"docker content"


async def test_kill_removes_container(sandbox: DockerSandbox):
    h = await sandbox.create(SandboxSpec(image=_IMAGE))
    await sandbox.kill(h)
    with pytest.raises(SandboxNotFound):
        await sandbox.exec(h, ["echo", "x"])


async def test_download_missing_path_raises_file_not_found(sandbox: DockerSandbox):
    h = await sandbox.create(SandboxSpec(image=_IMAGE))
    with pytest.raises(FileNotFoundError):
        await sandbox.download(h, "/does-not-exist")


async def test_accepts_externally_provided_client():
    client = docker.from_env()
    s = DockerSandbox(client=client)
    h = await s.create(SandboxSpec(image=_IMAGE))
    try:
        r = await s.exec(h, ["echo", "external"])
        assert r.stdout == b"external\n"
    finally:
        await s.kill(h)


async def test_walk_lists_uploaded_files(sandbox: DockerSandbox):
    h = await sandbox.create(SandboxSpec(image=_IMAGE))
    await sandbox.upload(h, b"hello", "/a.txt")
    await sandbox.upload(h, b"world!!", "/sub/b.txt")
    entries = await sandbox.walk(h, "/")
    by_path = {e.path: e.size for e in entries}
    assert by_path == {"/a.txt": 5, "/sub/b.txt": 7}


def test_parse_find_output_skips_blank_lines():
    """find's output may include a trailing newline; _parse_find_output
    has to skip the resulting empty record without throwing."""
    from workspace_app.sandbox.docker import _parse_find_output

    raw = b"5\t1.0\ta.txt\n\n7\t2.0\tb.txt\n"
    entries = list(_parse_find_output(raw, base="/workspace"))
    assert [e.path for e in entries] == ["/a.txt", "/b.txt"]
