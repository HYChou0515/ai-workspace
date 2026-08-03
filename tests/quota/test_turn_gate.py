"""P5's headline condition — the refusal lands BEFORE the turn is persisted.

Where a limit is enforced is the whole design here. Gating each `exec` still
lets the agent plan, reach for a sandbox, be refused, and retry: the turn is
spent rediscovering something the service already knew. So the check sits at the
same boundary the workspace-full check does — before the user's message is
written — and the composer gets a 507 instead of waiting on a reply that will
never come.
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
from workspace_app.resources.conversation import Conversation
from workspace_app.sandbox.mock import MockSandbox

from ..api._client import TestClient as ApiTestClient

ONE_CORE = ResourceLimits(cpu_cores=1.0, memory_bytes=512 * 1024**2, disk_bytes=0)


@contextlib.contextmanager
def _app(limits: PerUserResources) -> Iterator[tuple[ApiTestClient, SpecStar]]:
    """An app driven through its LIFESPAN on purpose: the heartbeat row that
    doubles as the per-person ledger is registered there (after `spec.apply`, so
    its CRUD routes stay private). A bare client would exercise a store whose
    model was never registered."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        app_resources={"rca": ONE_CORE},
        per_user_resources=limits,
    )
    with ApiTestClient(app) as client:
        yield client, spec


def _mk(spec: SpecStar, owner: str) -> str:
    return (
        spec.get_resource_manager(RcaInvestigation)
        .create(RcaInvestigation(title="t", owner=owner))
        .resource_id
    )


def _persisted(spec: SpecStar) -> int:
    """How many messages exist at all (they live inside `Conversation`).

    Asserted on the store rather than through a read route: the claim under test
    is that the refusal lands BEFORE anything is written, and the store is where
    that is decided."""
    total = 0
    for rev in spec.get_resource_manager(Conversation).list_resources():
        data = rev.data
        assert isinstance(data, Conversation)
        total += len(data.messages)
    return total


def _wake(client: ApiTestClient, item: str) -> None:
    """Wake an item's sandbox SYNCHRONOUSLY. A chat POST returns 202 and finishes
    in a background task, so a test that raced it would be asserting on whether
    that task had run yet rather than on the gate."""
    got = client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo", "hi"]})
    assert got.status_code == 200, got.text


def test_a_second_item_is_refused_once_the_person_is_at_their_limit():
    with _app(PerUserResources(count=1)) as (client, spec):
        first = _mk(spec, "alice")
        second = _mk(spec, "alice")
        _wake(client, first)  # this is what puts alice at her limit
        before = _persisted(spec)

        refused = client.post(f"/a/rca/items/{second}/messages", json={"content": "go"})
        assert refused.status_code == 507
        detail = refused.json()["detail"]
        assert detail["error"] == "sandbox_quota_exceeded"
        assert detail["dimension"] == "sandboxes"

        # …and nothing was persisted: the composer must not be left waiting on a
        # turn that was never created.
        assert _persisted(spec) == before


def test_the_item_already_holding_a_sandbox_keeps_working():
    """Being at the limit must not lock someone out of what they already have
    open — that is the difference between a quota and a punishment."""
    with _app(PerUserResources(count=1)) as (client, spec):
        item = _mk(spec, "alice")
        _wake(client, item)
        for _ in range(3):
            _wake(client, item)  # same live sandbox, never refused


def test_another_persons_items_are_unaffected():
    with _app(PerUserResources(count=1)) as (client, spec):
        _wake(client, _mk(spec, "alice"))
        _wake(client, _mk(spec, "bob"))  # bob has his own allowance


def test_no_configured_limit_leaves_every_turn_alone():
    with _app(PerUserResources()) as (client, spec):
        for _ in range(5):
            _wake(client, _mk(spec, "alice"))


def test_the_terminal_is_gated_too():
    """The human shell wakes a sandbox exactly like an agent turn does. Leaving
    it out would make the terminal the way around the limit."""
    with _app(PerUserResources(count=1)) as (client, spec):
        _wake(client, _mk(spec, "alice"))
        refused = client.post(
            f"/a/rca/items/{_mk(spec, 'alice')}/exec", json={"cmd": ["echo", "hi"]}
        )
        assert refused.status_code == 507
        assert refused.json()["detail"]["error"] == "sandbox_quota_exceeded"
