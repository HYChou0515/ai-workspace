"""#366: the per-item sandbox address (handle) shared across pods.

The address lives in specstar (not per-pod memory) so two API pods serving the
same item converge on ONE handle instead of each minting their own sandbox.
"""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.api.sandbox_address import (
    SpecstarAddressStore,
    register_sandbox_address,
)
from workspace_app.sandbox.protocol import SandboxHandle


def _store() -> SpecstarAddressStore:
    spec = SpecStar()
    register_sandbox_address(spec)
    register_sandbox_address(spec)  # idempotent — safe on every pod
    return SpecstarAddressStore(spec)


async def test_first_claim_wins_and_others_converge():
    store = _store()
    assert await store.get("item-1") is None  # unclaimed → None

    h1 = SandboxHandle(id="addr-1")
    h2 = SandboxHandle(id="addr-2")
    assert await store.claim("item-1", h1) == h1  # first writer wins
    # A second pod claiming with its own fresh handle converges on the winner
    # (so it does NOT keep a diverging second sandbox).
    assert await store.claim("item-1", h2) == h1
    assert await store.get("item-1") == h1


async def test_distinct_items_are_independent():
    store = _store()
    a, b = SandboxHandle(id="A"), SandboxHandle(id="B")
    assert await store.claim("a", a) == a
    assert await store.claim("b", b) == b
    assert await store.get("a") == a
    assert await store.get("b") == b


async def test_forget_releases_the_slot_for_reclaim():
    # When the sandbox behind an address is torn down, forget() releases the
    # slot so the item's NEXT (freshly-created) sandbox can claim it.
    store = _store()
    h1, h2 = SandboxHandle(id="h1"), SandboxHandle(id="h2")
    assert await store.claim("item-1", h1) == h1

    await store.forget("item-1")
    assert await store.get("item-1") is None

    # a fresh sandbox's address takes the released slot (no longer converges on h1)
    assert await store.claim("item-1", h2) == h2
    assert await store.get("item-1") == h2

    await store.forget("item-1")
    await store.forget("item-1")  # idempotent — no error when already released


async def test_swap_replaces_dead_address_and_loser_converges():
    # #366 P2: when the sandbox behind an address dies, one pod CAS-swaps a fresh
    # address in (expected=the dead one). A peer that already swapped makes our
    # swap a no-op that converges on the peer's new address.
    store = _store()
    dead = SandboxHandle(id="dead")
    fresh = SandboxHandle(id="fresh")
    other = SandboxHandle(id="other")
    assert await store.claim("item-1", dead) == dead

    # expected matches → we win the swap
    assert await store.swap("item-1", expected=dead, new=fresh) == fresh
    assert await store.get("item-1") == fresh

    # expected no longer matches (a peer already refreshed) → converge on current
    assert await store.swap("item-1", expected=dead, new=other) == fresh
    assert await store.get("item-1") == fresh


async def test_swap_on_a_released_slot_claims_fresh():
    # If the slot was forgotten (item closed) between a pod finding a dead address
    # and its swap, the swap degrades to a fresh claim rather than erroring.
    store = _store()
    dead, fresh = SandboxHandle(id="dead"), SandboxHandle(id="fresh")
    assert await store.claim("item-1", dead) == dead
    await store.forget("item-1")  # slot released mid-flight
    assert await store.swap("item-1", expected=dead, new=fresh) == fresh
    assert await store.get("item-1") == fresh
