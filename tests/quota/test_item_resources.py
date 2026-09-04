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

import pytest
from fastapi import FastAPI
from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.config.schema import PerUserResources
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.perm.model import Permission
from workspace_app.quota.limits import ResourceLimits
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec, WalkResult

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

    #: When True, `exists` keeps answering yes after everything has been killed
    #: — which is what the shared item dir on `kind: local` actually does. Those
    #: dirs answer "who has files", not "what is running".
    pretend_dir_survives = False

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        self.specs.append(spec)
        return await super().create(spec, sandbox_id)

    async def walk(self, handle: SandboxHandle, root: str) -> WalkResult:
        # `_is_cold` probes with `walk` (measured, not guessed), so this is the
        # method that decides whether the item's dir "still exists". Answering
        # for a killed sandbox is what `kind: local` really does: the dir is on
        # a shared volume and outlives the processes until the reaper rmtrees it.
        if self.pretend_dir_survives:
            return WalkResult(files=[], dirs=[])
        return await super().walk(handle, root)


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
    """An item, and its size set the way production sets it.

    The size is NOT written at create: a create may not state one — that was the
    third open door the review found, and a fixture that seeded the row directly
    would be a fourth, testing a state the product cannot reach.

    It is applied afterwards under `rm.using(created_by)`, which is exactly what
    `set_item_resources` does: the owner bypass in `authorize` is what lets a
    `change_permission` delegate persist without holding `write_meta`.

    Named parameters rather than `**kwargs`: a splatted dict types as a union
    and ty stops checking which field got which value, which is how a test calls
    something wrong and stays green."""
    import msgspec

    rm = spec.get_resource_manager(RcaInvestigation)
    item = rm.create(RcaInvestigation(title="t", owner=owner, permission=permission)).resource_id
    if cpu is not None or memory is not None:
        rev = rm.get(item)
        data = rev.data
        assert isinstance(data, RcaInvestigation)
        with rm.using(WHO["id"]):
            rm.update(
                item,
                msgspec.structs.replace(data, sandbox_cpu_cores=cpu, sandbox_memory_bytes=memory),
            )
    return item


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


def test_running_is_reported_from_this_items_own_heartbeat():
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


# ── P7: a dial the backend will not honour is a lie ──────────────────────


def test_the_environment_reports_what_the_backend_will_really_apply():
    """#712's lesson, one layer up.

    That round's first defect was billing what was REQUESTED rather than what
    the backend actually applies — an App that declared nothing occupied a core
    for free. The same gap here is worse, because a person set the number
    themselves: they choose 2 cores, the panel shows 2, and the sandbox runs
    with no ceiling at all because this deploy cannot apply one.

    So the payload carries the backend's own answer alongside the resolved one.
    Where they disagree, the resolved figure is what we asked for and the
    enforced figure is what will happen.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", cpu=2.0)
        body = client.get(f"/a/rca/items/{item}/environment").json()

        assert body["effective_cpu_cores"] == 2.0, "what we asked for"
        assert body["enforced_cpu_cores"] == 2.0, "and what the backend says it will apply"


def test_a_backend_that_caps_nothing_is_reported_as_capping_nothing():
    """The case that must NOT read as "2 cores applied".

    A local deploy without CAP_SETUID or a writable cgroup root applies no
    ceiling at all. The dial then has no effect, and drawing it would promise
    something the machine will not do. `None` is the honest answer — and it is
    deliberately the same answer as "we could not ask the host", because
    `HttpSandbox` reports an unreachable host identically to one that caps
    nothing (see `warn_unenforceable_dimensions`). The UI says "cannot confirm"
    for both rather than inventing a distinction the backend cannot make.
    """

    class _Unenforcing(_RecordingSandbox):
        async def effective_limits(self, spec):  # noqa: ANN001, ANN201
            from workspace_app.sandbox.protocol import EnforcedLimits

            return EnforcedLimits(cpu_cores=None, memory_bytes=None)

    spec_store = make_spec(default_user=lambda: WHO["id"])
    WHO["id"] = "alice"
    sandbox = _Unenforcing(cpu_cores=8.0, memory_bytes=8 * 1024**3)
    app = create_app(
        spec=spec_store,
        sandbox=sandbox,
        filestore=SpecstarFileStore(spec_store),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: WHO["id"],
        app_resources={"rca": FOUR_CORES},
        per_user_resources=PerUserResources(cpu=4.0),
    )
    with ApiTestClient(app) as client:
        item = _mk(spec_store, "alice", cpu=2.0)
        body = client.get(f"/a/rca/items/{item}/environment").json()

        assert body["effective_cpu_cores"] == 2.0, "we still asked for it"
        assert body["enforced_cpu_cores"] is None, "but nothing will hold it to that"


def test_a_new_size_lands_on_the_very_next_request():
    """Found by asking who else reads the number, not by a failing test.

    `_all_facts_of` memoises (item -> slug, owner, cpu, memory) for five
    seconds, because the quota closures turned one file write into five store
    round trips without it. That cache now holds the size somebody just typed:
    save, start a turn immediately, and the sandbox is built from the OLD value
    while the panel cheerfully shows it too — "I changed it and nothing
    happened", self-healing after five seconds, which is the worst duration for
    a bug because it is exactly long enough to look like it did not work and
    short enough to be gone when anybody looks.

    The permission setter has held this position since #306: "a cache that
    outlives a revocation is a security bug, not a slow one." Same rule, same
    remedy — the write drops the entry so the next read re-derives.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(spec, "alice")
        # Warm the cache the way a real page load does, so the entry exists and
        # is fresh — reading through the route is the whole point.
        assert client.get(f"/a/rca/items/{item}/environment").status_code == 200

        client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0})

        body = client.get(f"/a/rca/items/{item}/environment").json()
        assert body["stated_cpu_cores"] == 1.0
        assert body["effective_cpu_cores"] == 1.0, "the panel must not show the pre-save size"

        _wake(client, item)
        assert sandbox.specs[-1].cpu_cores == 1.0, "and the sandbox is built from it"


# ── the gate has to hold on EVERY door, not the one I built ──────────────
#
# Adversarial review, finding 1. I added a dedicated route on
# `change_permission` and then left the FIELD on `WorkItemBase`, where
# specstar's generic PATCH writes it under `write_meta` — and `authorize`
# short-circuits `write_meta` to True for ANY caller on a public item. The
# careful route was one of two doors.
#
# Same shape as #767's F1, twice in one session: the rule was enforced where I
# was looking rather than where the value is written.


def test_the_generic_patch_cannot_set_the_size_on_a_public_item():
    """The traced repro: a caller with no grants at all, on a public item.

    `PUT .../resources` correctly refuses them. The generic PATCH must refuse
    them too, or the verb chosen in §1.5 — the one the AI can never hold —
    protects a route nobody has to use."""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", permission=Permission(visibility="public"))

        WHO["id"] = "carol"  # no grants whatsoever
        refused = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0})
        assert refused.status_code in (403, 404), refused.text

        smuggled = client.patch(f"/rca-investigation/{item}", json={"sandbox_cpu_cores": 999.0})
        assert smuggled.status_code == 403, f"the other door: {smuggled.text}"


def test_the_generic_patch_still_edits_everything_else():
    """The control. Over-gating would take away ordinary editing to protect one
    field, and a test that only checked the refusal would call that a pass."""
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
        assert (
            client.patch(f"/rca-investigation/{item}", json={"title": "renamed"}).status_code == 200
        )
        assert (
            client.patch(f"/rca-investigation/{item}", json={"sandbox_memory_bytes": 1}).status_code
            == 403
        ), "memory is gated too — one dimension standing in for the other is how P1 nearly shipped"


