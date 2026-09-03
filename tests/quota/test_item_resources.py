"""P1 — an item may state how big its own environment is.

Until now the size of an item's sandbox was a property of its App: every item
of one App got the same cgroup. This is the seam that lets one item ask for
less, and it is charged to its owner exactly as before.

Driven through the door a person actually uses — sending a message — rather
than by asking `_spec_for` what it returns. The whole point of the field is
that it changes whether a turn is admitted, and a test that stops at the
resolver would pass while the number never reached the gate.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.config.schema import PerUserResources
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.quota.limits import ResourceLimits
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec

from ..api._client import TestClient as ApiTestClient

#: Bigger than the person's whole budget below — so an item that takes the App's
#: word for it can never be admitted, and one that asks for less can.
FOUR_CORES = ResourceLimits(cpu_cores=4.0, memory_bytes=4 * 1024**3, disk_bytes=0)


class _RecordingSandbox(MockSandbox):
    """The real stand-in, plus a note of what it was asked to build.

    Subclassed rather than replaced so every other behaviour stays whatever
    `MockSandbox` does — the claim under test is which numbers arrive at
    `create`, and a double that reimplemented the sandbox could agree with a
    wrong answer."""

    def __init__(self, *, cpu_cores: float, memory_bytes: int) -> None:
        super().__init__(cpu_cores=cpu_cores, memory_bytes=memory_bytes)
        self.specs: list[SandboxSpec] = []

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        self.specs.append(spec)
        return await super().create(spec, sandbox_id)


@contextlib.contextmanager
def _app(
    limits: PerUserResources, *, app_resources: dict[str, ResourceLimits]
) -> Iterator[tuple[ApiTestClient, SpecStar, _RecordingSandbox]]:
    """Driven through the LIFESPAN: the heartbeat row that doubles as the
    per-person ledger is registered there."""
    spec = make_spec()
    sandbox = _RecordingSandbox(cpu_cores=8.0, memory_bytes=8 * 1024**3)
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        app_resources=app_resources,
        per_user_resources=limits,
    )
    with ApiTestClient(app) as client:
        yield client, spec, sandbox


def _mk(
    spec: SpecStar,
    owner: str,
    *,
    cpu: float | None = None,
    memory: int | None = None,
) -> str:
    """Named rather than `**kwargs`: a splatted dict types as a union, and ty
    stops checking which field got which value — the exact way a test can call
    something wrong and still be green."""
    return (
        spec.get_resource_manager(RcaInvestigation)
        .create(
            RcaInvestigation(
                title="t",
                owner=owner,
                sandbox_cpu_cores=cpu,
                sandbox_memory_bytes=memory,
            )
        )
        .resource_id
    )


def _wake(client: ApiTestClient, item: str) -> None:
    """Wake an item's sandbox SYNCHRONOUSLY. A chat POST returns 202 and finishes
    in a background task, so a test that raced it would be asserting on whether
    that task had run yet rather than on the gate."""
    got = client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo", "hi"]})
    assert got.status_code == 200, got.text


def test_asking_for_less_lets_two_items_run_where_one_filled_the_budget():
    """The tracer bullet, and the whole point of the feature in one assertion.

    A 4-core App under a 2-core budget: taking the App's word means one item
    fills the person completely, and the second is refused. Saying "this one
    only needs a core" is what makes them fit — and the number has to reach the
    ADMISSION GATE to do it, not merely be stored on the item.
    """
    with _app(PerUserResources(cpu=2.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        first = _mk(spec, "alice", cpu=1.0)
        second = _mk(spec, "alice", cpu=1.0)

        _wake(client, first)
        allowed = client.post(f"/a/rca/items/{second}/messages", json={"content": "go"})

        assert allowed.status_code == 202, allowed.text


def test_taking_the_apps_word_still_fills_the_budget():
    """The control, and the reason the test above proves anything.

    Without a per-item size the App's ceiling applies (clamped to what the owner
    can afford, so it still RUNS — §1.3), and it takes the whole budget. If this
    one also passed, the test above would be measuring nothing.
    """
    with _app(PerUserResources(cpu=2.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        first = _mk(spec, "alice")
        second = _mk(spec, "alice")

        _wake(client, first)
        refused = client.post(f"/a/rca/items/{second}/messages", json={"content": "go"})

        assert refused.status_code == 507, refused.text
        assert refused.json()["detail"]["error"] == "sandbox_quota_exceeded"


def test_an_unset_item_follows_the_owners_budget_when_it_changes():
    """Why the default is never STORED (plan §1.9).

    An item that states no size resolves one every time it is asked, so raising
    the owner's quota moves it. Had `min(App, budget)` been computed once and
    written to the item, a three-month-old item would still be running at the
    old size — looking entirely normal, with nothing on screen to say it was
    stale. Same argument that refused a configured context limit in #767: a
    second copy of a fact is a fact that will be wrong.
    """
    with _app(PerUserResources(cpu=1.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(spec, "alice")
        _wake(client, item)
        assert sandbox.specs[-1].cpu_cores == 1.0, "clamped to what alice can afford"

    with _app(PerUserResources(cpu=3.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(spec, "alice")
        _wake(client, item)
        assert sandbox.specs[-1].cpu_cores == 3.0, "the same item follows the new budget"


def test_a_stated_size_is_kept_even_when_the_budget_grows():
    """The control. Only the UNSET case follows the budget — a number somebody
    typed is theirs, and quietly growing it would be as wrong as quietly
    shrinking it."""
    with _app(PerUserResources(cpu=8.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(spec, "alice", cpu=1.0)
        _wake(client, item)
        assert sandbox.specs[-1].cpu_cores == 1.0


# ── memory gets its own conditions, never cpu's ──────────────────────────
#
# The previous plan's post-mortem: every acceptance condition it wrote checked
# `count`, so "usage is always 0" passed the whole plan. One dimension may not
# stand in for another.


def test_memory_is_resolved_separately_from_cpu():
    """An item may state one dimension and not the other, and each falls through
    on its own — the same per-dimension rule the config layer already promises."""
    with _app(PerUserResources(memory="4G"), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(spec, "alice", memory=512 * 1024**2)
        _wake(client, item)

        got = sandbox.specs[-1]
        assert got.memory_bytes == 512 * 1024**2, "the item's own figure"
        assert got.cpu_cores == 4.0, "and cpu still comes from the App, untouched"


def test_memory_alone_is_clamped_by_the_owners_budget():
    """The memory half of the clamp, asserted on its own. With only the cpu
    condition, a clamp that silently ignored memory would pass."""
    with _app(PerUserResources(memory="1G"), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(spec, "alice", memory=8 * 1024**3)
        _wake(client, item)

        assert sandbox.specs[-1].memory_bytes == 1024**3, "held down to what alice can afford"


def test_memory_alone_refuses_a_second_item():
    """`cpu` has room; `memory` is what refuses. Driven through the door, so
    this covers the whole path rather than the resolver."""
    roomy_cpu = PerUserResources(cpu=100.0, memory="1G")
    with _app(roomy_cpu, app_resources={"rca": FOUR_CORES}) as (client, spec, sandbox):
        first = _mk(spec, "alice", memory=1024**3)
        second = _mk(spec, "alice", memory=1024**3)

        _wake(client, first)
        refused = client.post(f"/a/rca/items/{second}/messages", json={"content": "go"})

        assert refused.status_code == 507, refused.text
        assert refused.json()["detail"]["dimension"] == "memory"


# ── the configuration that hides the defect ──────────────────────────────
#
# The previous plan verified against a demo App that DID declare `resources`,
# which is why "wrong when nothing is declared" survived it. Every App in this
# repo declares nothing, so that is the configuration to verify against.

UNDECLARED = ResourceLimits(cpu_cores=None, memory_bytes=None, disk_bytes=0)


def test_an_items_own_size_applies_when_its_app_declares_nothing():
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": UNDECLARED}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(spec, "alice", cpu=1.0)
        _wake(client, item)

        assert sandbox.specs[-1].cpu_cores == 1.0


def test_nothing_declared_anywhere_leaves_the_spec_unstated():
    """No App ceiling, no item size, no budget ⇒ `None`, which means "inherit the
    deploy's own default" — NOT zero, and not some number we made up. This is
    the shape every App in this repo is in today."""
    with _app(PerUserResources(), app_resources={"rca": UNDECLARED}) as (client, spec, sandbox):
        item = _mk(spec, "alice")
        _wake(client, item)

        got = sandbox.specs[-1]
        assert got.cpu_cores is None
        assert got.memory_bytes is None
