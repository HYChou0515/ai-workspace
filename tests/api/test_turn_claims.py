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


def test_release_marks_every_open_claim_on_a_key():
    store = _store()
    store.open(_claim(created_at=1))
    store.open(_claim(created_at=2))
    store.open(_claim(key="item-2", created_at=3))
    store.release("item-1")
    by_key = {(r.claim.key, r.claim.created_at): r.claim.released for r in store.list_open()}
    assert by_key == {("item-1", 1): True, ("item-1", 2): True, ("item-2", 3): False}


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
    store.release("item-1")
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
    a.release("item-1")
    assert not a.is_mine(cid)  # let go: not mine to finish
    (row,) = b.list_open()
    b.take(row)
    assert b.is_mine(cid) and not a.is_mine(cid)
    b.finish(cid)
    assert not b.is_mine(cid)  # gone