def test_creating_an_item_cannot_smuggle_a_size_past_the_gate():
    """Finding 1b. `POST /a/{slug}/items` merged the body straight into the
    model, so both fields arrived with no verb checked and no validation run —
    `> 0` never applies at create.

    `memory_bytes: 0` was the sharp end: `_fmt_bytes(0)` writes `max` to the
    cgroup (unlimited) while admission charges `memory_bytes or 0` — unlimited
    memory, billed as nothing."""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        made = client.post(
            "/a/rca/items",
            json={"title": "x", "sandbox_cpu_cores": -4.0, "sandbox_memory_bytes": 0},
        )
        assert made.status_code in (200, 201, 422), made.text
        if made.status_code == 422:
            return  # refused outright is also a correct answer
        item = made.json()["item_id"] if "item_id" in made.json() else made.json()["resource_id"]
        body = client.get(f"/a/rca/items/{item}/environment").json()
        assert body["stated_cpu_cores"] is None, "a size may not be set at create"
        assert body["stated_memory_bytes"] is None


def test_a_stated_size_cannot_exceed_the_apps_ceiling():
    """Adversarial review, finding 2: the App's number was a FALLBACK, never a
    ceiling.

    `_spec_for` read the item's value when set and the App's when not, then
    clamped by the owner's budget alone. So "App declares 4, person types 999"
    resolved to 999 on a deploy with no per-user quota — which is the shipped
    default, so it is the common case rather than the corner.

    Three documents and an i18n string all said the opposite. That string,
    `itemenv.size.clamped.app`, was provably unreachable: with only a budget
    clamp, `clamped` implies `effective === budget.cpu`, so the panel could
    only ever blame the quota.
    """
    with _app(PerUserResources(), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(spec, "alice", cpu=999.0)
        _wake(client, item)

        assert sandbox.specs[-1].cpu_cores == 4.0, "the App's ceiling binds a stated value too"


def test_memory_cannot_exceed_the_apps_ceiling_either():
    """Its own condition. cpu standing in for memory is how P1 nearly shipped a
    clamp that only worked in one dimension."""
    with _app(PerUserResources(), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(spec, "alice", memory=64 * 1024**3)
        _wake(client, item)

        assert sandbox.specs[-1].memory_bytes == 4 * 1024**3


def test_the_panel_can_say_the_app_was_what_bound_it():
    """The other half of finding 2: the explanation must be reachable.

    A string that cannot render is worse than a missing one — it reads as
    covered."""
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", cpu=999.0)
        body = client.get(f"/a/rca/items/{item}/environment").json()

        assert body["stated_cpu_cores"] == 999.0
        assert body["effective_cpu_cores"] == 4.0, "the App bound it, not the quota"


def test_resizing_a_live_environment_is_refused_by_the_server():
    """Adversarial review, finding 3 — a quota bypass, not a UI nicety.

    §1.4 says the size is read-only while the sandbox is live, and gave the
    reason: there is no resize op, so the number and the cgroup would disagree.
    I disabled the INPUT and left the route open. The heartbeat re-reads
    `spec_for` on every bump, so lowering a live item's size re-bills the new
    number against a cgroup still holding the old one:

        A live at 4 cores (budget 4) ⇒ B correctly refused
        PUT A {"cpu_cores": 0.1} ⇒ 200
        ledger now says 0.1 ⇒ B admitted, 7 real cores running, panel says 3.1/4

    Repeat per item and the budget is unbounded. This is exactly the
    "紀錄＝信念，沒有東西對應現實" failure §1.4 cited #720 to avoid — arriving
    through a door the plan opened itself.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", cpu=4.0)
        _wake(client, item)

        refused = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 0.1})

        assert refused.status_code == 409, refused.text
        detail = str(refused.json()["detail"])
        assert "close" in detail.lower() or "關閉" in detail, (
            f"must say what to do about it: {detail}"
        )


def test_resizing_is_allowed_again_once_nothing_is_running():
    """The control. Refusing while live is the rule; refusing always would make
    the setting unreachable on any item anyone has ever used."""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", cpu=4.0)

        got = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0})

        assert got.status_code in (200, 204), got.text


def test_the_refusal_never_names_an_item_the_reader_cannot_see():
    """Adversarial review, finding 4.

    §1.8's disclosure gate is `viewer == exc.owner`, and I read that as "the
    person whose items these are". It is not: `owner` is the DEBTOR field, an
    ordinary string anyone with write access can PATCH (#687). So carol can
    create a private item, set its `owner` to alice, wake it — and the next time
    alice is refused on her own work, the refusal hands her carol's title.

    `_title_of` is a point `get`, which no access scope filters, so the gate has
    to be the reader's own access rather than a field either of them can write.
    """
    with _app(PerUserResources(cpu=2.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        WHO["id"] = "carol"
        hers = _mk(
            spec,
            "alice",  # the DEBTOR field points at alice…
            cpu=2.0,
            permission=Permission(visibility="private"),  # …but alice cannot see it
        )
        client.patch(f"/rca-investigation/{hers}", json={"title": "Carol's confidential plan"})
        _wake(client, hers)

        WHO["id"] = "alice"
        mine = _mk(spec, "alice", cpu=2.0)
        refused = client.post(f"/a/rca/items/{mine}/messages", json={"content": "go"})

        assert refused.status_code == 507, refused.text
        assert "Carol" not in str(refused.json()), "a title alice holds no read_meta on"


def test_the_refusal_still_names_the_items_the_reader_can_see():
    """The control. Redacting everything would take away the remedy §1.8 exists
    to provide — a refusal that names nothing is the wall it replaced."""
    with _app(PerUserResources(cpu=2.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        first = _mk(spec, "alice", cpu=2.0)
        client.patch(f"/rca-investigation/{first}", json={"title": "晶圓良率分析"})
        _wake(client, first)
        second = _mk(spec, "alice", cpu=2.0)

        refused = client.post(f"/a/rca/items/{second}/messages", json={"content": "go"})

        holding = refused.json()["detail"]["holding"]
        assert [h["title"] for h in holding] == ["晶圓良率分析"]


# ── the regression MY fix introduced ────────────────────────────────────


def test_closing_an_item_still_works_for_someone_who_is_not_the_owner():
    """Regression review F1. My escalation read `context.current`; specstar's
    field is `current_resource`.

    So `held` was always None, `getattr(None, "sandbox_cpu_cores", UNSET)` was
    UNSET, and `WorkItemBase.sandbox_cpu_cores` defaults to `None` — so
    `None != UNSET` made EVERY whole-object write on EVERY WorkItem look like a
    privileged edit. Everyone except the owner and superusers was refused, and
    `close_app_item` calls `rm.update` directly with no handler for
    `PermissionDeniedError`, so an ordinary close became a 500.

    One wrong attribute name, and the blast radius was every App's every item.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", permission=Permission(visibility="public"))

        WHO["id"] = "carol"
        closed = client.post(f"/a/rca/items/{item}/close", json={"status": "resolved"})

        assert closed.status_code in (200, 204), closed.text


def test_a_whole_object_write_that_changes_nothing_privileged_is_allowed():
    """The same regression from the other side: read the item, put it back
    unchanged. Every field matches what is stored, so nothing privileged is
    being written and `write_meta` is the right bar."""
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
        current = client.get(f"/rca-investigation/{item}").json()["data"]
        put_back = client.put(f"/rca-investigation/{item}", json=current)

        assert put_back.status_code == 200, put_back.text


def test_a_whole_object_write_that_raises_the_size_is_still_refused():
    """…and the control for the control. Fixing the over-block must not undo
    the block."""
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
        current = client.get(f"/rca-investigation/{item}").json()["data"]
        current["sandbox_cpu_cores"] = 999.0

        assert client.put(f"/rca-investigation/{item}", json=current).status_code == 403


def test_a_json_patch_cannot_raise_the_size_either():
    """Regression review F2 — and the format the FE actually sends.

    specstar binds `patch_data.patch`, so RFC 6902 arrives as a plain LIST of
    ops. The escalation reached for a `.patch` attribute on it, so the whole
    branch was dead and every json-patch write skipped the check. My commit
    message claimed "both patch flavours are covered"; it was covering one.

    The existing test for this passed a real `jsonpatch.JsonPatch` object — a
    shape specstar never delivers — so the double agreed with the bug.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", permission=Permission(visibility="public"))

        WHO["id"] = "carol"  # no grants at all
        smuggled = client.patch(
            f"/rca-investigation/{item}",
            json=[{"op": "replace", "path": "/sandbox_cpu_cores", "value": 999.0}],
        )

        assert smuggled.status_code == 403, smuggled.text


def test_a_json_patch_cannot_rewire_access_either():
    """The pre-existing half of the same dead branch, and the sharper one.

    `perm/checker`'s module docstring promises that a generic PUT/PATCH cannot
    be used to rewire access control. Through 6902 it could: a stranger could
    grant themselves `change_permission` on somebody else's item. That defect
    predates this branch — the escalation was written for merge-patch and never
    reached the other flavour — but it is the same line, so it is fixed and
    pinned here rather than left for whoever meets it next.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", permission=Permission(visibility="public"))

        WHO["id"] = "carol"
        escalation = client.patch(
            f"/rca-investigation/{item}",
            json=[
                {"op": "replace", "path": "/permission/change_permission", "value": ["user:carol"]}
            ],
        )

        assert escalation.status_code == 403, escalation.text


def test_a_json_patch_of_an_ordinary_field_still_works():
    """The control. The FE patches titles and statuses this way all day."""
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
        got = client.patch(
            f"/rca-investigation/{item}",
            json=[{"op": "replace", "path": "/title", "value": "renamed"}],
        )

        assert got.status_code == 200, got.text


def test_the_auto_crud_create_cannot_set_a_size_either():
    """Regression review F3 — the third door, and the one carrying the sharp end.

    `POST /rca-investigation` is specstar's own create route, and the checker
    votes `allow` for create because there is no current row to compare against.
    "No row" answers the DIFFERS question; it does not answer "may you state
    this at all", and for these two fields the answer is no — the same reason
    the App-level create route strips them.

    `memory_bytes: 0` is why it matters: `_fmt_bytes(0)` writes `max` to the
    cgroup (unlimited) while admission charges `memory_bytes or 0`, i.e.
    nothing. Unlimited memory, billed as zero, set by anyone who may create an
    item — and `owner` can name somebody else, so the bill lands on them.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        WHO["id"] = "carol"
        made = client.post(
            "/rca-investigation",
            json={
                "title": "x",
                "owner": "alice",
                "sandbox_cpu_cores": 999.0,
                "sandbox_memory_bytes": 0,
            },
        )

        assert made.status_code == 403, made.text


def test_creating_an_ordinary_item_through_the_auto_crud_still_works():
    """The control. Refusing every create to protect two fields would be a much
    bigger outage than the hole."""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        made = client.post("/rca-investigation", json={"title": "x", "owner": "alice"})

        assert made.status_code in (200, 201), made.text


def test_the_size_is_editable_again_after_a_pod_forgot_its_session():
    """Regression review F5 — my 409 became a dead end on the default backend.

    On `kind: local` there is no address store, so `has_live_sandbox` falls back
    to "does the item's dir exist" — and that dir outlives the processes until
    the idle reaper rmtrees it (its own docstring: the item dirs answer "who has
    files", not "what is running"). After a pod restart that skipped
    `close_all`, or on a replica that never warmed this item, the dir is there
    and nothing is running.

    My 409 then tells the person to close an environment that is already gone,
    the panel disables the input on the same signal, and the only way out is the
    8-hour idle reaper. Refusing an edit needs a source that answers "is
    something RUNNING", so it now asks the heartbeat — which is also the source
    the quota actually bills from, so the refusal and the bill agree by
    construction.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        # Set BEFORE anything runs. Flipping it after the close was too late —
        # the teardown's own walk had already reported the dir gone, so the probe
        # never saw the state, and the first version of this test survived a
        # mutation that put the directory probe back. Measured with a script:
        # after `close_session`, with the dir answering, `has_live_sandbox`
        # returns True.
        sandbox.pretend_dir_survives = True

        item = _mk(spec, "alice")
        _wake(client, item)
        assert (
            client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0}).status_code == 409
        )

        # `close_environment` clears the HEARTBEAT even when it finds no session
        # to kill, which is what makes the heartbeat the honest source here.
        assert client.delete(f"/me/resources/live/{item}").status_code == 204

        reopened = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0})
        assert reopened.status_code in (200, 204), (
            f"the dir outlives the processes; the setting must not: {reopened.text}"
        )


