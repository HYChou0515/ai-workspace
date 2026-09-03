"""Item locator (#54) — resolve an item's identity and conversations.

Every workspace route nests under ``/a/{slug}/items/{item_id}`` and needs the same
small vocabulary: validate the slug→item pairing, read an item's owning App slug /
profile / title, resolve its turn's ``AgentConfig``, and find (or create) its chats.
Those resolutions were a cluster of closures inside ``create_app``; gathering them
behind one small interface keeps the slug/profile/title scan and the default-chat /
engine-key / chat-validation rules in a single place the routes, the turn-context
builder, and the workflow executor all cross.

Read-only against ``find_work_item`` (``apps.resolve``) and the multi-chat helpers
(``api.chats``); the only writes are ``conversation_for``'s get-or-create of an
item's default chat.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import NamedTuple

from fastapi import HTTPException
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ..apps.catalog import AppCatalog
from ..apps.manifest import load_app_manifest
from ..apps.resolve import debtor_of, find_work_item, resolve_item_agent_config
from ..perm import Verb
from ..resources import AgentConfig, Conversation
from ..resources.groups import groups_of
from .chats import find_default_conversation, resolve_default_conversation
from .item_authz import (
    ItemAccessFacts,
    check_access,
    load_access_facts,
    refuse_if_gone,
)
from .item_conversation_perm import item_conversation_mirror

# Permission facts change on an administrative timescale, requests arrive on a
# human one. Matches the other windows in this codebase (usage_window, the
# facade's liveness memo) so there is one granularity to reason about.
_ACCESS_WINDOW_S = 5.0


class TurnFacts(NamedTuple):
    """One item's turn-relevant fields, resolved together. See `ItemLocator.turn_facts`."""

    slug: str | None
    profile: str
    skill_prefs: dict[str, bool]
    env_vars: dict[str, str]


