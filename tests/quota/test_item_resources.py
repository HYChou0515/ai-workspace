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
from workspace_app.perm.model import Permission
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


#: Who the next request is from. A mutable holder rather than a header,
#: because that is how the app resolves identity — `get_user_id` is a callable
#: the composition root supplies, and a double that invented a header would be
#: testing a door this app does not have.
WHO = {"id": "alice"}


@contextlib.contextmanager
def _app(
    limits: PerUserResources, *, app_resources: dict[str, ResourceLimits]
) -> Iterator[tuple[ApiTestClient, SpecStar, _RecordingSandbox]]:
    """Driven through the LIFESPAN: the heartbeat row that doubles as the
    per-person ledger is registered there."""
    WHO["id"] = "alice"
    spec = make_spec(default_user=lambda: WHO["id"])
    sandbox = _RecordingSandbox(cpu_cores=8.0, memory_bytes=8 * 1024**3)
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: WHO["id"],
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
    permission: Permission | None = None,
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
                permission=permission,
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


# ── P2: its own route, its own verb ──────────────────────────────────────


def _restricted(**grants: list[str]) -> Permission:
    """A `restricted` permission granting exactly the verbs named — set at
    CREATE time, which is how the item's `created_by` and its grants come to
    agree. Patching it on afterwards fights the very access scope under test.

    Subjects carry their `user:` prefix. Without it the grant simply matches
    nobody, and the test would read as "correctly refused" while proving
    nothing."""
    return Permission(visibility="restricted", **grants)


def test_editing_the_size_needs_more_than_editing_the_item():
    """The reason this is not a field on the item PATCH.

    Everything else on an item is `write_meta`. This decides how much of the
    OWNER's budget the item may spend, which is a different grant — and it must
    stay out of reach of the item's own agent, since `AI_FORBIDDEN` is what
    stops a turn raising its own ceiling.

    The second half of the assertion is the one that matters: the person is not
    locked out of the item, only out of this. A fix that gated too much would
    pass a test that only checked the refusal.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(
            spec,
            "owner-alice",
            permission=_restricted(read_meta=["user:bob"], write_meta=["user:bob"]),
        )

        WHO["id"] = "bob"
        refused = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0})
        assert refused.status_code in (403, 404), refused.text

        # The item's own fields go through specstar's auto-CRUD, gated on
        # `write_meta` — which bob has and must keep.
        still_editable = client.patch(f"/rca-investigation/{item}", json={"title": "renamed"})
        assert still_editable.status_code == 200, (
            f"bob must keep the write_meta he has: {still_editable.text}"
        )


def test_a_delegate_with_change_permission_may_set_it():
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(
            spec,
            "owner-alice",
            permission=_restricted(
                read_meta=["user:bob"], read_chat=["user:bob"], change_permission=["user:bob"]
            ),
        )

        WHO["id"] = "bob"
        got = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0})
        assert got.status_code in (200, 204), got.text

        # Back to someone who may actually run the item: bob was granted the
        # right to SET the size, not to spend it. That asymmetry is the design.
        WHO["id"] = "alice"
        _wake(client, item)
        assert sandbox.specs[-1].cpu_cores == 1.0, "and it reaches the sandbox"


def test_clearing_it_goes_back_to_the_resolved_default():
    """`null` is how "I have no opinion" is said — and it must restore the
    computed default rather than storing a zero."""
    with _app(PerUserResources(cpu=3.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(spec, "alice", cpu=1.0)
        cleared = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": None})
        assert cleared.status_code in (200, 204), cleared.text

        _wake(client, item)
        assert sandbox.specs[-1].cpu_cores == 3.0, "min(App 4, budget 3)"


def test_the_agent_can_never_hold_the_verb_that_sets_the_size():
    """The deciding reason this route is gated on `change_permission` and not on
    something more ordinary.

    The item's agent runs INSIDE the very sandbox this number sizes. A verb the
    AI can hold would let a turn raise its own CPU ceiling and spend the owner's
    budget — so the gate had to be one of the two verbs `AI_FORBIDDEN` covers,
    and `use_terminal` is about shell access rather than administration.

    Asserted against the authorizer rather than the constant: `AI_FORBIDDEN`
    containing the name proves only that someone wrote it down, while this
    proves the refusal actually happens. A grant as explicit as it gets — the
    AI is named in `change_permission` — and it still must not pass.
    """
    from workspace_app.perm import Actor, Permission, authorize

    everything_granted = Permission(
        visibility="restricted",
        read_meta=["user:agent"],
        change_permission=["user:agent"],
    )
    ai = Actor(user_id="agent", is_ai=True)
    human = Actor(user_id="agent", is_ai=False)

    # `created_by` is somebody else on purpose: the owner bypass would answer
    # True for either actor and hide the rule under test.
    assert authorize(human, "change_permission", everything_granted, created_by="alice") is True
    assert authorize(ai, "change_permission", everything_granted, created_by="alice") is False, (
        "an agent holding this verb could raise its own ceiling"
    )


# ── P3: what a collaborator may see ──────────────────────────────────────


def test_seeing_the_environment_needs_more_than_seeing_the_item_exists():
    """`read_meta` is the discoverability verb — it puts a title in a dashboard
    list and nothing else. Someone with only that never opens the workspace, so
    there is no screen to put this on; granting it would open a route answering
    "how big is that person's environment right now" to an audience with no use
    for the answer. An authorisation with no consumer is only attack surface.

    `read_chat` is "may enter this workspace", which is exactly where the panel
    lives — the gate and the screen it guards line up, so they cannot drift.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "owner-alice", permission=_restricted(read_meta=["user:bob"]))

        WHO["id"] = "bob"
        assert client.get(f"/a/rca/items/{item}/environment").status_code in (403, 404)