def test_a_genuinely_running_environment_still_blocks_the_edit():
    """The control, and the reason the change is not just a loosening: while a
    heartbeat is live the cgroup really does hold the old number, so accepting
    an edit would re-bill against a machine that never changed."""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice")
        _wake(client, item)

        assert (
            client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0}).status_code == 409
        )


# ⚠️ No test guards the BATCHING of the title lookup.
#
# I wrote one (count `find_work_item` calls per held item) and it passed both
# with the batched version and with the per-item one put back — the counts it
# could see were dominated by lookups from the admission gate and the fact
# memo, not by this path. A test that survives the mutation it was written for
# is decorative, so it is not here rather than sitting green and claiming cover.
#
# The improvement itself is measured, in the review that found it: at four held
# environments, `find_work_item` went 6 -> 14, `load_access_facts` 0 -> 4 and
# `groups_of` 0 -> 4 before the batching. What holds it now is that
# `check_access` is pure — there is no per-row I/O left to add back without
# reintroducing a call the code no longer makes.


def test_the_environment_says_which_limit_bound_the_size():
    """Adversarial review, finding 7 — the panel was guessing, with the wrong
    person's number.

    It compared `effective` against the VIEWER's budget from `/me/resources`,
    while `effective` was clamped by the OWNER's. For the `change_permission`
    delegate P6 exists to create those are two different people, so the
    comparison was normally false and the panel blamed the App — which after R2
    is a real possibility, so a wrong attribution now sends someone to change a
    setting that is not the one holding them.

    Only the backend knows both ceilings, so it says which one bound rather
    than shipping the numbers and hoping the client works it out.
    """
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        by_app = _mk(spec, "alice", cpu=999.0)
        body = client.get(f"/a/rca/items/{by_app}/environment").json()
        assert body["cpu_bound_by"] == "app", body

    with _app(PerUserResources(cpu=1.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        by_quota = _mk(spec, "alice", cpu=999.0)
        body = client.get(f"/a/rca/items/{by_quota}/environment").json()
        assert body["cpu_bound_by"] == "quota", body


def test_nothing_bound_it_when_the_stated_size_fits():
    """The control: an attribution that is always set would read as "you are
    being held back" on every item that is not."""
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", cpu=1.0)

        assert client.get(f"/a/rca/items/{item}/environment").json()["cpu_bound_by"] is None


def test_a_nonsense_number_is_refused_rather_than_silently_clearing_the_setting():
    """Adversarial review, finding 12.

    Pydantic accepts `Infinity` and `NaN` by default. Neither is `<= 0`, so both
    passed validation, and both collapsed to `None` on the store round trip — so
    a request to SET a size performed a RESET and reported 200. The worst
    possible pair: the action taken is not the action asked for, and the status
    says it worked.

    A huge finite value is refused for a different reason: `int(1e300 * 100000)`
    is what reaches `cpu.max`, and the cgroup write fails with a generic launch
    error that names nothing. §1.7 traded away the floor mechanism on the
    promise that a bad value would point back at the setting, and a failure
    three layers down does not.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", cpu=2.0)

        # Sent as RAW body text: `json=` refuses to encode these, but a client
        # that writes them literally is exactly how they reach the server, and
        # pydantic accepts them by default.
        for bad in ("Infinity", "NaN", "1e400", "1e300"):
            got = client.put(
                f"/a/rca/items/{item}/resources",
                content=b'{"cpu_cores": ' + bad.encode() + b"}",
                headers={"content-type": "application/json"},
            )
            assert got.status_code == 422, f"{bad} -> {got.status_code} {got.text}"

        # …and the setting they had is untouched, which is the half a 200 broke.
        body = client.get(f"/a/rca/items/{item}/environment").json()
        assert body["stated_cpu_cores"] == 2.0


def test_a_nonsense_memory_size_is_refused_too():
    """Its own condition — cpu standing in for memory is how this feature keeps
    nearly shipping one-dimensional checks."""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice")

        # `"9" * 309` overflows a float, and `math.isfinite` converts before it
        # compares — so the guard meant to stop "a generic error that names
        # nothing" raised `OverflowError` and produced a 500 that names nothing.
        for bad in ("inf", "nan", "9" * 40, "9" * 309, "9" * 300 + "G"):
            got = client.put(f"/a/rca/items/{item}/resources", json={"memory": bad})
            assert got.status_code == 422, f"{bad!r} -> {got.status_code} {got.text}"


# ── round 3: my own RR5 fix was ineffective ─────────────────────────────


def test_an_idle_but_running_environment_still_blocks_the_edit(monkeypatch):
    """Round-3 finding 1. I hardcoded a 120-second window and wrote in the
    docstring that it was "the same window the admission gate uses". It was not:
    the gate is built from `idle_timeout`, which defaults to EIGHT HOURS — 240x
    longer. And the heartbeat is only bumped on use, never on a timer.

    So three minutes of the model thinking was enough to make a running sandbox
    read as stopped, and the 409 lifted — which is verbatim the re-billing the
    refusal exists to prevent ("charged 0.1 cores for 4 real ones").

    The window is now passed in from the same place the gate takes it, because
    a second number is a second rule.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", cpu=4.0)
        _wake(client, item)
        assert (
            client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 0.1}).status_code == 409
        )

        # Three minutes later, nothing closed, the sandbox still running.
        from workspace_app.api import item_routes

        real_now = item_routes._now_ms
        monkeypatch.setattr(item_routes, "_now_ms", lambda: real_now() + 180_000)
        still = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 0.1})

        assert still.status_code == 409, f"idle is not stopped: {still.text}"


