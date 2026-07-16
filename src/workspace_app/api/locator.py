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

from collections.abc import Callable

from fastapi import HTTPException
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ..apps.catalog import AppCatalog
from ..apps.manifest import load_app_manifest
from ..apps.resolve import find_work_item, resolve_item_agent_config
from ..perm import Verb
from ..resources import AgentConfig, Conversation
from .chats import find_default_conversation, resolve_default_conversation
from .item_authz import require_item_access
from .item_conversation_perm import item_conversation_mirror


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
    ) -> None:
        self._spec = spec
        self._app_catalog = app_catalog
        self._conv_rm = spec.get_resource_manager(Conversation)
        # #306 PR3: the current-request user + superuser set, so a workspace
        # sub-route can gate itself (`require_access`) against the item's live
        # Permission — the auto-CRUD scope only covers the item resource, not the
        # hand-written file/chat/stream routes that go through this locator.
        self._get_user_id = get_user_id
        self._superusers = superusers

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

    def slug_of(self, item_id: str) -> str | None:
        """The App slug owning an item — pairs with `profile_of` so the
        runner can read the profile's `.skill/` dir. None for an unknown id."""
        found = find_work_item(self._spec, item_id)
        return found[0] if found is not None else None

    def require_item(self, slug: str, item_id: str) -> str:
        """#95: the workspace routes nest under ``/a/{slug}/items/{item_id}``.
        Validate that ``item_id`` really belongs to App ``slug`` (404 otherwise)
        so a wrong slug can't operate on another App's item, and return the id
        for the handler to use."""
        found = find_work_item(self._spec, item_id)
        if found is None or found[0] != slug:
            raise HTTPException(
                status_code=404, detail=f"item {item_id!r} not found in app {slug!r}"
            )
        return item_id

    def require_access(self, slug: str, item_id: str, verb: Verb) -> str:
        """#306 PR3 — the authorizing sibling of ``require_item``: validate slug↔item,
        then gate the current user for ``verb`` against the item's live Permission
        (``read_meta`` first → 404 no existence leak, then ``verb`` → 403). Returns
        the ``item_id`` so a handler drops it in where it used ``require_item``."""
        require_item_access(
            self._spec,
            slug,
            item_id,
            verb,
            user=self._get_user_id(),
            superusers=self._superusers,
        )
        return item_id

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
