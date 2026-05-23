"""Phase 7 — Sandbox.expose_port across all three implementations.

Per plan-backend §7.1: the kernel runs inside the sandbox and the
backend talks to it over ZMQ from outside, so we need a way to ask
"what host:port reaches container port N?". For LocalProcessSandbox
the sandbox is the host (noop pass-through); for DockerSandbox the
ports must be pre-published at create-time (Docker can't add a port
to a live container), so we declare them on SandboxSpec.
"""

from __future__ import annotations

import contextlib

import pytest

from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec

# ---------------- MockSandbox ----------------


async def test_mock_expose_port_returns_localhost_and_same_port():
    s = MockSandbox()
    h = await s.create(SandboxSpec())
    host, port = await s.expose_port(h, 9001)
    assert (host, port) == ("127.0.0.1", 9001)


async def test_mock_expose_port_records_calls_for_assertions():
    """Tests that drive the KernelService can later check 'which ports
    did we expose on this sandbox?' without having to wire a separate
    spy. MockSandbox tracks them on the handle for free."""
    s = MockSandbox()
    h = await s.create(SandboxSpec())
    await s.expose_port(h, 9001)
    await s.expose_port(h, 9002)
    assert sorted(s.exposed_ports(h)) == [9001, 9002]


async def test_mock_expose_port_is_idempotent_for_pre_declared_ports():
    """If the port was already declared in SandboxSpec.exposed_ports
    (the KernelService flow), expose_port returns the mapping without
    duplicating the spy entry."""
    s = MockSandbox()
    h = await s.create(SandboxSpec(exposed_ports=(9001,)))
    await s.expose_port(h, 9001)
    await s.expose_port(h, 9001)
    assert s.exposed_ports(h) == [9001]


# ---------------- LocalProcessSandbox ----------------


async def test_local_expose_port_is_passthrough():
    """Sandbox is the host — exposing a port is conceptually a noop;
    we just hand back ("127.0.0.1", container_port)."""
    s = LocalProcessSandbox()
    h = await s.create(SandboxSpec())
    try:
        host, port = await s.expose_port(h, 9001)
        assert (host, port) == ("127.0.0.1", 9001)
    finally:
        await s.kill(h)


# ---------------- DockerSandbox ----------------

pytest.importorskip("docker")
from docker.errors import DockerException  # noqa: E402

import docker  # noqa: E402
from workspace_app.sandbox.docker import DockerSandbox  # noqa: E402


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except (DockerException, OSError):
        return False


needs_docker = pytest.mark.skipif(not _docker_available(), reason="docker daemon unavailable")
_IMAGE = "debian:12-slim"


@pytest.fixture
async def docker_sandbox():
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


@needs_docker
async def test_docker_expose_port_returns_dynamic_host_port(docker_sandbox: DockerSandbox):
    """Ports pre-declared in SandboxSpec.exposed_ports get published to
    a random host port at create-time; expose_port surfaces the mapping."""
    h = await docker_sandbox.create(SandboxSpec(image=_IMAGE, exposed_ports=(9001,)))
    host, host_port = await docker_sandbox.expose_port(h, 9001)
    assert host in ("0.0.0.0", "127.0.0.1")
    assert host_port > 0
    assert host_port != 9001  # docker picked something dynamic


@needs_docker
async def test_docker_expose_port_unknown_port_raises(docker_sandbox: DockerSandbox):
    """If the port wasn't pre-declared we can't add it to a running
    container — surface that as a clear error instead of hanging."""
    h = await docker_sandbox.create(SandboxSpec(image=_IMAGE, exposed_ports=(9001,)))
    with pytest.raises(ValueError, match="not pre-published"):
        await docker_sandbox.expose_port(h, 9999)


@needs_docker
async def test_docker_expose_port_multiple_ports_distinct_mappings(docker_sandbox: DockerSandbox):
    h = await docker_sandbox.create(SandboxSpec(image=_IMAGE, exposed_ports=(9001, 9002, 9003)))
    p1 = await docker_sandbox.expose_port(h, 9001)
    p2 = await docker_sandbox.expose_port(h, 9002)
    p3 = await docker_sandbox.expose_port(h, 9003)
    assert len({p1[1], p2[1], p3[1]}) == 3