def test_repointing_the_owner_does_not_unlock_a_running_environment():
    """Round-3 finding 2 — a door my own fix opened.

    The heartbeat row is keyed by the owner recorded when it was bumped, and I
    looked it up under `owner_of(item)` as it is NOW. `owner` is an ordinary
    PATCH-able field (#687), so repointing it moved the query to somebody with
    no rows and the item read as stopped. It also fired on an ordinary handover,
    which is the same bug wearing a legitimate hat.

    The item's own heartbeat is keyed on the item, and nobody can rewrite that.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", cpu=4.0)
        _wake(client, item)
        assert (
            client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 0.1}).status_code == 409
        )

        moved = client.patch(
            f"/rca-investigation/{item}",
            json=[{"op": "replace", "path": "/owner", "value": "bob"}],
        )
        assert moved.status_code == 200, moved.text

        still = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 0.1})
        assert still.status_code == 409, f"a rewritten owner unlocked it: {still.text}"


def test_each_dimension_reports_its_own_ceiling():
    """Round-3 finding 5. One scalar for both dimensions misattributes the
    moment they differ.

    App declares 4 cores and states no memory; the owner's quota is unlimited
    on cpu and 2G on memory; the item asks for 999 cores and 8G. The CPU is held
    by the App, the memory by the quota — and a single value reported "quota",
    so the panel (which renders the explanation for CPU) told the person their
    quota was holding a number the App was holding.

    That is the wrong-setting failure the field exists to remove, produced by
    the field itself.
    """
    app_caps_cpu_only = ResourceLimits(cpu_cores=4.0, memory_bytes=None, disk_bytes=0)
    quota_caps_memory_only = PerUserResources(cpu=0.0, memory="2G")
    with _app(quota_caps_memory_only, app_resources={"rca": app_caps_cpu_only}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", cpu=999.0, memory=8 * 1024**3)

        body = client.get(f"/a/rca/items/{item}/environment").json()

        assert body["effective_cpu_cores"] == 4.0
        assert body["cpu_bound_by"] == "app", body
        assert body["effective_memory_bytes"] == 2 * 1024**3
        assert body["memory_bound_by"] == "quota", body


def test_my_resources_does_not_name_an_item_i_cannot_see():
    """Round-3 finding 7 — the redaction I added to the 507 is defeated one
    route over, and that route is the one the refusal points at.

    `GET /me/resources` builds every live row's title from `locator.title_of`
    with no `read_meta` check. The debtor is the `owner` FIELD, which anyone
    with write access can PATCH (#687), so carol can point a private item at
    alice and have her own title read back to alice on a page alice opens for
    an unrelated reason.

    Pre-dates this branch — the page has always listed by owner — but the
    property is one this branch now claims, and a rule that holds on one route
    and not its neighbour is not a rule.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        WHO["id"] = "carol"
        hers = _mk(spec, "alice", permission=Permission(visibility="private"))
        client.patch(f"/rca-investigation/{hers}", json={"title": "Carol's confidential plan"})
        _wake(client, hers)

        WHO["id"] = "alice"
        body = client.get("/me/resources").json()

        assert "Carol" not in str(body), f"a title alice holds no read_meta on: {body}"
        # …and the row itself survives, because she IS being charged for it and
        # closing it is the remedy the page exists to offer.
        assert any(e["item_id"] == hers for e in body["live"]), body


def test_a_soft_deleted_item_does_not_take_down_the_whole_resources_page():
    """Round-4 finding 3, and strictly worse than the one R3-6 just fixed.

    A soft-deleted item can still be holding a sandbox — that is R3-6's whole
    premise, and why its row must stay closable. But on the page that OWNS the
    Close button, an unguarded lookup let `ResourceIsDeletedError` reach the
    global handler, which 410s the entire response: the person loses every row,
    including the healthy ones, and with them the only way to free their quota.

    "A rule that holds on one route and not its neighbour is not a rule" was the
    thesis of the commit that fixed the 507 side. This is the neighbour.
    """
    # Roomy on purpose: this is about a deleted row, not about a limit.
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        healthy = _mk(spec, "alice")
        doomed = _mk(spec, "alice")
        _wake(client, healthy)
        _wake(client, doomed)
        assert len(client.get("/me/resources").json()["live"]) == 2

        assert client.delete(f"/rca-investigation/{doomed}").status_code in (200, 204)

        page = client.get("/me/resources")
        assert page.status_code == 200, f"one deleted item took the page with it: {page.text}"
        body = page.json()
        assert any(e["item_id"] == healthy for e in body["live"]), body
        # …and the deleted one is still listed, because it is still being
        # charged for and closing it is the remedy this page exists to offer.
        assert any(e["item_id"] == doomed for e in body["live"]), body


def test_the_panel_and_the_resize_refusal_agree_about_running():
    """Round-4 finding 5: R3-3 shipped with no test and survived a mutation.

    The panel disables its inputs on `running`, and the resize route refuses on
    its own reading. When those came from different sources the route unblocked
    while the screen stayed grey — the person clicked Close and nothing changed.
    One question, one answer, asserted together so they cannot drift apart
    again.
    """
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        # The dir survives everything, which is what `kind: local` really does —
        # so a directory probe would answer "running" forever while the
        # heartbeat correctly says it stopped.
        sandbox.pretend_dir_survives = True
        item = _mk(spec, "alice")
        _wake(client, item)

        assert client.get(f"/a/rca/items/{item}/environment").json()["running"] is True
        assert (
            client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0}).status_code == 409
        )

        assert client.delete(f"/me/resources/live/{item}").status_code == 204

        # BOTH must move together. Asserting only the route is what let the
        # backend-only fix pass.
        assert client.get(f"/a/rca/items/{item}/environment").json()["running"] is False
        assert client.put(
            f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0}
        ).status_code in (200, 204)


