"""Regressions for three defects the review measured — each one in a place the
PR had explicitly claimed was safe.

They share a shape worth naming: every one was invisible to the tests that
existed because the two sides being compared happened to agree (the app's
default and the host's default are both 512M/1.0), or because the thing that
should have fired was simply never wired to anything a test observed.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps import resolve as resolve_mod
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.config.schema import PerUserResources, Settings
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.quota.disk_ledger import DiskLedger
from workspace_app.quota.limits import resolve_discovered_apps
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec

from ..api._client import TestClient as ApiTestClient


class _SpySandbox(MockSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.specs: list[SandboxSpec] = []

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        self.specs.append(spec)
        return await super().create(spec, sandbox_id)


def _mk(spec: SpecStar, owner: str = "alice") -> str:
    return (
        spec.get_resource_manager(RcaInvestigation)
        .create(RcaInvestigation(title="t", owner=owner))
        .resource_id
    )


@contextlib.contextmanager
def _app(**kw) -> Iterator[tuple[ApiTestClient, SpecStar, _SpySandbox]]:
    spec = make_spec()
    sandbox = _SpySandbox()
    kw.setdefault("app_resources", resolve_discovered_apps(Settings()))
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        **kw,
    )
    with ApiTestClient(app) as client:
        yield client, spec, sandbox


# ─── 1. the app must not overwrite the sandbox host's own configuration ──


def test_an_undeclared_app_sends_no_cpu_or_memory_over_the_wire():
    """The whole `SANDBOX_HOST_*` contract rests on this. `create_app` used to
    hand every sandbox a concrete cpu/memory, because the resolution folded the
    LOCAL backend's defaults in as its bottom layer — so the http client sent
    numbers no operator chose and the host's configured ceiling was replaced.

    Nothing could observe it: both defaults are 512M / 1.0. A host tuned upward
    for a data-analysis App would have silently dropped back to 512M and started
    OOM-killing after this rollout.
    """
    with _app() as (client, spec, sandbox):
        item = _mk(spec)
        assert (
            client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo", "hi"]}).status_code
            == 200
        )
    assert sandbox.specs, "the exec should have created a sandbox"
    got = sandbox.specs[-1]
    assert got.cpu_cores is None
    assert got.memory_bytes is None
    assert got.pids_max is None


def test_a_declared_app_does_send_its_ceilings():
    """The other half — an App that states a cost still reaches the backend, or
    the feature does nothing at all."""
    from workspace_app.quota.limits import ResourceLimits

    with _app(app_resources={"rca": ResourceLimits(2.0, 1024, 0)}) as (client, spec, sandbox):
        item = _mk(spec)
        client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo", "hi"]})
    assert (sandbox.specs[-1].cpu_cores, sandbox.specs[-1].memory_bytes) == (2.0, 1024)


# ─── 2. bytes the agent produced must reach the per-person total ─────────


async def test_a_mirror_measurement_lands_in_the_disk_ledger():
    """`exec` writes STRAIGHT into the sandbox — a `pip install`, a clone, a
    generated file never pass through the files facade. The mirror sweep is the
    only thing that ever sees those bytes, so if its measurement does not reach
    the ledger they are not merely late in the owner's total, they are absent
    from it forever. And `exec` is the path that fills a disk fastest."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        per_user_resources=PerUserResources(disk="1G"),
    )
    with ApiTestClient(app):
        item = _mk(spec)
        # What the sweeper does once a mirror pass has measured a workspace.
        # Seed the measurement the walk would have produced, then publish it.
        app.state.workspace_files.record_measurement(item, 5_000_000)
        await app.state.publish_workspace_usage(item)
        assert await DiskLedger(spec).total_for("alice") == 5_000_000


# ─── 3. a deploy that configures nothing must pay nothing ────────────────


def test_no_per_user_limit_means_no_ledger_writes_and_no_extra_lookups(monkeypatch):
    """Both halves of "an existing deploy is unaffected".

    `find_work_item` is a store round-trip and its own docstring warns the call
    COUNT is the latency (~219ms measured in production). The quota closures had
    turned one file write into five of them, and added a durable ledger write on
    top — for a deploy with no per-person limit at all, i.e. for an answer
    nobody had asked for."""
    calls: list[str] = []
    real = resolve_mod.find_work_item

    def _counting(spec, item_id):
        calls.append(item_id)
        return real(spec, item_id)

    monkeypatch.setattr(resolve_mod, "find_work_item", _counting)

    with _app() as (client, spec, _sandbox):  # no per_user_resources at all
        item = _mk(spec)
        calls.clear()
        assert client.put(f"/a/rca/items/{item}/files/a.txt", content=b"hello").status_code == 204

        # One lookup for the whole write, not one per question asked about it.
        assert len(calls) <= 1, f"{len(calls)} item lookups for a single write"
        # …and nothing durable was written to account for a limit that does not exist.
        assert DiskLedger(spec)._per_item_sync("alice") == []
