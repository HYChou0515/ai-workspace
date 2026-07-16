"""Permission disclosure — the platform primitive behind "there IS an answer,
but you can't see it, because you lack permission".

`partition_by_disclosure` splits candidate resources into three tiers by what the
actor may do: `readable` (read_content — the ordinary case), `discoverable`
(read_meta but NOT read_content — it exists to them, its NAME may be surfaced, its
bytes are withheld), and `hidden` (not even read_meta — a uniform 404 that is
NEVER disclosed). The middle tier is new; it is what lets retrieval say a withheld
source exists instead of silently dropping it.

These tests exercise the pure primitive through its public interface (Actor +
Permission), the same way `test_authorize` does — no storage, so it generalises to
any protected resource.
"""

from workspace_app.perm import Actor, Permission
from workspace_app.perm.disclosure import DisclosurePartition, partition_by_disclosure

BOB = "bob"  # the resource owner (created_by) throughout


def _one(perm, actor, *, owner=BOB, superusers=frozenset()):
    """Classify a single ("c", perm, owner) entry and return its tier name."""
    part = partition_by_disclosure(actor, [("c", perm, owner)], superusers=superusers)
    if part.readable:
        return "readable"
    if part.discoverable:
        return "discoverable"
    assert part.hidden == ["c"]
    return "hidden"


# ── the three tiers ────────────────────────────────────────────────────────────


def test_public_is_readable():
    assert _one(Permission(visibility="public"), Actor.human("alice")) == "readable"


def test_absent_permission_is_public_hence_readable():
    """No Permission object ≡ public (back-compat), so it is fully readable."""
    assert _one(None, Actor.human("alice")) == "readable"


def test_restricted_with_read_content_is_readable():
    perm = Permission(visibility="restricted", read_content=["user:alice"])
    assert _one(perm, Actor.human("alice")) == "readable"


def test_restricted_with_read_meta_only_is_discoverable():
    """THE new tier: alice may know the resource exists (read_meta) but may not
    read its bytes (read_content) — so it is disclosed by existence, not content."""
    perm = Permission(visibility="restricted", read_meta=["user:alice"])
    assert _one(perm, Actor.human("alice")) == "discoverable"


def test_restricted_ungranted_is_hidden():
    perm = Permission(visibility="restricted", read_content=["user:carol"])
    assert _one(perm, Actor.human("alice")) == "hidden"


def test_private_non_owner_is_hidden_preserving_the_404():
    perm = Permission(visibility="private")
    assert _one(perm, Actor.human("alice")) == "hidden"


# ── owner / superuser bypass land in readable, never merely discoverable ────────


def test_owner_of_a_private_resource_is_readable():
    assert _one(Permission(visibility="private"), Actor.human(BOB)) == "readable"


def test_superuser_is_readable_even_for_private():
    perm = Permission(visibility="private")
    assert _one(perm, Actor.human("root"), superusers=frozenset({"root"})) == "readable"


# ── read_meta grant is the axis, orthogonal to visibility tier ─────────────────


def test_read_meta_via_group_grant_is_discoverable():
    """The read_meta grant may target a group the actor belongs to (#307), and it
    still lands them in the discoverable tier — the disclosure axis is the grant,
    not the visibility label."""
    perm = Permission(visibility="restricted", read_meta=["group:sales"])
    actor = Actor.human("alice", groups=frozenset({"sales"}))
    assert _one(perm, actor) == "discoverable"


def test_read_content_wins_over_read_meta_when_both_granted():
    perm = Permission(
        visibility="restricted",
        read_meta=["user:alice"],
        read_content=["user:alice"],
    )
    assert _one(perm, Actor.human("alice")) == "readable"


# ── an AI actor: a ceiling that admits read_meta but not read_content ──────────


def test_ai_capped_below_read_content_can_still_be_told_existence():
    """An AI whose ceiling grants read_meta but not read_content sees a resource as
    discoverable — it may learn something exists without being able to read it."""
    perm = Permission(visibility="public")
    ai = Actor.ai("alice", ceiling=frozenset({"read_meta"}))
    assert _one(perm, ai) == "discoverable"


# ── the partition: order preserved, every id lands in exactly one bucket ────────


def test_partition_preserves_input_order_and_covers_every_id():
    alice = Actor.human("alice")
    entries = [
        ("pub", Permission(visibility="public"), BOB),
        ("readable", Permission(visibility="restricted", read_content=["user:alice"]), BOB),
        ("disc", Permission(visibility="restricted", read_meta=["user:alice"]), BOB),
        ("hidden", Permission(visibility="private"), BOB),
        ("disc2", Permission(visibility="restricted", read_meta=["user:alice"]), BOB),
    ]
    part = partition_by_disclosure(alice, entries)
    assert part == DisclosurePartition(
        readable=["pub", "readable"],
        discoverable=["disc", "disc2"],
        hidden=["hidden"],
    )


def test_empty_input_yields_empty_partition():
    part = partition_by_disclosure(Actor.human("alice"), [])
    assert part == DisclosurePartition([], [], [])
