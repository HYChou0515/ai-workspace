"""P7 + P8's backend — "what am I holding, what may I hold", and the admin
override.

P7's acceptance conditions: changing one person's allowance changes only theirs;
no override means the deploy default; and it takes effect without a restart —
which is what the gate resolving limits per check buys.

P8's panel needs one call that answers both halves at once. A panel that fetched
usage and limits separately could render a pair that never coexisted.
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

from ..api._client import TestClient as ApiTestClient

ONE_CORE = ResourceLimits(cpu_cores=1.0, memory_bytes=512 * 1024**2, disk_bytes=0)


@contextlib.contextmanager
def _app(
    limits: PerUserResources,
    *,
    me: str = "alice",
    app_resources: dict[str, ResourceLimits] | None = None,
) -> Iterator[tuple[ApiTestClient, SpecStar]]:
    spec = make_spec()
    app = create_app(
        spec=spec,
        # A backend that enforces ceilings of its own — which is what every
        # production backend does. A mock enforcing nothing cannot show what an
        # App that declares nothing actually costs its owner.
        sandbox=MockSandbox(cpu_cores=2.0, memory_bytes=256 * 1024**2),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        workspace_quota=0,
        app_resources=app_resources or {"rca": ONE_CORE},
        per_user_resources=limits,
        get_user_id=lambda: me,
        superusers=frozenset({"root"}),
    )
    with ApiTestClient(app) as client:
        yield client, spec


def _mk(spec: SpecStar, owner: str) -> str:
    return (
        spec.get_resource_manager(RcaInvestigation)
        .create(RcaInvestigation(title="my item", owner=owner))
        .resource_id
    )


def test_the_panel_reports_usage_and_limits_together():
    with _app(PerUserResources(count=3, disk="1G")) as (client, spec):
        item = _mk(spec, "alice")
        client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo", "hi"]})
        client.put(f"/a/rca/items/{item}/files/a.bin", content=b"x" * 40)

        got = client.get("/me/resources")
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["owner"] == "alice"
        assert body["limits"]["count"] == 3
        assert body["limits"]["disk_bytes"] == 1024**3
        # the live environment is named, not just counted — a list of things to
        # close is useless if you cannot tell which is which
        assert [e["item_id"] for e in body["live"]] == [item]
        assert body["live"][0]["title"] == "my item"
        assert body["live"][0]["slug"] == "rca"
        assert body["disk_in_use"] == 40


def test_closing_an_environment_frees_the_slot():
    """P8's acceptance loop: refused → open the panel → close → the same thing
    now works."""
    with _app(PerUserResources(count=1)) as (client, spec):
        first = _mk(spec, "alice")
        second = _mk(spec, "alice")
        client.post(f"/a/rca/items/{first}/exec", json={"cmd": ["echo", "hi"]})
        refused = client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo", "hi"]})
        assert refused.status_code == 507

        assert client.delete(f"/me/resources/live/{first}").status_code == 204

        assert (
            client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo", "hi"]}).status_code
            == 200
        )


def test_you_cannot_close_someone_elses_environment():
    with _app(PerUserResources(count=5)) as (client, spec):
        theirs = _mk(spec, "bob")
        client.post(f"/a/rca/items/{theirs}/exec", json={"cmd": ["echo", "hi"]})
        assert client.delete(f"/me/resources/live/{theirs}").status_code == 404


def test_a_refused_close_leaves_the_other_person_s_environment_alone():
    """404 must be a refusal, not a partial close.

    The check that answers 404 and the code that clears the row live in
    different places, so a clear that ran first — or ran regardless — would take
    someone else's environment off their panel while it kept running, and they
    would have no way to get it back."""
    # count=1, so bob holding one environment means his next one is refused.
    # That refusal is the only thing that can tell "his row survived" apart from
    # "his row was cleared" — with a generous limit both look identical.
    with _app(PerUserResources(count=1)) as (client, spec):  # me == alice
        theirs = _mk(spec, "bob")
        client.post(f"/a/rca/items/{theirs}/exec", json={"cmd": ["echo", "hi"]})

        assert client.delete(f"/me/resources/live/{theirs}").status_code == 404

        another = _mk(spec, "bob")
        assert (
            client.post(f"/a/rca/items/{another}/exec", json={"cmd": ["echo", "hi"]}).status_code
            == 507
        ), "the refused close released bob's slot"


# ─── the admin override ────────────────────────────────────────────────


def test_an_override_changes_only_that_person():
    with _app(PerUserResources(count=1), me="root") as (client, spec):
        assert client.put("/admin/user-resources/alice", json={"count": 9}).status_code == 204
        assert client.get("/admin/user-resources/alice").json()["limits"]["count"] == 9
        assert client.get("/admin/user-resources/bob").json()["limits"]["count"] == 1


def test_an_override_takes_effect_without_a_restart():
    """The gate resolves limits per check, so a raised allowance applies to the
    very next turn."""
    with _app(PerUserResources(count=1), me="root") as (client, spec):
        first = _mk(spec, "root")
        second = _mk(spec, "root")
        client.post(f"/a/rca/items/{first}/exec", json={"cmd": ["echo", "hi"]})
        assert (
            client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo", "hi"]}).status_code
            == 507
        )

        assert client.put("/admin/user-resources/root", json={"count": 5}).status_code == 204

        assert (
            client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo", "hi"]}).status_code
            == 200
        )


def test_clearing_an_override_falls_back_to_the_deploy_default():
    with _app(PerUserResources(count=1), me="root") as (client, _spec):
        client.put("/admin/user-resources/alice", json={"count": 9})
        assert client.delete("/admin/user-resources/alice").status_code == 204
        assert client.get("/admin/user-resources/alice").json()["limits"]["count"] == 1


def test_an_override_is_per_dimension():
    """Setting only `count` must leave the other dimensions on the deploy
    default — an exception grants what it names and nothing else."""
    with _app(PerUserResources(count=1, disk="1G"), me="root") as (client, _spec):
        client.put("/admin/user-resources/alice", json={"count": 9})
        limits = client.get("/admin/user-resources/alice").json()["limits"]
        assert limits["count"] == 9
        assert limits["disk_bytes"] == 1024**3


def test_the_admin_can_see_who_is_above_the_default_without_knowing_their_id():
    """The by-id read only answers "does THIS person have one", so an operator
    could only find an exception they already knew about."""
    with _app(PerUserResources(count=1, disk="1G"), me="root") as (client, _spec):
        client.put("/admin/user-resources/alice", json={"count": 9})
        client.put("/admin/user-resources/bob", json={"memory": "8G"})

        body = client.get("/admin/user-resources").json()
        assert [o["user_id"] for o in body["overrides"]] == ["alice", "bob"]
        # RAW, not merged: bob has no count exception, so it reads 0 rather than
        # the deploy's 1 — otherwise every row would look overridden everywhere.
        by_id = {o["user_id"]: o for o in body["overrides"]}
        assert (by_id["alice"]["count"], by_id["alice"]["memory"]) == (9, "")
        assert (by_id["bob"]["count"], by_id["bob"]["memory"]) == (0, "8G")
        # and the baseline they are exceptions TO, so a number means something
        assert body["defaults"] == {
            "count": 1,
            "cpu": 0.0,
            "memory_bytes": 0,
            "disk_bytes": 1024**3,
        }


def test_a_revoked_exception_leaves_the_list():
    """`clear_for` SOFT-deletes and `list_resources` returns soft-deleted rows,
    so without an `is_deleted` filter the page would keep reporting privileges
    nobody holds — the same trap the activity ledger documents."""
    with _app(PerUserResources(count=1), me="root") as (client, _spec):
        client.put("/admin/user-resources/alice", json={"count": 9})
        assert len(client.get("/admin/user-resources").json()["overrides"]) == 1

        client.delete("/admin/user-resources/alice")
        assert client.get("/admin/user-resources").json()["overrides"] == []


def test_only_an_admin_can_list_the_exceptions():
    """404 rather than 403, like the by-id read: whether anyone has an exception
    is not something an ordinary caller should be able to probe."""
    with _app(PerUserResources(count=1), me="alice") as (client, _spec):
        assert client.get("/admin/user-resources").status_code == 404


def test_a_non_admin_cannot_read_or_set_anyones_limits():
    """404 rather than 403 — whether a person has an exception is not something
    to let a non-admin probe for."""
    with _app(PerUserResources(count=1)) as (client, _spec):  # me == alice
        assert client.get("/admin/user-resources/bob").status_code == 404
        assert client.put("/admin/user-resources/bob", json={"count": 9}).status_code == 404
        assert client.delete("/admin/user-resources/bob").status_code == 404


def test_a_live_environment_is_charged_what_the_backend_really_caps_it_at():
    """An App that declares nothing is not free.

    Its `SandboxSpec` carries `None`, which means "backend, use your own
    ceiling" — and the backend does: production caps every sandbox at
    `SANDBOX_HOST_CPU_CORES` / `MEMORY_MAX`, a local deploy at
    `sandbox.isolation.*`. The tally read the SPEC, so it charged 0: the panel
    showed "CPU 0" beside a live environment, and a per-person cpu/memory cap
    summed those zeros and could never bind."""
    undeclared = ResourceLimits(cpu_cores=None, memory_bytes=None, disk_bytes=0)
    with _app(PerUserResources(count=3), app_resources={"rca": undeclared}) as (client, spec):
        item = _mk(spec, "alice")
        client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo", "hi"]})

        got = client.get("/me/resources").json()
        assert got["live"][0]["cpu_cores"] == 2.0
        assert got["cpu_in_use"] == 2.0
        assert got["memory_in_use"] == 256 * 1024**2


def test_closing_frees_the_slot_and_stops_listing_it_together():
    """The panel's list and the machine must move as ONE.

    The route used to clear the heartbeat itself, right after asking the registry
    to close. When the close quietly did nothing — no session on this replica,
    which is every request after a restart — the row was cleared anyway: the
    environment vanished from the panel while still running, and with nothing
    left to click there was no way to try again. Whoever clears the row has to be
    whoever killed the sandbox."""
    with _app(PerUserResources(count=1)) as (client, spec):
        first = _mk(spec, "alice")
        second = _mk(spec, "alice")
        client.post(f"/a/rca/items/{first}/exec", json={"cmd": ["echo", "hi"]})
        assert [e["item_id"] for e in client.get("/me/resources").json()["live"]] == [first]

        assert client.delete(f"/me/resources/live/{first}").status_code == 204

        # gone from the panel…
        assert client.get("/me/resources").json()["live"] == []
        # …and the slot it was holding is genuinely free again
        assert (
            client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo", "hi"]}).status_code
            == 200
        )


def _forget_heartbeat(spec: SpecStar, item_id: str) -> None:
    """Lose the ledger row while the sandbox keeps running — the state every one
    of these tests is about, reached the way production reaches it."""
    from workspace_app.api.sandbox_activity import SpecstarActivityStore

    SpecstarActivityStore(spec)._forget_sync(item_id)


def test_the_panel_lists_an_environment_no_ledger_row_names():
    """The symptom: nothing on the page, and the sandbox is still running.

    The list was drawn entirely from the heartbeat ledger, which is belief, and
    belief goes missing — a pod that died between create and its first bump, a
    row cleared by a close that killed nothing, a heartbeat that simply aged out
    of the window while the sandbox kept running. Whatever the cause, the
    environment vanished from the one page that offers a Close button, so there
    was no longer anything to click, and it went on costing its owner.

    Asking the backend what it is really running is the only cure, because no
    record can be checked against another record."""
    with _app(PerUserResources(count=3)) as (client, spec):
        item = _mk(spec, "alice")
        client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo", "hi"]})
        # the ledger loses it, the machine does not
        _forget_heartbeat(spec, item)
        assert client.get("/me/resources").json()["live"] != [], (
            "the panel only knew what it had written down"
        )


def test_an_environment_found_that_way_also_starts_counting_again():
    """A panel that is honest while the gate stays blind is half a fix.

    The limit counts the ledger, not the page. Re-arming the heartbeat is what
    makes the two agree: the environment is running, so it costs, so the next
    one is refused — which is also what makes the Close button on that row
    worth pressing."""
    with _app(PerUserResources(count=1)) as (client, spec):
        first = _mk(spec, "alice")
        second = _mk(spec, "alice")
        client.post(f"/a/rca/items/{first}/exec", json={"cmd": ["echo", "hi"]})
        _forget_heartbeat(spec, first)

        # opening the panel finds it…
        assert [e["item_id"] for e in client.get("/me/resources").json()["live"]] == [first]
        # …and it is charged again, so the slot really is taken
        assert (
            client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo", "hi"]}).status_code
            == 507
        )


def test_the_panel_does_not_list_someone_elses_environment():
    """The backend answers about every sandbox on the pod that took the request,
    not just mine. Attributing one of them to the reader would show a stranger's
    work on their page — and hand them a Close button for it."""
    with _app(PerUserResources(count=3)) as (client, spec):  # me == alice
        theirs = _mk(spec, "bob")
        client.post(f"/a/rca/items/{theirs}/exec", json={"cmd": ["echo", "hi"]})
        _forget_heartbeat(spec, theirs)

        assert client.get("/me/resources").json()["live"] == []


def test_a_close_that_cannot_be_done_right_now_says_so():
    """Silence is what made this button unreliable.

    A busy host is reachable but not answering yet — the sandbox is very much
    alive (#492 keeps that case apart from a missing one precisely so it is not
    rebuilt or written off). Answering 204 there tells the person it worked;
    they watch the row stay and read the button as broken. 503 with Retry-After
    says what it is, and every record stays put so the retry has something to
    act on."""
    from workspace_app.sandbox.protocol import SandboxBusy

    class _BusyOnKill(MockSandbox):
        async def kill(self, handle):
            raise SandboxBusy(handle.id)

    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=_BusyOnKill(cpu_cores=2.0, memory_bytes=256 * 1024**2),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        workspace_quota=0,
        app_resources={"rca": ONE_CORE},
        per_user_resources=PerUserResources(count=3),
        get_user_id=lambda: "alice",
    )
    with ApiTestClient(app) as client:
        item = _mk(spec, "alice")
        client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo", "hi"]})

        refused = client.delete(f"/me/resources/live/{item}")
        assert refused.status_code == 503, refused.text
        # …and the row is still there to press again
        assert [e["item_id"] for e in client.get("/me/resources").json()["live"]] == [item]


def test_closing_a_sandbox_that_is_already_gone_clears_the_row():
    """An operator deleting a sandbox out of band is a supported thing to do,
    and the panel has to catch up rather than argue.

    `kill` then raises `SandboxNotFound`, which is the GOAL — the same reading
    `kill_idle` gives it before forgetting the heartbeat. Treating it as "found
    it, could not confirm" instead left the owner charged for an environment
    that did not exist, with a Close button that refused for the whole idle
    window (8 hours by default)."""
    from workspace_app.sandbox.protocol import SandboxNotFound

    class _AlreadyGone(MockSandbox):
        async def kill(self, handle):
            raise SandboxNotFound(handle.id)

    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=_AlreadyGone(cpu_cores=2.0, memory_bytes=256 * 1024**2),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        workspace_quota=0,
        app_resources={"rca": ONE_CORE},
        per_user_resources=PerUserResources(count=1),
        get_user_id=lambda: "alice",
    )
    with ApiTestClient(app) as client:
        first = _mk(spec, "alice")
        second = _mk(spec, "alice")
        client.post(f"/a/rca/items/{first}/exec", json={"cmd": ["echo", "hi"]})

        assert client.delete(f"/me/resources/live/{first}").status_code == 204
        # the slot it was holding is genuinely free again, on the FIRST press
        assert (
            client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo", "hi"]}).status_code
            == 200
        )


def test_closing_something_that_was_never_running_is_not_an_error():
    """Nothing to close is not a failure to close.

    Every item has a Close-able row only while it holds a sandbox, but a stale
    page, a double click, or a workflow pausing an item that never ran one all
    arrive here with nothing to do. Answering 409 would teach people that the
    button is broken."""
    with _app(PerUserResources(count=3)) as (client, spec):
        item = _mk(spec, "alice")  # no exec: nothing was ever created
        assert client.delete(f"/me/resources/live/{item}").status_code == 204


def test_one_environment_is_charged_once_even_if_the_host_lists_it_twice():
    """An item can hold two live sandboxes for a moment — a #366 CAS loser
    before it kills its orphan, or a rebuild after a probe read a transport blip
    as death — and the host names each of them separately.

    Counting both puts "2 / 1" on the panel beside one environment and doubles
    the cpu and memory it reports, which is the gauge telling the person to free
    something that is not there."""
    from workspace_app.sandbox.protocol import RunningSandbox, SandboxHandle

    class _DoubleListing(MockSandbox):
        async def running_sandboxes(self):
            listed = await super().running_sandboxes() or []
            return [
                *listed,
                *(
                    RunningSandbox(
                        handle=SandboxHandle(id=f"{e.handle.id}-twin"), item_id=e.item_id
                    )
                    for e in listed
                ),
            ]

    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=_DoubleListing(cpu_cores=2.0, memory_bytes=256 * 1024**2),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        workspace_quota=0,
        app_resources={"rca": ONE_CORE},
        per_user_resources=PerUserResources(count=3),
        get_user_id=lambda: "alice",
    )
    with ApiTestClient(app) as client:
        item = _mk(spec, "alice")
        client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo", "hi"]})
        _forget_heartbeat(spec, item)

        body = client.get("/me/resources").json()
        assert [e["item_id"] for e in body["live"]] == [item]
        assert body["cpu_in_use"] == 1.0
