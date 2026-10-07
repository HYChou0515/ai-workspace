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


# ── plan-tool-running-version P1: what the live sandbox MOUNTED rides on its address ──

from workspace_app.tooling.external import MountedTool  # noqa: E402

V1 = {"t": MountedTool(sha="s1", version="1.0")}
V2 = {"t": MountedTool(sha="s2", version="2.0")}


async def test_claim_records_what_the_sandbox_mounted():
    store = _store()
    await store.claim("item-1", SandboxHandle(id="h1"), tools=V1)
    assert await store.mounted("item-1") == V1


async def test_a_losing_claim_reads_the_winners_mounts_not_its_own():
    # The row is ONE write: a pod that lost the claim must not see its own
    # bundles reported for the winner's sandbox.
    store = _store()
    await store.claim("item-1", SandboxHandle(id="h1"), tools=V1)
    assert await store.claim("item-1", SandboxHandle(id="h2"), tools=V2) == SandboxHandle(id="h1")
    assert await store.mounted("item-1") == V1


async def test_swap_replaces_the_mounts_with_the_new_sandboxs():
    store = _store()
    h1, h2 = SandboxHandle(id="h1"), SandboxHandle(id="h2")
    await store.claim("item-1", h1, tools=V1)
    assert await store.swap("item-1", expected=h1, new=h2, tools=V2) == h2
    assert await store.mounted("item-1") == V2


async def test_a_lost_swap_leaves_the_peers_mounts():
    store = _store()
    h1, h2, h3 = SandboxHandle(id="h1"), SandboxHandle(id="h2"), SandboxHandle(id="h3")
    await store.claim("item-1", h1, tools=V1)
    await store.swap("item-1", expected=h1, new=h2, tools=V2)
    # A pod that also found h1 dead lost the race: it converges and changes nothing.
    assert await store.swap("item-1", expected=h1, new=h3, tools=V1) == h2
    assert await store.mounted("item-1") == V2


async def test_reclaiming_a_released_slot_records_the_new_mounts():
    store = _store()
    await store.claim("item-1", SandboxHandle(id="h1"), tools=V1)
    await store.forget("item-1")
    await store.claim("item-1", SandboxHandle(id="h2"), tools=V2)
    assert await store.mounted("item-1") == V2


async def test_mounts_are_unknown_when_not_recorded():
    # `None` is "unknown", distinct from `{}` ("mounted nothing"): an address
    # written by an older build, or a create whose resolve failed.
    store = _store()
    assert await store.mounted("nobody") is None
    await store.claim("item-1", SandboxHandle(id="h1"))
    assert await store.mounted("item-1") is None
    await store.claim("item-2", SandboxHandle(id="h2"), tools={})
    assert await store.mounted("item-2") == {}


async def test_mounts_of_a_forgotten_address_are_unknown():
    store = _store()
    await store.claim("item-1", SandboxHandle(id="h1"), tools=V1)
    await store.forget("item-1")
    assert await store.mounted("item-1") is None
