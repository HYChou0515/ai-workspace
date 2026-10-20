"""P4 of `docs/plan-env-token-refresh.md`: refresh a value that is about to be
used and near expiry (D2), write the new values back to the layer they came
from (D3), and read a failure only through the reporting contract (D5).

The stores are the real ones over an in-memory spec; the provider is the
deploy's side, scripted per test.
"""

from __future__ import annotations

import asyncio

import pytest

from workspace_app.api.env_layers import PersonEnv
from workspace_app.api.env_provider import IEnvProvider, InputField, RefreshRejected
from workspace_app.api.env_refresh import refresh_if_due
from workspace_app.api.private_env import PrivateEnvStore, register_private_env
from workspace_app.api.refresh_claims import RefreshClaims, register_refresh_claims
from workspace_app.resources import make_spec


class _Erp(IEnvProvider):
    """Mints ERP_TOKEN with ERP_REFRESH; "old" is near expiry."""

    def __init__(self, *, outcome: object = None, rotate: bool = True) -> None:
        self.calls: list[dict[str, str]] = []
        self.checked: list[dict[str, str]] = []
        self._outcome = outcome
        self._rotate = rotate

    @property
    def id(self) -> str:
        return "erp"

    @property
    def label(self) -> str:
        return "ERP"

    @property
    def produces(self) -> frozenset[str]:
        return frozenset({"ERP_TOKEN", "ERP_REFRESH"})

    @property
    def inputs(self) -> tuple[InputField, ...]:
        return ()

    async def resolve(
        self, values: dict[str, str]
    ) -> dict[str, str]:  # pragma: no cover - not this flow
        return {}

    def needs_refresh(self, values: dict[str, str]) -> bool:
        self.checked.append(dict(values))
        return values.get("ERP_TOKEN") == "old"

    async def refresh(self, values: dict[str, str]) -> dict[str, str]:
        self.calls.append(dict(values))
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        if self._outcome == "hang":
            await asyncio.sleep(10)
        new = {"ERP_TOKEN": "new"}
        if self._rotate:
            new["ERP_REFRESH"] = "r2"
        return new


class _NoRefresh(_Erp):
    """A deploy written before refresh: its methods are the defaults."""

    needs_refresh = IEnvProvider.needs_refresh
    refresh = IEnvProvider.refresh


class _World:
    def __init__(self) -> None:
        spec = make_spec()
        register_private_env(spec)
        register_refresh_claims(spec)
        self.store = PrivateEnvStore(spec)
        self.claims = RefreshClaims(spec)
        self.shared: dict[str, dict[str, str]] = {}

    def read_shared(self, item_id: str) -> dict[str, str]:
        return dict(self.shared.get(item_id, {}))

    def write_shared(self, item_id: str, values: dict[str, str]) -> None:
        self.shared[item_id] = dict(values)

    async def run(
        self,
        providers: list[IEnvProvider],
        *,
        user: str = "alice",
        item: str = "i1",
        policy: dict[str, str] | None = None,
        service: dict[str, str] | None = None,
        timeout_s: float = 5.0,
        wait_s: float = 0.2,
    ):
        typed = self.store.get(user, item) if user else {}
        seam = self.store.seam(user, item) if user else {}
        person = PersonEnv(
            own={**typed, **seam},
            personal=self.store.personal(user) if user else {},
            service=service or {},
        )
        return await refresh_if_due(
            providers=providers,
            store=self.store,
            claims=self.claims,
            read_shared=self.read_shared,
            write_shared=self.write_shared,
            shared=self.read_shared(item),
            person=person,
            policy=policy or {},
            user_id=user,
            item_id=item,
            timeout_s=timeout_s,
            wait_s=wait_s,
        )


OLD = {"ERP_TOKEN": "old", "ERP_REFRESH": "r1"}


async def test_a_value_that_is_not_near_expiry_is_left_alone():
    w = _World()
    w.store.replace_personal("alice", {"ERP_TOKEN": "fresh", "ERP_REFRESH": "r1"})
    erp = _Erp()

    out = await w.run([erp])

    assert erp.calls == []
    assert erp.checked == [{"ERP_TOKEN": "fresh", "ERP_REFRESH": "r1"}]
    assert out.person.personal == {"ERP_TOKEN": "fresh", "ERP_REFRESH": "r1"}


async def test_my_variables_are_refreshed_and_written_back_there():
    w = _World()
    w.store.replace_personal("alice", {**OLD, "OTHER": "o"})
    erp = _Erp()

    out = await w.run([erp])

    assert erp.calls == [OLD]
    assert w.store.personal("alice") == {"ERP_TOKEN": "new", "ERP_REFRESH": "r2", "OTHER": "o"}
    assert out.person.personal["ERP_TOKEN"] == "new"
    assert out.rejected == []


