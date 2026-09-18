"""A turn's durable claim (plan-graceful-shutdown P3).

The turn answering a question lives in one pod's memory; the question itself
was persisted at acceptance. The claim is the durable bridge between the two:
opened when the message is persisted, carrying the whole send recipe, hard-
deleted when the turn persists its reply. A peer that finds a claim whose
owner is gone re-runs the recipe.
"""

from __future__ import annotations

from typing import Any

from specstar.types import PreconditionFailedError

from workspace_app.api.turn_claims import (
    SpecstarTurnClaimStore,
    TurnClaim,
    register_turn_claims,
)
from workspace_app.resources import make_spec


def _claim(**over: Any) -> TurnClaim:
    base: dict[str, Any] = dict(
        key="item-1",
        created_at=1_700_000_000_000,
        investigation_id="item-1",
        rid="conv-1",
        author="alice",
        lane="foreground",
        body={"content": "hi"},
    )
    base.update(over)
    return TurnClaim(**base)


def _store(spec=None) -> SpecstarTurnClaimStore:
    spec = spec or make_spec(default_user="u")
    register_turn_claims(spec)
    return SpecstarTurnClaimStore(spec, pod_id="pod-a")


def test_open_records_the_recipe_and_this_pod_as_owner():
    store = _store()
    cid = store.open(_claim())
    (row,) = store.list_open()
    assert row.id == cid
    assert row.claim.owner == "pod-a"
    assert row.claim.body == {"content": "hi"}
    assert not row.claim.released


def test_finish_removes_the_row_and_tolerates_a_row_already_gone():
    store = _store()
    cid = store.open(_claim())
    store.finish(cid)
    store.finish(cid)  # a second finish (a retry, a race) is not an error
    assert store.list_open() == []


def test_release_marks_every_open_claim_of_mine_on_the_keys():
    store = _store()
    store.open(_claim(created_at=1))
    store.open(_claim(created_at=2))
    store.open(_claim(key="item-2", created_at=3))
    store.open(_claim(key="item-3", created_at=4))
    store.release(["item-1", "item-3"])  # one round trip for the whole drain
    by_key = {(r.claim.key, r.claim.created_at): r.claim.released for r in store.list_open()}
    assert by_key == {
        ("item-1", 1): True,
        ("item-1", 2): True,
        ("item-2", 3): False,
        ("item-3", 4): True,
    }


def test_release_leaves_another_pods_claim_on_the_key_alone():
    """A draining pod lets go of ITS turns. A claim a peer already took (a
    stale takeover while this pod was wedged) is the peer's to finish: released
    by this pod's drain, the peer's `is_mine` would read False and its finished
    answer would be thrown away and re-run once more (round 1)."""
    spec = make_spec(default_user="u")
    a = _store(spec)
    b = SpecstarTurnClaimStore(spec, pod_id="pod-b")
    a.open(_claim())
    (row,) = b.list_open()
    b.take(row)
    a.release(["item-1"])
    (row,) = a.list_open()
    assert row.claim.owner == "pod-b" and not row.claim.released


def test_release_does_not_overwrite_a_claim_a_peer_took_meanwhile():
    """`release` writes from a listing; a peer's `take` can land between the
    listing and the write. The write is a CAS on the listed etag, so it
    fails instead of putting `owner=<this pod>, released=True` over the
    taker's row — which would have had the taker drop its finished answer
    as "not mine" and a later tick re-run it once more (round 2)."""
    spec = make_spec(default_user="u")
    a = _store(spec)
    b = SpecstarTurnClaimStore(spec, pod_id="pod-b")
    a.open(_claim())
    stale = a.list_open()  # what a's drain read
    (row,) = b.list_open()
    b.take(row)  # the peer got there first
    a.list_open = lambda: stale  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    a.release(["item-1"])
    (row,) = b.list_open()
    assert row.claim.owner == "pod-b" and not row.claim.released


def test_a_release_past_its_deadline_writes_nothing():
    """A drain bounds its handover; a store that answers late must not land
    the release AFTER the cancelled copy persisted its partial as this
    pod's — a peer would then re-run a question that already has an ending
    (round 2). `not_after` is checked before every write."""
    import time

    spec = make_spec(default_user="u")
    store = _store(spec)
    store.open(_claim())
    listed = store.list_open()
    store.list_open = lambda: (time.sleep(0.2), listed)[1]  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    store.release(["item-1"], not_after=time.monotonic() + 0.05)
    (row,) = SpecstarTurnClaimStore(spec, pod_id="pod-a").list_open()
    assert not row.claim.released


def test_two_opens_in_the_same_millisecond_are_two_claims():
    """Two sends on one key inside one millisecond are two questions, each
    owed an answer. A row keyed by (key, created_at) alone merged them: the
    first turn's persist finished the shared row and the second's `is_mine`
    found nothing — its answer was silently not persisted (round 1)."""
    store = _store()
    first = store.open(_claim(body={"content": "first"}))
    second = store.open(_claim(body={"content": "second"}))
    assert first != second
    assert {r.claim.body["content"] for r in store.list_open()} == {"first", "second"}
    store.finish(first)
    assert store.is_mine(second)


def test_take_counts_the_reruns():
    """How many times the turn has been re-run, for the bound the reclaimer
    applies: a turn that cannot finish anywhere is not re-run for ever."""
    spec = make_spec(default_user="u")
    a = _store(spec)
    b = SpecstarTurnClaimStore(spec, pod_id="pod-b")
    a.open(_claim())
    (row,) = b.list_open()
    assert row.claim.reruns == 0
    taken = b.take(row)
    assert taken.claim.reruns == 1
    assert a.take(taken).claim.reruns == 2


def test_take_is_a_cas_the_second_taker_loses():
    """Two pods see the same orphaned claim on the same tick; exactly one runs
    the turn. The loser's `take` raises on the stale etag and it moves on."""
    spec = make_spec(default_user="u")
    a = _store(spec)
    b = SpecstarTurnClaimStore(spec, pod_id="pod-b")
    a.open(_claim())
    (seen_by_a,) = a.list_open()
    (seen_by_b,) = b.list_open()
    taken = b.take(seen_by_b)
    assert taken.claim.owner == "pod-b" and not taken.claim.released
    try:
        a.take(seen_by_a)
    except PreconditionFailedError:
        pass
    else:
        raise AssertionError("the second taker must lose the CAS")
    (row,) = a.list_open()
    assert row.claim.owner == "pod-b"


def test_take_clears_released_so_the_row_is_not_taken_twice():
    store = _store()
    store.open(_claim())
    store.release(["item-1"])
    (row,) = store.list_open()
    assert row.claim.released
    taken = store.take(row)
    assert not taken.claim.released and taken.claim.owner == "pod-a"


def test_is_mine_is_owner_and_not_released():
    """What a turn checks before persisting: a claim another pod took, or one
    this pod let go of, is no longer this pod's to answer."""
    spec = make_spec(default_user="u")
    a = _store(spec)
    b = SpecstarTurnClaimStore(spec, pod_id="pod-b")
    cid = a.open(_claim())
    assert a.is_mine(cid) and not b.is_mine(cid)
    a.release(["item-1"])
    assert not a.is_mine(cid)  # let go: not mine to finish
    (row,) = b.list_open()
    b.take(row)
    assert b.is_mine(cid) and not a.is_mine(cid)
    b.finish(cid)
    assert not b.is_mine(cid)  # gone
