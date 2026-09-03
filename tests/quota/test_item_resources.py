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
        assert holding[0]["title"] == "", "no title for a row whose item is gone"