async def test_this_items_typed_values_are_refreshed_and_written_back_there():
    w = _World()
    w.store.replace("alice", "i1", {**OLD, "MINE": "m"})

    out = await w.run([_Erp()])

    assert w.store.get("alice", "i1") == {"ERP_TOKEN": "new", "ERP_REFRESH": "r2", "MINE": "m"}
    assert out.person.own["ERP_TOKEN"] == "new"
    assert w.store.personal("alice") == {}


async def test_the_shared_copy_is_refreshed_and_written_back_by_the_platform():
    """D3: a shared service token is everyone's, so its new value is too —
    whoever the run is for."""
    w = _World()
    w.shared["i1"] = {**OLD, "HOST": "h"}

    out = await w.run([_Erp()])

    assert w.shared["i1"] == {"ERP_TOKEN": "new", "ERP_REFRESH": "r2", "HOST": "h"}
    assert out.shared["ERP_TOKEN"] == "new"


async def test_a_value_the_seam_wrote_is_not_refreshed():
    """The deploy's IRequestEnv rewrites it on every request; there is no row
    of ours to write a refreshed one into."""
    w = _World()
    w.store.record_seam("alice", "i1", OLD)
    erp = _Erp()

    await w.run([erp])

    assert erp.calls == []


async def test_the_service_account_is_not_refreshed():
    w = _World()
    erp = _Erp()

    await w.run([erp], user="", service=OLD)

    assert erp.calls == []


async def test_values_split_across_layers_are_not_refreshed():
    """No single layer to write the new set back to."""
    w = _World()
    w.store.replace_personal("alice", {"ERP_TOKEN": "old"})
    w.shared["i1"] = {"ERP_REFRESH": "r1"}
    erp = _Erp()

    await w.run([erp])

    assert erp.calls == []


async def test_a_deploy_without_refresh_is_never_asked():
    w = _World()
    w.store.replace_personal("alice", OLD)
    plain = _NoRefresh()

    out = await w.run([plain])

    assert plain.checked == [] and plain.calls == []
    assert out.person.personal == OLD


async def test_a_rejected_refresh_token_clears_the_names_the_deploy_gave():
    """D5: the deploy says which variables died; the platform clears exactly
    those it produces and reports the provider for D4/D6."""
    w = _World()
    w.store.replace_personal("alice", {**OLD, "OTHER": "o"})
    erp = _Erp(outcome=RefreshRejected({"ERP_TOKEN", "ERP_REFRESH", "OTHER"}))

    out = await w.run([erp])

    # OTHER is not ERP's to clear, though the deploy named it.
    assert w.store.personal("alice") == {"OTHER": "o"}
    assert [(d.provider.id, d.layer) for d in out.rejected] == [("erp", "personal")]
    assert "ERP_TOKEN" not in out.person.personal


async def test_a_rejection_in_this_items_values_reaches_the_tool_too():
    """The tool's env is composed from what this returns: a cleared name must
    be gone from it, not only from the store."""
    w = _World()
    w.store.replace("alice", "i1", {**OLD, "MINE": "m"})
    erp = _Erp(outcome=RefreshRejected({"ERP_TOKEN", "ERP_REFRESH"}))

    out = await w.run([erp])

    assert w.store.get("alice", "i1") == {"MINE": "m"}
    assert out.person.own == {"MINE": "m"}


async def test_values_changed_under_the_claim_but_still_due_are_the_ones_sent():
    """The refresh token sent is the one stored now, not the one read before
    the claim — that one may already be spent."""
    w = _World()
    w.store.replace_personal("alice", {"ERP_TOKEN": "old", "ERP_REFRESH": "r9"})
    erp = _Erp()

    await refresh_if_due(
        providers=[erp],
        store=w.store,
        claims=w.claims,
        read_shared=w.read_shared,
        write_shared=w.write_shared,
        shared={},
        person=PersonEnv(personal=dict(OLD)),  # read before a peer rotated r1 → r9
        policy={},
        user_id="alice",
        item_id="i1",
    )

    assert erp.calls == [{"ERP_TOKEN": "old", "ERP_REFRESH": "r9"}]


async def test_a_composition_with_no_store_refreshes_nothing():
    erp = _Erp()
    person = PersonEnv(personal=dict(OLD))
    out = await refresh_if_due(
        providers=[erp],
        store=None,
        claims=None,
        read_shared=lambda _i: {},
        write_shared=lambda _i, _v: None,
        shared={},
        person=person,
        policy={},
        user_id="alice",
        item_id="i1",
    )
    assert erp.checked == [] and out.person is person


