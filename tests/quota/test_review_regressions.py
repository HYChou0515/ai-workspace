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
from workspace_app.config.schema import (
    PerUserResources,
    ResourceSettings,
    Settings,
)
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.quota.disk_ledger import DiskLedger
from workspace_app.quota.limits import (
    ResourceLimits,
    resolve_discovered_apps,
    warn_unenforceable_dimensions,
)
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


# ─── 4. the warning that keeps a per-person dimension from being a dead knob ──


def _limits(**by_slug) -> dict[str, ResourceLimits]:
    return {slug: ResourceLimits(*args) for slug, args in by_slug.items()}


def test_a_dimension_nobody_declared_is_reported_as_never_firing():
    """`per_user.cpu` sums what each live sandbox may use. With no App stating a
    cost the sum has no terms, so the number sits in the config dump enforcing
    nothing — the dead-knob class this codebase treats as a defect."""
    settings = Settings(resources=ResourceSettings(per_user=PerUserResources(cpu=4)))
    (message,) = warn_unenforceable_dimensions(settings, _limits(rca=(None, None, 0)))
    assert "per_user.cpu" in message
    assert "never fires" in message


def test_the_check_is_per_dimension_not_shared():
    """The regression: one shared "did anyone declare anything?" answer let an
    App that stated only `memory` silence the `cpu` warning — while `cpu` still
    could not bind, because every sandbox's cpu term was zero."""
    settings = Settings(resources=ResourceSettings(per_user=PerUserResources(cpu=4)))
    messages = warn_unenforceable_dimensions(settings, _limits(rca=(None, 2048, 0)))
    assert messages, "an App declaring only memory must not silence the cpu warning"
    assert "cpu" in messages[0]


def test_partial_declaration_is_reported_too():
    """The nastier shape: the cap fires against a partial sum, so it binds or
    not depending on which Apps a person happens to be using. It LOOKS like it
    works, which is why silence here would be worse than the total case."""
    settings = Settings(resources=ResourceSettings(per_user=PerUserResources(cpu=4)))
    (message,) = warn_unenforceable_dimensions(
        settings, _limits(rca=(2.0, None, 0), pm=(None, None, 0))
    )
    assert "pm" in message and "rca" not in message
    assert "partial sum" in message


def test_a_fully_declared_dimension_says_nothing():
    settings = Settings(resources=ResourceSettings(per_user=PerUserResources(cpu=4)))
    assert warn_unenforceable_dimensions(settings, _limits(rca=(2.0, None, 0))) == []


# ─── 5. a refused write must not be charged ──────────────────────────────


async def test_a_refused_write_leaves_no_phantom_size_in_the_ledger():
    """The ledger used to record BEFORE the check raised, so a rejected write
    charged its owner for bytes that never landed. The mirror sweep only visits
    WARM items, so a cold one would have carried that phantom size forever."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        workspace_quota=0,
        app_resources={"rca": ResourceLimits(None, None, 0)},
        per_user_resources=PerUserResources(disk="100"),
    )
    with ApiTestClient(app) as client:
        item = _mk(spec)
        assert client.put(f"/a/rca/items/{item}/files/a.bin", content=b"x" * 80).status_code == 204
        refused = client.put(f"/a/rca/items/{item}/files/b.bin", content=b"y" * 80)
        assert refused.status_code == 507

        # 80 (what actually landed), never 160 (what was attempted).
        assert await DiskLedger(spec).total_for("alice") == 80


async def test_the_usage_panel_says_untracked_rather_than_zero():
    """The panel is visible to everyone by design. With no disk cap the ledger
    is deliberately not written, so 0 means "not measured" — rendering it as
    "nothing stored" states something false."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        per_user_resources=PerUserResources(count=5),  # no disk cap
    )
    with ApiTestClient(app) as client:
        _mk(spec)
        assert client.get("/me/resources").json()["disk_tracked"] is False

    app2 = create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        per_user_resources=PerUserResources(disk="1G"),
    )
    with ApiTestClient(app2) as client:
        assert client.get("/me/resources").json()["disk_tracked"] is True
