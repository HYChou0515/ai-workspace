"""#262 — the central authorize() decision over a shared Permission.

Tests describe behaviour through the public interface (Actor + authorize +
Permission); none reach into how the lists are stored.
"""

from workspace_app.perm import Actor, Permission, authorize

BOB = "bob"  # the resource creator (owner) in these tests


def test_public_resource_with_no_permission_object_allows_any_human_to_read():
    """No Permission object ≡ `public` — back-compat for rows written before
    #262, and the create-time default for collections/items."""
    assert authorize(Actor.human("alice"), "read_content", None, created_by=BOB) is True


# ── restricted: the per-verb grant lists go live ───────────────────────────────


def test_restricted_allows_a_listed_user_and_denies_an_unlisted_one():
    perm = Permission(visibility="restricted", read_content=["user:alice"])
    assert authorize(Actor.human("alice"), "read_content", perm, created_by=BOB) is True
    assert authorize(Actor.human("carol"), "read_content", perm, created_by=BOB) is False


def test_restricted_with_the_all_wildcard_allows_everyone():
    perm = Permission(visibility="restricted", read_content=["all"])
    assert authorize(Actor.human("anyone"), "read_content", perm, created_by=BOB) is True


def test_restricted_grant_can_target_a_group_the_actor_belongs_to():
    perm = Permission(visibility="restricted", read_content=["group:reflow"])
    actor = Actor.human("alice", groups=frozenset({"reflow"}))
    assert authorize(actor, "read_content", perm, created_by=BOB) is True
    assert authorize(Actor.human("alice"), "read_content", perm, created_by=BOB) is False


def test_edit_content_grant_implies_add_content():
    """edit_content ⊇ add_content: whoever may overwrite/delete may also add."""
    perm = Permission(visibility="restricted", edit_content=["user:alice"])
    assert authorize(Actor.human("alice"), "add_content", perm, created_by=BOB) is True


# ── private / public extremes ─────────────────────────────────────────────────


def test_private_hides_content_from_everyone_but_the_owner():
    perm = Permission(visibility="private")
    assert authorize(Actor.human("alice"), "read_content", perm, created_by=BOB) is False
    assert authorize(Actor.human(BOB), "read_content", perm, created_by=BOB) is True


def test_owner_can_do_anything_to_their_own_resource_even_when_private():
    perm = Permission(visibility="private")
    for verb in ("edit_content", "change_permission", "use_terminal"):
        assert authorize(Actor.human(BOB), verb, perm, created_by=BOB) is True


# ── superuser ─────────────────────────────────────────────────────────────────


def test_superuser_bypasses_everything_including_private_and_change_permission():
    perm = Permission(visibility="private")
    su = frozenset({"root"})
    assert (
        authorize(Actor.human("root"), "read_content", perm, created_by=BOB, superusers=su) is True
    )
    assert (
        authorize(Actor.human("root"), "change_permission", perm, created_by=BOB, superusers=su)
        is True
    )


# ── change_permission is never made public by visibility ──────────────────────


def test_change_permission_is_not_open_on_a_public_resource():
    """A public resource is open for DATA, but rewiring access is still only the
    owner / superuser / explicitly-granted."""
    perm = Permission(visibility="public")
    assert authorize(Actor.human("alice"), "change_permission", perm, created_by=BOB) is False


def test_change_permission_can_be_delegated_to_a_non_owner():
    perm = Permission(visibility="restricted", change_permission=["user:alice"])
    assert authorize(Actor.human("alice"), "change_permission", perm, created_by=BOB) is True


# ── the AI: ceiling ∩ speaker, with hard bars ─────────────────────────────────


def test_ai_can_never_change_permission_or_use_terminal_even_for_its_owner():
    """Hard bar in authorize(), not a prompt rule: a prompt-injection driving the
    owner's agent still cannot rewire access or open a shell."""
    perm = Permission(visibility="public")
    owner_ai = Actor.ai(BOB, ceiling=None)  # ceiling None = all verbs allowed…
    assert authorize(owner_ai, "change_permission", perm, created_by=BOB) is False  # …except these
    assert authorize(owner_ai, "use_terminal", perm, created_by=BOB) is False


def test_ai_is_capped_by_its_preset_ceiling():
    perm = Permission(visibility="public")
    ai = Actor.ai("alice", ceiling=frozenset({"read_content"}))
    assert authorize(ai, "read_content", perm, created_by=BOB) is True
    assert authorize(ai, "edit_content", perm, created_by=BOB) is False  # not in ceiling


def test_ai_is_also_bounded_by_the_speakers_own_grants():
    """ceiling ∩ speaker — even with a permissive ceiling, the AI can't exceed
    what the human driving it could do."""
    perm = Permission(visibility="restricted", read_content=["user:carol"])
    ai = Actor.ai("alice", ceiling=None)  # alice is the speaker; carol is granted, not alice
    assert authorize(ai, "read_content", perm, created_by=BOB) is False


def test_an_ai_driven_by_a_superuser_does_not_inherit_the_bypass():
    """The superuser may do anything by hand, but the agent they drive stays
    ceiling-bounded — so injection can't borrow superuser power."""
    perm = Permission(visibility="public")
    ai = Actor.ai("root", ceiling=frozenset({"read_content"}))
    su = frozenset({"root"})
    assert authorize(ai, "edit_content", perm, created_by=BOB, superusers=su) is False