class ItemLocator:
    """Resolve an item's identity (slug / profile / title / agent config) and its
    conversations from an opaque ``item_id``. Wraps ``apps.resolve.find_work_item``
    so the "id → which App owns it + the item" scan lives in one place."""

    def __init__(
        self,
        spec: SpecStar,
        app_catalog: AppCatalog,
        *,
        get_user_id: Callable[[], str] = lambda: "",
        superusers: frozenset[str] = frozenset(),
        access_window: float = _ACCESS_WINDOW_S,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._spec = spec
        self._app_catalog = app_catalog
        # item -> (facts, at) and user -> (groups, at). See `require_access`.
        self._access: dict[str, tuple[ItemAccessFacts | None, float]] = {}
        self._groups: dict[str, tuple[frozenset[str], float]] = {}
        self._access_window = access_window
        self._now = now
        self._conv_rm = spec.get_resource_manager(Conversation)
        # #306 PR3: the current-request user + superuser set, so a workspace
        # sub-route can gate itself (`require_access`) against the item's live
        # Permission — the auto-CRUD scope only covers the item resource, not the
        # hand-written file/chat/stream routes that go through this locator.
        self._get_user_id = get_user_id
        self._superusers = superusers

    def turn_facts(self, item_id: str) -> TurnFacts:
        """Everything a turn needs to know about its item, from ONE lookup.

        The accessors below each resolve the item for themselves, which is right
        for a caller that needs one answer and wrong for the turn path, which
        needs four for the same item — `TurnContextBuilder` was making ten
        synchronous specstar round trips per turn to collect them. `apps/resolve`
        puts the cost plainly: the call count IS the latency.

        Every fallback here is copied from the accessor it replaces, including
        `profile_of`'s "default" for an unknown id, so the two can never answer
        differently."""
        found = find_work_item(self._spec, item_id)
        if found is None:
            return TurnFacts(slug=None, profile="default", skill_prefs={}, env_vars={})
        return TurnFacts(
            slug=found[0],
            profile=found[1].profile,
            skill_prefs=dict(found[1].attached_skill_prefs),
            env_vars=dict(found[1].env_vars),
        )

    def title_of(self, item_id: str) -> str | None:
        """Title of any App's WorkItem, resolved generically by id (the mention
        + export paths need it for their copy). ``None`` when the id maps to no
        registered App's item."""
        found = find_work_item(self._spec, item_id)
        return found[1].title if found is not None else None

    def profile_of(self, item_id: str) -> str:
        """The App profile an item was created from — drives the §A skill index
        (the runner exposes `read_skill` when the profile ships skills).
        "default" when the id maps to no registered App's item."""
        found = find_work_item(self._spec, item_id)
        return found[1].profile if found is not None else "default"

    def skill_prefs_of(self, item_id: str) -> dict[str, bool]:
        """The item's per-item tri-state skill override (``attached_skill_prefs``,
        #380) — drives the skills picker's ``effective`` state + the read_skill
        gate. Empty when the id maps to no registered App's item (every skill
        follows its profile/App default)."""
        found = find_work_item(self._spec, item_id)
        return dict(found[1].attached_skill_prefs) if found is not None else {}

    def env_vars_of(self, item_id: str) -> dict[str, str]:
        """The item's user-set environment variables (``WorkItemBase.env_vars``)
        — read fresh per turn and named on the ``exec`` that dispatches each tool
        (#673), which is what makes an edit between turns take effect. Empty when
        the id maps to no registered App's item; empty is also how a user's last
        deleted variable stops reaching the tools, since nothing is stored
        anywhere for a stale copy to survive in."""
        found = find_work_item(self._spec, item_id)
        return dict(found[1].env_vars) if found is not None else {}

    def owner_of(self, item_id: str) -> str | None:
        """Who this item's resources are charged to — `owner`, falling back to
        the creator when it is blank. The rule itself is `apps.resolve.debtor_of`,
        shared with the quota facts memo so the two cannot disagree.

        Reads a DELETED item too, because deleting an item does not stop its
        sandbox: the machine keeps running until the reaper takes it, and
        somebody has to be charged for it in the meantime. Returning `None`
        here read as "nobody owes" at four gates at once."""
        found = find_work_item(self._spec, item_id, include_deleted=True)
        if found is None:
            return None
        return debtor_of(self._spec, found[0], item_id, found[1]) or None

    def slug_of(self, item_id: str) -> str | None:
        """The App slug owning an item — pairs with `profile_of` so the
        runner can read the profile's `.skill/` dir. None for an unknown id."""
        found = find_work_item(self._spec, item_id)
        return found[0] if found is not None else None

    def require_item(self, slug: str, item_id: str) -> str:
        """#95: the workspace routes nest under ``/a/{slug}/items/{item_id}``.
        Validate that ``item_id`` really belongs to App ``slug`` (404 otherwise)
        so a wrong slug can't operate on another App's item, and return the id
        for the handler to use. A SOFT-DELETED item of this App answers 410 Gone
        — the same answer `require_access` gives, through the same function.

        The first version of that 410 was hand-rolled here rather than shared,
        and asked "does this id resolve at all?" instead of "is it deleted?" —
        so a LIVE item addressed under the wrong App came back as Gone. The
        wrong-slug branch is 404 and nothing else: this gate authorizes nobody,
        so it must never tell a stranger that some other App holds this id.

        ONE read when it lets you through. The second version resolved the full
        access facts up front — an extra `get_meta` on every success, on the
        routes that poll (`turn-alive` went 3 gets + 3 metas to 3 + 4) — and
        justified it with "`require_access` already pays the same two reads",
        which is false: `require_access` MEMOISES its facts, so inside the
        window it pays nothing and this gate would re-read what the line above
        it had just cached. Both branches of the answer are unchanged; only the
        cost moved back onto the failure path, where a second round trip is
        free."""
        found = find_work_item(self._spec, item_id)
        if found is not None and found[0] == slug:
            return item_id
        # A miss here is one of three things, and only ONE of them is Gone: a
        # soft-deleted item OF THIS APP. An unknown id and an item of another
        # App are both 404 — this gate authorizes nobody, so the difference
        # between "no such id" and "another App holds it" is not a stranger's
        # to learn.
        facts = load_access_facts(self._spec, item_id, include_deleted=True)
        if facts is not None and facts.slug == slug:
            refuse_if_gone(facts, item_id)
        raise HTTPException(status_code=404, detail=f"item {item_id!r} not found in app {slug!r}")

    def require_access(self, slug: str, item_id: str, verb: Verb) -> str:
        """#306 PR3 — the authorizing sibling of ``require_item``: validate slug↔item,
        then gate the current user for ``verb`` against the item's live Permission
        (``read_meta`` first → 404 no existence leak, then ``verb`` → 403). Returns
        the ``item_id`` so a handler drops it in where it used ``require_item``.

        The two lookups behind the answer — the item row and its meta — are the
        ENTIRE database cost of a read request; the handlers themselves make
        none. They are also CPU-bound Python rather than SQL (a cached, zero-SQL
        `get` measured 28ms in production), so threads cannot parallelise them
        away and the only saving is not doing them. The FACTS are held for
        `access_window`; the DECISION never is, so a verb the caller has not
        asked for before is still evaluated properly, and a permission change
        calls `forget_access` rather than waiting the window out.
        """
        user = self._get_user_id()
        now = self._now()
        cached = self._access.get(item_id)
        if cached is None or now - cached[1] >= self._access_window:
            facts = load_access_facts(self._spec, item_id, include_deleted=True)
            # Cache the POSITIVE answer only. "No such item" is the one result
            # that goes stale in the direction that breaks things: an id looked
            # up moments before it exists — a workflow addressing the item it
            # just created — would keep 404-ing for the rest of the window. A
            # permission is a fact about a thing that exists; absence is not.
            if facts is not None:
                self._access[item_id] = (facts, now)
        else:
            facts = cached[0]
        groups = self._groups_for(user, now)
        check_access(
            facts, slug, item_id, verb, user=user, groups=groups, superusers=self._superusers
        )
        refuse_if_gone(facts, item_id)
        return item_id

    def _groups_for(self, user: str, now: float) -> frozenset[str]:
        """The caller's groups, held for the same window. Group membership is an
        administrative act, not a per-request one."""
        cached = self._groups.get(user)
        if cached is None or now - cached[1] >= self._access_window:
            cached = (groups_of(self._spec, user), now)
            self._groups[user] = cached
        return cached[0]

    def forget_access(self, item_id: str) -> None:
        """Drop the cached facts for an item — called by whatever changes its
        permission, so a revocation lands on the very next request instead of
        being hidden for up to a window. A cache that outlives a revocation is a
        security bug, not a slow one."""
        self._access.pop(item_id, None)

    #: Called when an item's stored facts change under the memo that caches
    #: them. Wired by `create_app`, which owns that cache; absent in the tests
    #: and replay paths that construct a locator without one.
    forget_item_facts: Callable[[str], None] | None = None

    def forget_item(self, item_id: str) -> None:
        """Drop the cached (slug, owner, environment size) for an item.

        Called by whatever WRITES those facts, for the same reason
        `forget_access` exists next door: the memo is five seconds wide, and a
        person who has just saved a size does not experience that as caching —
        they experience it as the setting not working, then working, with
        nothing to explain either. Five seconds is the worst duration for that:
        long enough to look broken, short enough to be gone before anyone can
        look."""
        if self.forget_item_facts is not None:
            self.forget_item_facts(item_id)

    def resolve_agent_config(self, item_id: str) -> AgentConfig | None:
        """#89: a per-App WorkItem (RcaInvestigation, …) resolves its turn's
        config via the 3-layer AppCatalog (app ◇ profile ◇ preset)."""
        return resolve_item_agent_config(self._spec, self._app_catalog, item_id)

    def context_files(self, item_id: str) -> list[str]:
        """The App's declared per-turn context files (manual §6) — the workspace files
        whose live content is injected each turn. Empty for most Apps."""
        slug = self.slug_of(item_id)
        if slug is None:  # pragma: no cover - callers pass a validated item id
            return []
        return load_app_manifest(slug).agent.context_files

    def conversation_for(self, item_id: str) -> tuple[str, Conversation]:
        """The item's DEFAULT chat (manual §3) — the earliest-born free chat,
        created on first use. With multi-chat an item holds many conversations; this
        resolves the implicit default and never returns a workflow chat. Pre-multi-chat
        items have one (unstamped) conversation, which stays the default — byte-for-byte
        preserved."""
        return resolve_default_conversation(
            self._conv_rm, item_id, mirror=item_conversation_mirror(self._spec, item_id)
        )

    def engine_key(self, item_id: str, chat_id: str) -> str:
        """The turn-engine / SSE key for a chat (manual §3). The DEFAULT chat keeps
        the legacy ``item_id`` key so item-level endpoints, the workflow drive path,
        and file-change broadcasts all share its stream; every other chat keys on its
        own id. Read-only — never materialises the default."""
        default = find_default_conversation(self._conv_rm, item_id)
        if default is not None and default[0] == chat_id:
            return item_id
        return chat_id

    def default_chat_id(self, item_id: str) -> str | None:
        """The id of the item's current default chat, or None when it has none.
        Read-only — never materialises one. Used to find the chat whose engine key
        just changed after a delete promoted it."""
        default = find_default_conversation(self._conv_rm, item_id)
        return None if default is None else default[0]

    def require_chat(self, slug: str, item_id: str, chat_id: str) -> tuple[str, Conversation]:
        """Validate slug→item AND that ``chat_id`` is a chat OF that item; return
        ``(chat_id, Conversation)`` or 404. Guards the chat-scoped endpoints (manual §3)."""
        investigation_id = self.require_item(slug, item_id)
        try:
            conv = self._conv_rm.get(chat_id).data
        except ResourceIDNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown chat: {chat_id!r}") from None
        if not isinstance(conv, Conversation) or conv.item_id != investigation_id:
            raise HTTPException(status_code=404, detail=f"unknown chat: {chat_id!r}")
        return chat_id, conv