def test_a_collaborator_in_the_workspace_sees_this_items_environment():
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(
            spec,
            "owner-alice",
            cpu=1.5,
            permission=_restricted(read_meta=["user:bob"], read_chat=["user:bob"]),
        )

        WHO["id"] = "bob"
        got = client.get(f"/a/rca/items/{item}/environment")

        assert got.status_code == 200, got.text
        body = got.json()
        assert body["stated_cpu_cores"] == 1.5, "what somebody set"
        assert body["effective_cpu_cores"] == 1.5, "and what will actually apply"
        assert body["running"] is False


def test_the_environment_view_never_leaks_the_owners_other_items():
    """The reason this is a separate route rather than reusing `/me/resources`.

    That payload is scoped to ONE person and carries every environment they
    hold, with titles. A collaborator needs to know why THIS item was refused,
    not what else its owner is working on."""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        mine = _mk(
            spec,
            "owner-alice",
            permission=_restricted(read_meta=["user:bob"], read_chat=["user:bob"]),
        )
        _mk(spec, "owner-alice")  # a second item bob has no business seeing

        WHO["id"] = "bob"
        body = client.get(f"/a/rca/items/{mine}/environment").json()

        flat = str(body)
        assert "cpu_in_use" not in flat, "the owner's total is not this route's business"
        assert "live" not in flat


def test_a_clamped_setting_reports_both_numbers():
    """Never silently trim. Someone set 8 cores and can only have 2 — showing
    only the 2 makes the panel disagree with what they typed, with nothing on
    screen to explain it, and showing only the 8 would be a lie about what runs.

    Both, so the UI can say WHICH limit bound. `_enforce_ceiling` already takes
    this position at boot: a config above the ceiling fails loudly rather than
    being trimmed behind the operator's back.
    """
    with _app(PerUserResources(cpu=2.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", cpu=8.0)
        body = client.get(f"/a/rca/items/{item}/environment").json()

        assert body["stated_cpu_cores"] == 8.0, "kept as written"
        assert body["effective_cpu_cores"] == 2.0, "and what actually applies"


def test_running_is_reported_from_a_probe_of_this_item():
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice")
        assert client.get(f"/a/rca/items/{item}/environment").json()["running"] is False

        _wake(client, item)
        assert client.get(f"/a/rca/items/{item}/environment").json()["running"] is True


# ── P5: the refusal is a door, not a wall ────────────────────────────────
#
# Defaulting to the ceiling (§1.3) makes hitting the limit ordinary rather than
# exceptional, so this moment IS the feature's experience. `SandboxQuotaExceeded`
# already promises in its own docstring that "the only useful thing to tell
# someone is what to close and how much it buys back" — and then carries only a
# dimension and two numbers.


def test_the_refusal_names_what_is_holding_the_budget():
    """Without this the person is told they are full and left to work out both
    who is holding it and where to go — on a page they have to know exists."""
    with _app(PerUserResources(cpu=2.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        first = _mk(spec, "alice", cpu=2.0)
        second = _mk(spec, "alice", cpu=2.0)
        client.patch(f"/rca-investigation/{first}", json={"title": "晶圓良率分析"})
        _wake(client, first)

        refused = client.post(f"/a/rca/items/{second}/messages", json={"content": "go"})

        assert refused.status_code == 507, refused.text
        holding = refused.json()["detail"]["holding"]
        assert [h["item_id"] for h in holding] == [first]
        assert holding[0]["title"] == "晶圓良率分析", "so it can be recognised, not just addressed"
        assert holding[0]["cpu_cores"] == 2.0, "and how much closing it buys back"


def test_the_refusal_tells_a_collaborator_nothing_about_the_owners_other_items():
    """The same message, minus everything that is not theirs to see.

    A collaborator drives a turn and hits the OWNER's ceiling. Naming the items
    would hand them the owner's working set — titles included — to explain one
    refusal. They still learn why they were stopped; they do not learn what else
    that person is doing."""
    with _app(PerUserResources(cpu=2.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        first = _mk(spec, "owner-alice", cpu=2.0)
        client.patch(f"/rca-investigation/{first}", json={"title": "機密專案"})
        _wake(client, first)
        second = _mk(
            spec,
            "owner-alice",
            cpu=2.0,
            permission=_restricted(
                read_meta=["user:bob"],
                read_chat=["user:bob"],
                read_content=["user:bob"],
                converse=["user:bob"],
            ),
        )

        WHO["id"] = "bob"
        refused = client.post(f"/a/rca/items/{second}/messages", json={"content": "go"})

        assert refused.status_code == 507, refused.text
        detail = refused.json()["detail"]
        assert detail["error"] == "sandbox_quota_exceeded"
        assert detail.get("holding") == [], "not the owner's working set"
        assert "機密專案" not in str(detail)