async def test_only_the_names_held_are_judged():
    """A name the sign-in produces that nobody holds is not part of what the
    tool gets, so it is not handed to needs_refresh."""
    w = _World()
    w.store.replace_personal("alice", {"ERP_TOKEN": "fresh"})
    erp = _Erp()

    await w.run([erp])

    assert erp.checked == [{"ERP_TOKEN": "fresh"}]


async def test_a_passing_failure_changes_nothing():
    w = _World()
    w.store.replace_personal("alice", OLD)
    erp = _Erp(outcome=RuntimeError("gateway 503"))

    out = await w.run([erp])

    assert w.store.personal("alice") == OLD
    assert out.person.personal == OLD
    assert out.rejected == []


async def test_a_refresh_past_the_platforms_bound_is_a_passing_failure():
    w = _World()
    w.store.replace_personal("alice", OLD)
    erp = _Erp(outcome="hang")

    out = await w.run([erp], timeout_s=0.05)

    assert w.store.personal("alice") == OLD
    assert out.rejected == []


async def test_a_needs_refresh_that_raises_reads_as_not_due():
    class _Broken(_Erp):
        def needs_refresh(self, values: dict[str, str]) -> bool:
            raise ValueError("cannot decode")

    w = _World()
    w.store.replace_personal("alice", OLD)
    erp = _Broken()

    out = await w.run([erp])

    assert erp.calls == []
    assert out.person.personal == OLD


async def test_two_runs_at_once_spend_a_rotating_refresh_token_once():
    """The second uses what the first wrote and does not send the refresh
    token again.

    Ordered by events, not by timing: the second run starts only once the
    first holds the claim and is inside `refresh`, so the result does not
    depend on how fast the machine is (the in-memory backend's create-if-absent
    is not atomic across threads, and a race there proved nothing). What this
    pins end to end is the re-read; the claim itself is pinned by
    `test_a_run_that_waits_too_long_goes_on_with_what_it_had` (a claim that
    always grants reddens it)."""
    w = _World()
    w.store.replace_personal("alice", OLD)
    inside = asyncio.Event()
    finish = asyncio.Event()

    class _Held(_Erp):
        async def refresh(self, values: dict[str, str]) -> dict[str, str]:
            inside.set()
            await finish.wait()
            return await super().refresh(values)

    erp = _Held()
    first_run = asyncio.create_task(w.run([erp], wait_s=5))
    await inside.wait()
    second_run = asyncio.create_task(w.run([erp], wait_s=5))
    await asyncio.sleep(0)  # let the second find the claim taken and start waiting
    finish.set()
    first, second = await asyncio.gather(first_run, second_run)

    assert len(erp.calls) == 1
    assert first.person.personal["ERP_TOKEN"] == "new"
    assert second.person.personal["ERP_TOKEN"] == "new"


async def test_a_run_that_waits_too_long_goes_on_with_what_it_had():
    """D4: never blocked — a holder that is slow (or dead) costs a wait, then
    the run carries the values it had."""
    w = _World()
    w.store.replace_personal("alice", OLD)
    held = w.claims.try_take("personal:alice:erp", ttl_ms=60_000)
    assert held is not None
    erp = _Erp()

    out = await w.run([erp], wait_s=0.05)

    assert erp.calls == []
    assert out.person.personal == OLD


async def test_a_value_someone_just_refreshed_is_not_refreshed_again():
    """Under the claim the layer is read again: a run that read the old
    values before a peer refreshed must not send the spent refresh token."""
    w = _World()
    w.store.replace_personal("alice", OLD)
    erp = _Erp()
    typed = PersonEnv(personal=dict(OLD))
    w.store.replace_personal("alice", {"ERP_TOKEN": "new", "ERP_REFRESH": "r2"})

    out = await refresh_if_due(
        providers=[erp],
        store=w.store,
        claims=w.claims,
        read_shared=w.read_shared,
        write_shared=w.write_shared,
        shared={},
        person=typed,
        policy={},
        user_id="alice",
        item_id="i1",
    )

    assert erp.calls == []
    assert out.person.personal["ERP_TOKEN"] == "new"


@pytest.mark.parametrize("policy", ["private_first", "private_only"])
async def test_under_a_private_policy_my_variables_are_what_gets_refreshed(policy):
    w = _World()
    w.store.replace_personal("alice", OLD)
    w.shared["i1"] = {"ERP_TOKEN": "shared-fresh", "ERP_REFRESH": "rs"}

    await w.run([_Erp()], policy={"ERP_TOKEN": policy, "ERP_REFRESH": policy})

    assert w.store.personal("alice")["ERP_TOKEN"] == "new"
    assert w.shared["i1"]["ERP_TOKEN"] == "shared-fresh"
