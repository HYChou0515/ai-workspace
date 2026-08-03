"""P12 — a third-party tool, from a published artifact to a running command.

Every other test covers one link. This one walks the chain: an artifact
appears in a store, the host resolves it, a sandbox is created with the sha,
and the tool actually runs inside it. Then the author publishes again, and the
property the whole content-addressed design exists for is checked — a new
sandbox gets the new version while a sandbox already running keeps the one it
started with.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.protocol import SandboxSpec
from sandbox_host.tool_cache import EXT_DIR, ToolCache
from sandbox_host.tool_resolve import ToolResolver

from .conftest import certify

_URL = "https://gitlab.example/api/v4/projects/7/jobs/artifacts/main/raw/dist/tool.manifest.json?job=build"
_BUILDER = "registry.example/tool-builder@sha256:beef"


def _published(version: str, says: str) -> tuple[bytes, bytes]:
    """What an author's CI uploads: a runnable bundle and its manifest."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        launch = f"#!/bin/sh\necho {says}\n".encode()
        info = tarfile.TarInfo("launch")
        info.size = len(launch)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(launch))
    bundle = buf.getvalue()
    manifest = json.dumps(
        {
            "format_version": 1,
            "name": "wafer-history",
            "version": version,
            "commands": [{"name": "trend", "description": "d", "params_json_schema": {}}],
            "builder": _BUILDER,
            "python": "3.12",
            "arch": "x86_64",
            "bundle": {"sha256": hashlib.sha256(bundle).hexdigest(), "size": len(bundle)},
            # Certified: an artifact is a tool the platform admitted.
            "grant": certify("wafer-history"),
        }
    ).encode()
    return manifest, bundle


class _Store:
    """A GitLab whose `latest artifact` URL can be re-pointed, like a real one
    when an author pushes again."""

    def __init__(self) -> None:
        self.manifest, self.bundle = _published("1.0.0", "VERSION-ONE")

    def publish(self, version: str, says: str) -> None:
        self.manifest, self.bundle = _published(version, says)

    def __call__(self, url: str) -> bytes:
        return self.manifest if url.split("?")[0].endswith(".json") else self.bundle


def _host(tmp_path: Path, store: _Store) -> tuple[ToolResolver, LocalProcessSandbox]:
    tools_root = tmp_path / "opt-tools"
    (tools_root / "builtin").mkdir(parents=True)
    resolver = ToolResolver(
        ToolCache(tools_root / EXT_DIR, harden=lambda _p: None),
        builder_id=_BUILDER,
        arch="x86_64",
        fetch=store,
        state_dir=tools_root,
    )
    sandbox = LocalProcessSandbox(
        root_dir=tmp_path / "sandboxes", isolate=False, tools_dir=tools_root
    )
    return resolver, sandbox


async def test_a_published_artifact_becomes_a_command_the_sandbox_can_run(
    tmp_path: Path,
) -> None:
    store = _Store()
    resolver, sandbox = _host(tmp_path, store)

    resolved = resolver.resolve("wafer-history", _URL)
    handle = await sandbox.create(SandboxSpec(tools={"wafer-history": resolved.sha}))
    run = await sandbox.exec(handle, ["../.tools/wafer-history/launch"])

    assert run.exit_code == 0
    assert "VERSION-ONE" in run.stdout.decode()
    # And the app was told what to tell the model, from the same resolve.
    assert resolved.version == "1.0.0"
    assert [c.name for c in resolved.commands] == ["trend"]


async def test_a_new_release_reaches_the_next_sandbox_and_leaves_the_running_one_alone(
    tmp_path: Path,
) -> None:
    """The property content-addressing is for. A sandbox mid-conversation keeps
    the tool it started with — swapping it underneath would change what a
    command does between two calls in one turn — while the next sandbox to open
    gets what the author just pushed, with nobody redeploying anything."""
    store = _Store()
    resolver, sandbox = _host(tmp_path, store)
    first = resolver.resolve("wafer-history", _URL)
    running = await sandbox.create(SandboxSpec(tools={"wafer-history": first.sha}))

    store.publish("2.0.0", "VERSION-TWO")
    second = resolver.resolve("wafer-history", _URL)
    fresh = await sandbox.create(SandboxSpec(tools={"wafer-history": second.sha}))

    assert second.sha != first.sha
    assert (
        "VERSION-TWO"
        in (await sandbox.exec(fresh, ["../.tools/wafer-history/launch"])).stdout.decode()
    )
    assert (
        "VERSION-ONE"
        in (await sandbox.exec(running, ["../.tools/wafer-history/launch"])).stdout.decode()
    )


async def test_the_sweep_cannot_reclaim_a_version_a_live_sandbox_is_on(
    tmp_path: Path,
) -> None:
    store = _Store()
    resolver, sandbox = _host(tmp_path, store)
    first = resolver.resolve("wafer-history", _URL)
    await sandbox.create(SandboxSpec(tools={"wafer-history": first.sha}))
    store.publish("2.0.0", "VERSION-TWO")
    second = resolver.resolve("wafer-history", _URL)
    await sandbox.create(SandboxSpec(tools={"wafer-history": second.sha}))

    removed = resolver.cache.sweep(in_use=sandbox.tools_in_use(), max_bytes=1)

    assert removed == []
    assert resolver.cache.has(first.sha) and resolver.cache.has(second.sha)


async def test_an_author_who_stops_publishing_does_not_break_an_open_workspace(
    tmp_path: Path,
) -> None:
    # The store goes away entirely. The last version that resolved keeps
    # working, flagged stale, rather than the workspace losing the tool.
    store = _Store()
    resolver, sandbox = _host(tmp_path, store)
    first = resolver.resolve("wafer-history", _URL)

    def offline(_url: str) -> bytes:
        raise OSError("gitlab is unreachable")

    resolver._fetch = offline
    fallen_back = resolver.resolve("wafer-history", _URL)

    assert fallen_back.sha == first.sha
    assert fallen_back.stale is True
    handle = await sandbox.create(SandboxSpec(tools={"wafer-history": fallen_back.sha}))
    run = await sandbox.exec(handle, ["../.tools/wafer-history/launch"])
    assert "VERSION-ONE" in run.stdout.decode()
