"""#700 — the handoff, played from the CALLING side.

Nothing about this feature is visible in our own UI: the picker lives in the
outside system's page, so "it merged" proves nothing. What has to hold is that
the sequence a legacy site actually issues — list, create or pick, upload,
record — works end to end against routes that already exist.

So this is a contract double: it models the other side's real request sequence,
including the parts we do not control (the exact `sorts` JSON, the raw-body PUT,
the RFC 6902 patch). A test that only asserted our handlers do not crash would
be immune to the regressions that matter here.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

_NEWEST_FIRST = json.dumps(
    [
        {"type": "meta", "key": "updated_time", "direction": "-"},
        # Tiebreaker: makes ONE query's order deterministic when timestamps
        # collide. It does NOT make `offset` paging safe — that needs an
        # immutable sort key, see the paging test below. Conflating the two is
        # the mistake this comment used to make.
        {"type": "meta", "key": "resource_id", "direction": "+"},
    ]
)

# What a caller must switch to in order to page past the first page. `created_time`
# never moves, so a concurrent handoff cannot slide the window between fetches.
_STABLE_FOR_PAGING = json.dumps(
    [
        {"type": "meta", "key": "created_time", "direction": "-"},
        {"type": "meta", "key": "resource_id", "direction": "+"},
    ]
)


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield RunDone()


class _LegacySite:
    """The calling side, expressed as the four moves it actually makes.

    Written as the caller sees the platform — no imports of our internals, no
    resource-manager peeking — so if a route, parameter name or status code
    drifts, this breaks in the same way the real integration would.
    """

    def __init__(self, client: TestClient, slug: str = "rca") -> None:
        self._c = client
        self._slug = slug

    def list_candidates(self, limit: int = 100) -> list[dict]:
        """The picker's only read: newest-touched first, explicitly capped.

        Reads the ENVELOPE listing, not the `/data` one. `/data` returns the
        struct alone — no resource id — so a picker built on it can render rows
        and then do nothing with the one the user clicks. The id lives in
        `revision_info`, which is also where this platform's own frontend reads
        it from (`web/src/api/real.ts`).
        """
        r = self._c.get(
            "/rca-investigation",
            params={"limit": limit, "sorts": _NEWEST_FIRST},
        )
        assert r.status_code == 200, r.text
        return [{"id": row["revision_info"]["resource_id"], **row["data"]} for row in r.json()]

    def already_absorbed(self, items: list[dict], ref: str) -> list[str]:
        """Answered from the page already fetched — never a query (the field is
        deliberately un-indexed, so a query would be silently wrong on SQL)."""
        return [it["title"] for it in items if ref in it["external_refs"]]

    def open_new_item(self, title: str) -> str:
        """Create the item WITHOUT the ref — it is recorded last, once the files
        are in.

        Naming the analysis at create time looks tidier and is a trap: a handoff
        that dies mid-upload leaves an item CLAIMING an analysis it does not
        hold, and because the picker greys out anything already absorbed, that
        analysis can never be retried into it. Recording last means a crash
        leaves "not yet recorded", and a re-run overwrites the same paths.
        """
        r = self._c.post(
            f"/a/{self._slug}/items",
            json={
                "title": title,
                # Explicit, because the default is private — an item nobody else
                # can see cannot be the thing colleagues converge on.
                "permission": {"visibility": "public"},
            },
        )
        assert r.status_code == 200, r.text
        return r.json()["resource_id"]

    def open_new_item_and_record(self, title: str, ref: str) -> str:
        """The whole 2b flow for tests that only need an item to exist: create,
        then record. Still in the correct order — never `external_refs` at
        create time."""
        item_id = self.open_new_item(title)
        self.record_absorbed(item_id, ref)
        return item_id

    def upload(self, item_id: str, ref: str, name: str, body: bytes) -> None:
        """One folder per source record, so several handoffs cannot collide."""
        folder = ref.replace(":", "-")
        r = self._c.put(f"/a/{self._slug}/items/{item_id}/files/{folder}/{name}", content=body)
        assert r.status_code == 204, r.text

    def record_absorbed(self, item_id: str, ref: str, *, attempts: int = 20) -> None:
        """Record the ref under an optimistic lock, and make the whole call safe
        to repeat.

        Two properties the naive one-shot append does not have:

        **No lost update.** The server's patch is a read-modify-write, so two
        appends landing together silently keep one and drop the other — both
        answering 200. `expected_revision_id` converts that into a 412 the
        caller can retry against fresh state.

        **Idempotent.** RFC 6902 `add` to `/-` appends unconditionally, so a
        caller that times out on a request which actually landed — the ordinary
        thing to do — would record the ref twice. Re-reading first and returning
        early when the ref is already present makes a retry a no-op, which is
        what the platform means by "at most once per item".
        """
        for _ in range(attempts):
            env = self._c.get(f"/rca-investigation/{item_id}")
            assert env.status_code == 200, env.text
            body = env.json()
            if ref in body["data"]["external_refs"]:
                return
            r = self._c.patch(
                f"/rca-investigation/{item_id}",
                params={"expected_revision_id": body["revision_info"]["revision_id"]},
                json=[{"op": "add", "path": "/external_refs/-", "value": ref}],
            )
            if r.status_code == 200:
                return
            assert r.status_code == 412, r.text  # 412 = someone else got there first; retry
        raise AssertionError(f"could not record {ref} after {attempts} attempts")

    def files_in(self, item_id: str) -> set[str]:
        r = self._c.get(f"/a/{self._slug}/items/{item_id}/files")
        assert r.status_code == 200, r.text
        return {e["path"] for e in r.json()}


def _app_for(who: dict[str, str]):
    """One store, a switchable caller — the same shape as two people sharing a
    deployment. `default_user` and `get_user_id` read the SAME source so the
    creator we record and the actor we authorize can never diverge."""
    spec = make_spec(default_user=lambda: who["user"])
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        agent_config_catalog=AgentConfigCatalog(),
        get_user_id=lambda: who["user"],
    )


def test_two_analyses_from_the_legacy_site_converge_on_one_item():
    """The whole point of #700: pressing the button twice for one real problem
    lands both analyses in ONE workspace instead of sprawling into two."""
    who = {"user": "alice"}
    legacy = _LegacySite(TestClient(_app_for(who)))

    # First analysis: nothing exists yet, so the user opens an item.
    assert legacy.list_candidates() == []
    item_id = legacy.open_new_item("Oven drift")
    legacy.upload(item_id, "legacy-rca:1", "readings.csv", b"t,v\n1,9\n")
    legacy.record_absorbed(item_id, "legacy-rca:1")

    # Second analysis, same real-world problem: the user picks that item.
    candidates = legacy.list_candidates()
    assert [c["title"] for c in candidates] == ["Oven drift"]
    assert legacy.already_absorbed(candidates, "legacy-rca:2") == []
    # Act on the row the USER CLICKED — the id has to come out of the listing,
    # not out of a create response the picker never sees. This is the move that
    # makes or breaks the integration, so the double must play it.
    picked = candidates[0]["id"]
    assert picked == item_id
    legacy.upload(picked, "legacy-rca:2", "pareto.png", b"\x89PNG")
    legacy.record_absorbed(picked, "legacy-rca:2")

    # One item holds both analyses, each in its own folder.
    absorbed = legacy.list_candidates()[0]["external_refs"]
    assert absorbed == ["legacy-rca:1", "legacy-rca:2"]
    assert legacy.files_in(item_id) >= {
        "/legacy-rca-1/readings.csv",
        "/legacy-rca-2/pareto.png",
    }


def test_a_second_press_of_the_same_analysis_is_recognisable_as_already_taken():
    """Decision 3: same record, same item ⇒ do nothing. The caller must be able
    to tell BEFORE uploading, or the user transfers megabytes for nothing."""
    who = {"user": "alice"}
    legacy = _LegacySite(TestClient(_app_for(who)))
    legacy.open_new_item_and_record("Oven drift", "legacy-rca:1")

    assert legacy.already_absorbed(legacy.list_candidates(), "legacy-rca:1") == ["Oven drift"]


def test_absorbing_an_analysis_pulls_the_item_back_to_the_front_of_the_page():
    """Decision 12 caps the listing, which re-opens the sprawl risk from the
    side: an item past the cap is invisible, so the user opens another one.
    Sorting by *last touched* is what closes it — an item being actively worked
    is pushed back to the front — so the ordering is load-bearing, not cosmetic.
    Created-time order would leave it buried exactly when it matters most."""
    who = {"user": "alice"}
    legacy = _LegacySite(TestClient(_app_for(who)))
    older = legacy.open_new_item_and_record("Oven drift", "legacy-rca:1")
    legacy.open_new_item_and_record("Coater streaks", "legacy-rca:2")

    assert [c["title"] for c in legacy.list_candidates()] == ["Coater streaks", "Oven drift"]

    legacy.record_absorbed(older, "legacy-rca:3")  # a second analysis lands on the older item

    assert [c["title"] for c in legacy.list_candidates()] == ["Oven drift", "Coater streaks"]


def test_a_stale_revision_is_refused_instead_of_silently_overwriting():
    """The precondition that makes a lost update *detectable*.

    Without it, two appends landing together both answer 200 and one simply
    vanishes. With it, the loser is told (412) and can retry against fresh
    state. This is the mechanism `record_absorbed` is built on, so it gets a
    deterministic test of its own rather than being inferred from a race.
    """
    who = {"user": "alice"}
    client = TestClient(_app_for(who))
    legacy = _LegacySite(client)
    item_id = legacy.open_new_item_and_record("Oven drift", "legacy-rca:0")
    stale = client.get(f"/rca-investigation/{item_id}").json()["revision_info"]["revision_id"]

    first = client.patch(
        f"/rca-investigation/{item_id}",
        params={"expected_revision_id": stale},
        json=[{"op": "add", "path": "/external_refs/-", "value": "legacy-rca:1"}],
    )
    second = client.patch(
        f"/rca-investigation/{item_id}",
        params={"expected_revision_id": stale},  # now one revision behind
        json=[{"op": "add", "path": "/external_refs/-", "value": "legacy-rca:2"}],
    )

    assert first.status_code == 200
    assert second.status_code == 412, second.text
    assert legacy.list_candidates()[0]["external_refs"] == ["legacy-rca:0", "legacy-rca:1"]


def test_recording_the_same_analysis_twice_is_a_no_op():
    """RFC 6902 `add` to `/-` appends unconditionally, so a caller that times
    out on a request which actually landed — the ordinary thing to do — would
    record the ref twice. "At most once per item" has to be enforced by the
    recording step, not hoped for."""
    who = {"user": "alice"}
    legacy = _LegacySite(TestClient(_app_for(who)))
    item_id = legacy.open_new_item_and_record("Oven drift", "legacy-rca:0")

    legacy.record_absorbed(item_id, "legacy-rca:1")
    legacy.record_absorbed(item_id, "legacy-rca:1")  # the retry
    legacy.record_absorbed(item_id, "legacy-rca:1")

    assert legacy.list_candidates()[0]["external_refs"] == ["legacy-rca:0", "legacy-rca:1"]


@pytest.mark.xfail(
    reason=(
        "Upstream: specstar's optimistic lock is check-then-write, not atomic — "
        "resource_manager/core.py reads current_revision_id, compares, and only "
        "then writes, with no transaction spanning the two. Two writers can both "
        "pass the check and the second overwrites the first, so a ref is lost "
        "while both requests answer 200. Retrying on 412 (what record_absorbed "
        "does) narrows the window but cannot close it. Single-pod deploys are "
        "unaffected — the patch route's critical section is synchronous, so one "
        "event loop serialises it — but multi-pod, the documented production "
        "shape, is not. Kept executing rather than deleted so the day the "
        "upstream primitive becomes atomic, this XPASSes and tells us — which "
        "only works because it repeats: see the docstring."
    ),
    strict=False,
)
def test_parallel_handoffs_to_one_item_keep_every_ref():
    """Several people hand analyses to the SAME item at the same moment.

    A lost ref is not cosmetic: the caller decides "already absorbed?" from this
    list, so losing one reads as "not absorbed yet" and the analysis is handed
    over a second time — precisely the sprawl this design exists to prevent.

    Threads, not `asyncio.gather`: the patch route's critical section is
    synchronous, so coroutines on one event loop cannot interleave and a
    gather-based test can never fail however broken the server is. That is
    exactly the false pass this replaces.

    REPEATED, and that is the point of this version. One round loses a ref only
    about half the time (measured: 9 losses in 20 rounds with the shipped retry
    loop), so a single-round xfail XPASSes at random — and a signal that fires by
    coin flip announces nothing. That is the same defect as the `asyncio.gather`
    version it replaced, with the polarity flipped: there a test that could never
    fail, here a test that passes for no reason. Ten rounds make the loss
    reliable (~1 - 0.55^10), so an XPASS becomes evidence rather than noise — and
    the day the upstream primitive turns atomic this flips to a STABLE xpass,
    which is the announcement the xfail is kept alive to make.
    """

    def one_round() -> tuple[list[str], list[str]]:
        legacy = _LegacySite(TestClient(_app_for({"user": "alice"})))
        item_id = legacy.open_new_item_and_record("Oven drift", "legacy-rca:0")
        incoming = [f"legacy-rca:{i}" for i in range(1, 9)]

        with ThreadPoolExecutor(max_workers=len(incoming)) as pool:
            list(pool.map(lambda ref: legacy.record_absorbed(item_id, ref), incoming))

        got = sorted(legacy.list_candidates()[0]["external_refs"])
        return got, sorted(["legacy-rca:0", *incoming])

    rounds = 10
    for round_no in range(rounds):
        got, want = one_round()
        assert got == want, (
            f"round {round_no + 1}/{rounds} lost {sorted(set(want) - set(got))} "
            "— every request answered 200"
        )


def test_paging_past_the_first_page_needs_an_immutable_sort_key():
    """Decision 12 sorts by `updated_time` — a key a concurrent handoff MUTATES.

    So an item can slide across the page boundary between two `offset` fetches
    and be seen by neither. Measured with the mutable key: page 1 gives `d, c`;
    a handoff then touches `a` (on page 2), lifting it to the front; page 2 at
    `offset=2` gives `c, b` — the caller sees `d, c, c, b` and never sees `a`.
    The `resource_id` tiebreaker does not prevent this, it only settles ties
    within one query; an earlier version of the guide claimed otherwise.

    An item the picker never shows is one the user re-creates, which is the
    sprawl this design exists to stop. Since decision 12 keeps the mutable key
    for page one (where it is exactly right), the guide tells callers to
    re-query under `created_time` to page. This pins that the escape hatch is
    genuinely stable — swap the sort back to `updated_time` and it fails.
    """
    who = {"user": "alice"}
    client = TestClient(_app_for(who))
    legacy = _LegacySite(client)
    ids = {name: legacy.open_new_item(name) for name in ("a", "b", "c", "d")}

    def page(offset: int) -> list[str]:
        rows = client.get(
            "/rca-investigation",
            params={"limit": 2, "offset": offset, "sorts": _STABLE_FOR_PAGING},
        ).json()
        return [r["data"]["title"] for r in rows]

    first = page(0)
    # A handoff lands on the OLDEST item, which sits on page two — the exact
    # interleaving that loses a row when the sort key is the mutable one.
    legacy.record_absorbed(ids["a"], "legacy-rca:1")
    second = page(2)

    assert sorted(first + second) == ["a", "b", "c", "d"], (first, second)


def test_uploading_into_a_deleted_item_says_it_is_gone_rather_than_crashing():
    """The picker lists, the user picks, and in between someone deletes the item.

    A plausible race given the list-then-act shape this integration is built on.
    The caller needs to tell "that item is gone, offer to open a new one" apart
    from "the platform is broken, retry later" — and a 500 says the second.
    Reading and patching a deleted item already answer 410; the upload path did
    not, so the outside team's most likely race looked like an outage.
    """
    who = {"user": "alice"}
    client = TestClient(_app_for(who))
    legacy = _LegacySite(client)
    item_id = legacy.open_new_item("Oven drift")
    assert client.delete(f"/rca-investigation/{item_id}").status_code in (200, 204)

    r = client.put(f"/a/rca/items/{item_id}/files/legacy-rca-1/readings.csv", content=b"x")

    assert r.status_code == 410, f"expected 410 Gone, got {r.status_code}"


def test_a_colleague_sees_the_item_so_convergence_survives_across_people():
    """Convergence has to hold between people, not just within one account —
    an item a colleague cannot see is one they will silently duplicate."""
    who = {"user": "alice"}
    app = _app_for(who)
    _LegacySite(TestClient(app)).open_new_item_and_record("Oven drift", "legacy-rca:1")

    who["user"] = "bob"
    seen = _LegacySite(TestClient(app)).list_candidates()

    assert [c["title"] for c in seen] == ["Oven drift"]