def test_a_deleted_item_is_still_named_in_a_refusal():
    """Round-4 finding 5, the other half: R3-6 also survived a mutation.

    A soft-deleted item can still hold a sandbox. Dropping its row from
    `holding` hid the one thing the person could close — and two shipped
    comments describe this case by name ("addressable beats invisible"), so the
    behaviour was documented in two places and asserted in none.

    Round 6 made it NAMED rather than merely addressable. The row is here
    because its sandbox is still running, and "close t" is an instruction
    somebody can follow where "close rca-investigation:12cec732" is not. The
    reader's own `read_meta` is still what decides whether the name is shown —
    `include_deleted` relaxes what can be FOUND, never who may see it.
    """
    with _app(PerUserResources(cpu=2.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        holder = _mk(spec, "alice", cpu=2.0)
        _wake(client, holder)
        assert client.delete(f"/rca-investigation/{holder}").status_code in (200, 204)

        second = _mk(spec, "alice", cpu=2.0)
        refused = client.post(f"/a/rca/items/{second}/messages", json={"content": "go"})

        assert refused.status_code == 507, refused.text
        holding = refused.json()["detail"]["holding"]
        assert [h["item_id"] for h in holding] == [holder], holding
        assert holding[0]["title"] == "t", "a deleted item's row must still name it"


def test_someone_elses_deleted_item_does_not_410_my_resources_page(monkeypatch):
    """Round-5 finding 1 — the fifth time this shape has held, and the widest.

    I guarded `_describe` and not the OTHER two lookups the same request makes.
    `_found_running` calls `facts_of` over `running_items()` — every sandbox on
    the replica, every tenant's — BEFORE the owner filter. So one person's
    soft-deleted item took down `/me/resources` for everybody else on the pod,
    with no heartbeat loss and no window expiry needed.

    The commit's own test was green because it was written from the deleter's
    point of view, and the deleter's row short-circuits before that lookup.
    """
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        WHO["id"] = "alice"
        hers = _mk(spec, "alice")
        _wake(client, hers)
        assert client.delete(f"/rca-investigation/{hers}").status_code in (200, 204)

        # The 5-second fact memo hides it: alice's own request warmed the entry,
        # so bob's lookup is served from cache. In production the entry expires
        # five seconds after the last touch and the raise then stands for as
        # long as the sandbox lives — up to the eight-hour reaper. Expiring it
        # here is what makes the probe reach the code under test.
        from workspace_app.api import app as app_mod

        monkeypatch.setattr(app_mod, "_ITEM_FACT_TTL_S", 0.0)

        WHO["id"] = "bob"
        page = client.get("/me/resources")

        assert page.status_code == 200, f"alice's deleted item broke bob's page: {page.text}"


def test_a_deleted_items_environment_can_still_be_closed():
    """Round-5 finding 2. The row I made visible last round carried a Close
    button that 410s — `close_environment` resolves the owner through another
    unguarded lookup.

    My own docstring justified keeping the row on exactly this basis: "they are
    being charged for it and closing it is the remedy this page exists to
    offer… an unnamed environment is still closable while an invisible one is
    not." It was not closable.
    """
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice")
        _wake(client, item)
        assert client.delete(f"/rca-investigation/{item}").status_code in (200, 204)
        assert any(e["item_id"] == item for e in client.get("/me/resources").json()["live"])

        closed = client.delete(f"/me/resources/live/{item}")

        assert closed.status_code == 204, f"the row is visible but not closable: {closed.text}"


def test_deleting_an_item_does_not_free_its_slot_while_it_still_runs(monkeypatch):
    """Round-6 finding 1 — a quota evasion I introduced, in four calls.

    Making the seam answer `None` for a soft-deleted item lost the difference
    between "there is no debtor" and "we could not resolve one". Four consumers
    read `owner is None` as nothing-to-bill: the admission gate returns early,
    the per-person disk cap returns early, usage is never recorded, and — worst
    — `registry._bump` writes `owner=""` over a ledger row that already named
    somebody, ERASING the charge for a sandbox that is still running.

    So: fill your quota, delete the item, poke it once (a soft-deleted item
    stays operable for the access memo's five seconds), and the slot is free
    while both sandboxes run.

    A soft-deleted item still exists and still owes. The seam reads it — with
    `include_deleted` — rather than pretending it is gone.
    """
    from workspace_app.api import app as app_mod

    monkeypatch.setattr(app_mod, "_ITEM_FACT_TTL_S", 0.0)
    with _app(PerUserResources(count=1), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        first = _mk(spec, "alice")
        second = _mk(spec, "alice")
        _wake(client, first)
        assert client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo"]}).status_code == 507

        assert client.delete(f"/rca-investigation/{first}").status_code in (200, 204)
        # The poke is ADMITTED, and the assertion says so: the positive access
        # memo holds for five seconds and a delete does not invalidate it, which
        # is the door the evasion walked through. Leaving the status unchecked
        # let this pass for the wrong reason — shorten the memo and the sequence
        # it narrates would stop happening while the test stayed green.
        poke = client.post(f"/a/rca/items/{first}/exec", json={"cmd": ["echo"]})
        assert poke.status_code == 200, poke.text

        still_refused = client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo"]})
        assert still_refused.status_code == 507, (
            f"deleting an item freed its slot while it still runs: {still_refused.text}"
        )


def test_a_deleted_items_sandbox_is_still_billed_to_its_owner(monkeypatch):
    """The same defect from the ledger's side, and the half that stays wrong
    silently rather than loudly: the page shows nothing running while the
    machine runs, so nobody can even see what to close."""
    from workspace_app.api import app as app_mod

    monkeypatch.setattr(app_mod, "_ITEM_FACT_TTL_S", 0.0)
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice")
        _wake(client, item)
        assert client.delete(f"/rca-investigation/{item}").status_code in (200, 204)
        client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo"]})

        body = client.get("/me/resources").json()

        assert any(e["item_id"] == item for e in body["live"]), (
            f"a running sandbox vanished from the page that owns Close: {body}"
        )
        assert body["cpu_in_use"] > 0, body


def test_a_deleted_item_is_billed_but_not_operable():
    """The other half of round-6 finding 1, and the direction I got wrong first.

    Making the seam resolve soft-deleted rows unconditionally fixes the billing
    and REOPENS the item: `require_item` gates every `/a/{slug}/items/...` route
    on the same lookup, so exec, resize and the rest would all start answering
    200 for something the user deleted. My own docstring claimed the routes
    would still refuse it "because they gate on access" — but access is read off
    the item record, which had just started resolving.

    "Still owes for its sandbox" and "still operable" are different questions.
    So resolving a deleted row is OPT-IN, taken only by the paths that answer
    the first — the debtor, the size, the resources page. Both halves are
    asserted here because a fix for either one alone is what the last two rounds
    actually shipped.

    The refusal is **410 Gone**, not 404: an outside system lists items and then
    acts on them, and "that one is finished, open a new one" is a different
    instruction from "no such item". CI caught me breaking that contract — no
    review round did — so the rule now lives at the GATE the routes share:
    operating on one item says GONE, while a page that lists or bills across
    MANY items never lets one deleted row take the page down.

    `unused` is deleted without ever being addressed through the API: the access
    memo holds a POSITIVE answer for five seconds and a delete does not
    invalidate it, so a just-used item stays operable for that window — true of
    this build with or without the change under test, and not what this is
    about.
    """
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        billed = _mk(spec, "alice")
        _wake(client, billed)
        assert client.delete(f"/rca-investigation/{billed}").status_code in (200, 204)

        assert any(e["item_id"] == billed for e in client.get("/me/resources").json()["live"]), (
            "a deleted item's running sandbox stopped being billed"
        )

        unused = _mk(spec, "alice")
        assert client.delete(f"/rca-investigation/{unused}").status_code in (200, 204)
        for verb, path, body in (
            ("post", f"/a/rca/items/{unused}/exec", {"cmd": ["echo"]}),
            ("put", f"/a/rca/items/{unused}/resources", {"cpu_cores": 1.0}),
            ("get", f"/a/rca/items/{unused}/environment", None),
        ):
            call = getattr(client, verb)
            reply = call(path, json=body) if body is not None else call(path)
            assert reply.status_code == 410, (
                f"{verb.upper()} {path} answered {reply.status_code}, not 410 Gone: {reply.text}"
            )


def test_a_deleted_item_is_still_charged_at_the_size_it_chose(monkeypatch):
    """Round-6, and what the mutation probe caught my OTHER tests not pinning.

    Deleting an item does not stop its sandbox, and the ledger keeps charging
    for it — but at WHAT? The size is resolved fresh on every heartbeat from the
    item's own record (never stored, §1), so a seam that reports a deleted item
    absent does not merely lose the debtor: the item's stated 1 core falls back
    to the App's declared ceiling and the person is billed FOUR.

    The lesson underneath is the probe's, not the reviewer's: my first three
    tests for this fix all stayed green when I broke the facts lookup, because
    the ledger backstop and the address of the heartbeat covered for it. A
    guard with no test that fails without it is a guard I only believe in.

    The fact memo is switched off: it holds the size for five seconds, this test
    runs in six, so with it on the assertion reads a value cached from BEFORE
    the delete and passes no matter what the lookup behind it does. That is the
    same lesson one layer down — it was the mutation probe that said so.
    """
    from workspace_app.api import app as app_mod

    monkeypatch.setattr(app_mod, "_ITEM_FACT_TTL_S", 0.0)
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice", cpu=1.0)
        _wake(client, item)
        assert client.delete(f"/rca-investigation/{item}").status_code in (200, 204)

        client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo"]})

        body = client.get("/me/resources").json()
        assert body["cpu_in_use"] == 1.0, (
            f"a deleted item stopped being charged at the size it chose: {body}"
        )


def test_the_resources_page_names_a_deleted_items_environment():
    """The row is on this page so somebody can close it, and "close
    rca-investigation:12cec732" is not an instruction anybody can follow.

    Same rule as everywhere else here: `include_deleted` relaxes what can be
    FOUND, never who may see it — the name still comes out only if this reader
    passes `read_meta` on the item.
    """
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice")
        _wake(client, item)
        assert client.delete(f"/rca-investigation/{item}").status_code in (200, 204)

        rows = [e for e in client.get("/me/resources").json()["live"] if e["item_id"] == item]

        assert rows and rows[0]["title"] == "t", f"a deleted item's row lost its name: {rows}"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n", " alice ", "alice\t"])
def test_blanking_the_owner_does_not_switch_the_quota_off(monkeypatch, blank):
    """Round-7 finding 1 — one PATCH per extra sandbox, and the gate stops.

    #687 (`owner` is writable by anyone with write access) is an accepted
    trade-off, and §4 states it as "point `owner` at yourself and raise the
    numbers" — the bill MOVES. Blanking it is different in kind: `_owner_of`
    answers `None`, and that reads as NO DEBTOR at every gate at once. The
    admission gate returns early, the disk cap returns early, usage is never
    recorded, and the sandbox appears on nobody's resources page — so it is also
    a sandbox nobody can see to close.

    So an empty `owner` falls back to the creator. `created_by` is specstar's
    own, set at create and not writable through any route, which is what makes
    it a floor rather than a second field to keep in sync. `owner` still wins
    whenever it says anything, so the documented trade-off is unchanged: this
    only removes the answer "nobody".

    Parametrised over WHITESPACE, which is round-8's finding: the floor was
    `if item.owner:`, so one space walked straight past it — truthiness is not
    the same question as "does this say anything". A blank of any shape says
    nothing.

    A non-empty bogus name (`"ghost"`) is a different case and stays accepted:
    that is #687's documented trade-off, where the bill MOVES to a name nobody
    holds. What must not exist is a bill that goes nowhere.

    The PADDED cases are round-9's, and they are the ones that matter most: the
    first fix tested `.strip()` but RETURNED the raw string, so `"alice "` billed
    a person who does not exist while reading as "alice" in every UI there is.
    Predicate and value have to use the same normalisation — a rule that decides
    on one form of a value and then stores another is two rules.
    """
    from workspace_app.api import app as app_mod

    monkeypatch.setattr(app_mod, "_ITEM_FACT_TTL_S", 0.0)
    with _app(PerUserResources(count=1), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        first = _mk(spec, WHO["id"])
        second = _mk(spec, WHO["id"])
        _wake(client, first)
        assert client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo"]}).status_code == 507

        blanked = client.patch(f"/rca-investigation/{second}", json={"owner": blank})
        assert blanked.status_code in (200, 204), blanked.text

        still_refused = client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo"]})

        assert still_refused.status_code == 507, (
            f"blanking `owner` switched the per-person quota off: {still_refused.text}"
        )


def test_an_item_that_ran_once_still_occupies_a_slot_the_next_time():
    """Round-7 finding 5 — the admission gate asked the wrong thing, and I had
    already worked that out one file over.

    "Does this item already hold its slot?" was answered by
    `registry.has_live_sandbox`, which on `kind: local` degrades to "does the
    item's directory exist" — and those dirs outlive the processes until the
    8-hour reaper. So the FIRST run of an item leaves a mark that reads as
    "already holding a slot" for the rest of the day: close it, start somebody
    else, and it lets itself back in for free.

    `set_item_resources` rejected that same source and moved to the heartbeat,
    with the measurement written into its docstring ("with the pod's session
    gone and the dir still present it answers True"). The judgement did not
    travel one file.

    Now the gate reads the ledger it is about to count — "does it hold a slot"
    and "what is held" cannot disagree when they are the same row.
    """
    with _app(PerUserResources(count=1), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        sandbox.pretend_dir_survives = True
        first = _mk(spec, "alice")
        second = _mk(spec, "alice")
        _wake(client, first)
        assert client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo"]}).status_code == 507

        assert client.delete(f"/me/resources/live/{first}").status_code == 204
        assert client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo"]}).status_code == 200

        back_in = client.post(f"/a/rca/items/{first}/exec", json={"cmd": ["echo"]})

        assert back_in.status_code == 507, (
            f"an item that had run once let itself back in for free: {back_in.text}"
        )


def test_the_debtor_of_an_item_they_cannot_read_sees_the_row_but_not_its_name():
    """`include_deleted` relaxes what can be FOUND, never who may SEE it — and
    every other test of that sentence reads as one person who is owner,
    creator and permission-holder at once, so none of them could tell the two
    halves apart.

    Here they come apart: bob creates a private item and states alice as its
    `owner`, so the sandbox is billed to alice while the item stays unreadable
    to her. She is refused, and the refusal must still list the environment
    holding her budget — it is hers to close — with NO title, because naming it
    would read bob's private item back to her.
    """
    with _app(PerUserResources(count=1), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        WHO["id"] = "bob"
        hers = _mk(spec, "alice", permission=Permission(visibility="private"))
        _wake(client, hers)

        WHO["id"] = "alice"
        mine = _mk(spec, "alice")
        refused = client.post(f"/a/rca/items/{mine}/exec", json={"cmd": ["echo"]})

        assert refused.status_code == 507, refused.text
        holding = refused.json()["detail"]["holding"]
        assert [h["item_id"] for h in holding] == [hers], holding
        assert holding[0]["title"] == "", (
            f"the debtor was shown the title of an item she cannot read: {holding}"
        )


def test_a_live_item_under_the_wrong_app_is_not_found_rather_than_gone():
    """Round-8 finding 1 — I hand-rolled a second copy of the shared refusal at
    the one gate that most needed the shared one, and the copy asks the wrong
    question.

    `require_item`'s fallback asked "does this id resolve AT ALL?" rather than
    "is it deleted?", so the whole wrong-slug branch started answering 410 Gone
    for an item that is alive and well. That inverts the very contract the
    previous round restored: the outside system that lists items and acts on
    them is told "that one is finished, open a new one" about a live item it
    merely addressed under the wrong App.

    `require_item` also authorizes nothing, so the 410/404 split there is
    readable by anyone — one more reason the answer for a live item must be the
    same 404 a stranger gets for an id that never existed.

    My own checklist this round says a rule has to become ONE function so that
    missing a gate is impossible. I wrote that function and then did not call it
    here.
    """
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        alive = _mk(spec, "alice")

        wrong_slug = client.get(f"/a/pm/items/{alive}/tools")
        unknown = client.get("/a/rca/items/rca-investigation:does-not-exist/tools")

        assert wrong_slug.status_code == 404, (
            f"a LIVE item answered {wrong_slug.status_code} under the wrong app: {wrong_slug.text}"
        )
        assert unknown.status_code == 404, unknown.text

        # …and the SAME gate still says Gone for an item of this App that is
        # deleted. Asserted here because the routes behind `require_item`
        # (tools / capability / entity) are the only ones that reach it — the
        # resize and environment routes go through the other two gates, so a
        # test written against those cannot see this one at all. The mutation
        # probe is what said so: deleting this call changed nothing anywhere.
        assert client.delete(f"/rca-investigation/{alive}").status_code in (200, 204)
        gone = client.get(f"/a/rca/items/{alive}/tools")
        assert gone.status_code == 410, f"a deleted item answered {gone.status_code}: {gone.text}"

        # …but only under ITS OWN App. A deleted item addressed under another
        # App is a wrong-slug 404 like any other: "some App holds this id" is
        # not a stranger's to learn from a gate that authorizes nobody. The
        # mutation probe is what found this case missing — dropping the slug
        # test from the 410 branch changed nothing that any test could see.
        gone_elsewhere = client.get(f"/a/pm/items/{alive}/tools")
        assert gone_elsewhere.status_code == 404, (
            f"a deleted item leaked its App: {gone_elsewhere.status_code} {gone_elsewhere.text}"
        )


def test_a_delegate_can_close_a_deleted_items_environment():
    """Round-8 S1 — the row this page argues hardest for was the one a manager
    could not act on.

    Closing is what a `change_permission` grant is FOR: §1.4 makes closing the
    only way to resize a live environment, and the panel draws that person the
    button. But the delegate branch resolved the App slug with a lookup that
    reports a soft-deleted item as absent, so the slug came out `""` and the
    check refused — and once the gates started answering 410 for a deleted item,
    they refused it a second way.

    Closing is a BILLING action, not an operation on the item: the sandbox is
    running and somebody is paying for it, exactly as the resources page says
    two lines above. So this path resolves the item the way the ledger does and
    deliberately does not refuse a deleted one.
    """
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        WHO["id"] = "alice"
        item = _mk(
            spec,
            "alice",
            permission=_restricted(
                read_meta=["user:bob"], read_chat=["user:bob"], change_permission=["user:bob"]
            ),
        )
        _wake(client, item)
        assert client.delete(f"/rca-investigation/{item}").status_code in (200, 204)

        WHO["id"] = "bob"
        closed = client.delete(f"/me/resources/live/{item}")

        assert closed.status_code == 204, (
            f"a manager could not close the deleted item's environment: {closed.text}"
        )


def test_the_slug_must_match_the_item_at_every_gate():
    """Round-10 finding 2, corrected by round 11 — #95 ("a wrong slug can't
    operate on another App's item") is tested at TWO carriers, because it lives
    at two.

    THE MAP, grepped rather than remembered (three claims in this branch got it
    wrong, including the commit message that introduced this test):

    * ``/resources`` and ``/environment`` -> ``_authorize_item`` ->
      `require_item_access` -> `check_access`
    * ``/exec`` -> `ItemLocator.require_access` -> `check_access`
    * ``/tools`` -> `ItemLocator.require_item`, which never reaches
      `check_access` at all — it answers from ONE read and compares the slug
      itself

    So a mutation of `check_access` leaves ``/tools`` green and a mutation of
    `require_item` leaves the other three green. The comparison is now the
    shared `app_matches`, which is what makes ONE mutation reach both; the
    ``/tools`` half is asserted in
    `test_a_live_item_under_the_wrong_app_is_not_found_rather_than_gone`.

    Every gate is asserted INDEPENDENTLY. The first version asserted in
    sequence, and under the mutation the first request raised out of the client
    — so the other two assertions never ran and "verified red" evidenced only
    one of them.
    """
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, WHO["id"])

        def _status(call) -> int | str:
            """The status, or the exception — a gate that CRASHES on a wrong slug
            has not enforced anything either, and swallowing that would make the
            next mutation look guarded."""
            try:
                return call().status_code
            except Exception as exc:  # noqa: BLE001
                return type(exc).__name__

        got = {
            # require_item_access
            "resources": _status(
                lambda: client.put(f"/a/pm/items/{item}/resources", json={"cpu_cores": 1.0})
            ),
            "environment": _status(lambda: client.get(f"/a/pm/items/{item}/environment")),
            # require_access
            "exec": _status(
                lambda: client.post(f"/a/pm/items/{item}/exec", json={"cmd": ["echo"]})
            ),
        }

        assert got == {"resources": 404, "environment": 404, "exec": 404}, got

        # …and the same routes still work under the RIGHT slug, so this pins the
        # pairing rather than "everything 404s".
        assert client.get(f"/a/rca/items/{item}/environment").status_code == 200
        assert client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo"]}).status_code == 200


def test_editing_the_member_roster_needs_more_than_editing_the_item():
    """The guard-sweep's worst survivor: the rule was right and nothing pinned it.

    `PUT /members` is gated on `change_permission`, and it has to be, because
    `_reconcile_member_grants` writes PARTICIPANT GRANTS — `read_meta`,
    `read_chat`, `read_content`, `converse` — for whoever is on the roster.
    Editing members grants ACCESS.

    Weaken it to `write_meta` and two doors open: on a restricted item a
    collaborator who may edit fields but may NOT read the chat can put themselves
    on the roster and hand themselves entry; on a PUBLIC item `write_meta` is
    granted to everyone, so any stranger can rewrite the roster and strip the
    people already on it.

    Nothing in 499 tests noticed that mutation. The route's own docstring argues
    for the verb ("editing members now grants ACCESS") — an argument is not an
    assertion.
    """
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        WHO["id"] = "alice"
        item = _mk(
            spec,
            "alice",
            permission=_restricted(
                read_meta=["user:bob"],
                read_content=["user:bob"],
                write_meta=["user:bob"],
            ),
        )

        WHO["id"] = "bob"  # may edit the item; may not decide who gets in
        refused = client.put(f"/a/rca/items/{item}/members", json={"members": ["bob"]})

        assert refused.status_code == 403, (
            f"a write_meta collaborator rewrote the roster: {refused.status_code} {refused.text}"
        )

        WHO["id"] = "alice"  # the owner may
        allowed = client.put(f"/a/rca/items/{item}/members", json={"members": ["bob"]})
        assert allowed.status_code == 200, allowed.text


def test_a_stranger_cannot_rewrite_a_public_items_roster():
    """The other door the same verb closes, and the wider one: `write_meta` on a
    PUBLIC item is granted to anybody at all, so gating the roster on it would
    let a passer-by grant themselves participant access — or drop everyone who
    already had it."""
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        WHO["id"] = "alice"
        item = _mk(spec, "alice", permission=Permission(visibility="public"))

        WHO["id"] = "mallory"
        refused = client.put(f"/a/rca/items/{item}/members", json={"members": ["mallory"]})

        assert refused.status_code == 403, (
            f"a stranger rewrote a public item's roster: {refused.status_code} {refused.text}"
        )


def test_an_unknown_app_is_not_found_rather_than_a_crash():
    """A bogus slug on the resize route must be refused BEFORE anything tries to
    resolve the App — `app_model` raises `KeyError` for an unregistered slug, so
    the order of these two lines is the difference between a 404 and a 500 on a
    request anybody can send."""
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, WHO["id"])

        got = client.put(f"/a/no-such-app/items/{item}/resources", json={"cpu_cores": 1.0})

        assert got.status_code == 404, f"unknown app answered {got.status_code}: {got.text}"


def test_a_create_cannot_name_somebody_else_as_the_debtor():
    """`owner` comes from auth at create, never from the body — the same door
    the sandbox sizes are barred at.

    #687 concedes that `owner` can be repointed AFTERWARDS by anyone with write
    access, so this guard does not make the debtor tamper-proof; it keeps the
    create path from being a second, quieter way to do it, and it is the half
    that is cheap to hold. A guard whose sibling rule is already accepted as
    broken still deserves the test, or it will be removed as redundant by
    somebody reading only §4."""
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        WHO["id"] = "mallory"

        made = client.post("/a/rca/items", json={"title": "t", "owner": "alice"})
        assert made.status_code in (200, 201), made.text

        item_id = made.json()["resource_id"]
        rm = spec.get_resource_manager(RcaInvestigation)
        stored = rm.get(item_id).data
        assert isinstance(stored, RcaInvestigation)

        assert stored.owner == "mallory", (
            f"a create named somebody else as the debtor: {stored.owner!r}"
        )


def test_a_failing_group_lookup_still_produces_a_refusal_not_a_crash(monkeypatch):
    """Two belts, one rule, and neither was pinned: "a refusal must not become a
    500" (`_titles_of`, inside the 507 handler) and "a listing must not 500 on
    this" (`_describer`, on `GET /me/resources`).

    Both run a `groups_of` query to decide which held environments this reader
    may NAME. A store hiccup or an unmigrated group index there turns the two
    things a person at their limit has to work with — the refusal that explains
    it and the page that lets them act — into 500s. The rows degrade to unnamed
    instead; that is the whole point of the try/except, and it was written into
    both comments and asserted in neither.
    """
    from workspace_app.api import app as app_mod
    from workspace_app.api import quota_routes as quota_mod

    def _boom(*_args: object, **_kwargs: object):
        raise RuntimeError("group index is unavailable")

    with _app(PerUserResources(count=1), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        first = _mk(spec, WHO["id"])
        second = _mk(spec, WHO["id"])
        _wake(client, first)

        monkeypatch.setattr(app_mod, "groups_of", _boom)
        monkeypatch.setattr(quota_mod, "groups_of", _boom)

        refused = client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo"]})
        page = client.get("/me/resources")

        assert refused.status_code == 507, (
            f"a failing group lookup turned the refusal into {refused.status_code}: {refused.text}"
        )
        assert [h["item_id"] for h in refused.json()["detail"]["holding"]] == [first]
        assert page.status_code == 200, (
            f"a failing group lookup took down the usage page: {page.status_code} {page.text}"
        )
        assert any(e["item_id"] == first for e in page.json()["live"])


def test_the_item_fact_memo_is_a_cache_and_not_a_map(monkeypatch):
    """The THIRD unbounded-memo carrier, found by asking the same question of
    the sibling that got it right.

    `_item_facts` never evicts by TTL — an entry ages out of being TRUSTED but
    stays in the dict — so this whole-dict clear is the only thing between a
    long-lived pod and one entry per item id it has ever touched. Its bound was
    the counter-example that found the other two (`ItemLocator._access` and
    `_groups`), and it had no test of its own either.
    """
    from workspace_app.api import app as app_mod

    monkeypatch.setattr(app_mod, "_ITEM_FACT_MAX", 8)
    with _app(PerUserResources(cpu=100.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sandbox,
    ):
        for _ in range(app_mod._ITEM_FACT_MAX + 20):
            item = _mk(spec, WHO["id"])
            assert client.get(f"/a/rca/items/{item}/environment").status_code == 200

        # `TestClient.app` is typed as a bare ASGI callable, so narrow it to the
        # FastAPI it really is rather than reaching through an `Any`: `ty` is a
        # third viewpoint here, and silencing it would give up the one check
        # that noticed.
        served = client.app
        assert isinstance(served, FastAPI)
        facts = served.state.item_facts
        assert len(facts) <= app_mod._ITEM_FACT_MAX + 1, len(facts)
