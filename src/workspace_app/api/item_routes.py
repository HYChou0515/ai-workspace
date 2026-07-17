"""App work-item lifecycle routes (#54).

Create an App's ``WorkItem`` (seeding its profile's files + collections) and close
one (the generic, manifest-driven lifecycle close that tears the sandbox down and,
when a chat pipeline is wired, promotes the dialogue to the insights KB).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException, Response, status
from pydantic import BaseModel
from specstar import SpecStar

from ..apps.base import WorkItemBase
from ..filestore.protocol import FileStore
from ..kb.ingest import Ingestor
from ..perm import Actor, Permission, Verb, authorize
from ..perm.model import user_subject
from ..resources.groups import groups_of
from .activity import ActivityLog
from .item_authz import require_item_access
from .item_conversation_perm import push_item_mirror_to_conversations
from .locator import ItemLocator
from .notifications import notification_sent, notify
from .permission_body import PermissionBody, PermissionOut, build_permission, granted_user_ids
from .promote import promote_chat_to_kb
from .registry import InvestigationRegistry
from .schemas import _CloseItemBody
from .turns import ChatTurnEngine

# asyncio holds only a WEAK reference to a bare ``create_task()`` result, so an
# un-referenced fire-and-forget task can be garbage-collected mid-flight — the
# background promote then vanishes before it writes the insight, surfacing as a
# flaky "no insight written" under GC pressure on a loaded CI runner. Keep a
# strong reference until each task finishes, discarding it on completion.
_promote_tasks: set[asyncio.Task[list[str]]] = set()

_LOGGER = logging.getLogger(__name__)


class ItemAccessRequestOut(BaseModel):
    """Result of POST /a/{slug}/items/{id}/request-access (permission-disclosure).
    ``requested`` is True iff a fresh owner notification was sent; ``already_readable``
    is True when the caller can already enter the workspace (nothing to request)."""

    item_id: str
    requested: bool
    already_readable: bool = False


class _MembersBody(BaseModel):
    members: list[str]


# grill D7: a member is a Participant — the verbs that let them work in the item.
_PARTICIPANT_VERBS: tuple[Verb, ...] = ("read_meta", "read_chat", "read_content", "converse")


def _reconcile_member_grants(
    current: Permission | None, old_members: list[str], new_members: list[str]
) -> Permission:
    """grill D7 — fold the item's member roster into its ``Permission`` as Participant
    grants (read_meta + read_chat + read_content + converse), so a private-default
    item's members can actually enter it (and the storage-layer list scope, which
    reads the indexed ``permission.read_meta``, admits them). A member ADDED gains the
    participant verbs; a member REMOVED is stripped from them — any grant the owner
    made to a non-member (via the permission dialog) is untouched. Members exist ⇒
    ``restricted`` so the grants are live (``public`` stays public — open anyway)."""
    base = current if current is not None else Permission()
    old_subjects = {user_subject(m) for m in old_members}
    new_subjects = [user_subject(m) for m in new_members]
    removed = old_subjects - set(new_subjects)
    added = [s for s in new_subjects if s not in old_subjects]
    grants: dict[str, list[str]] = {}
    for verb in _PARTICIPANT_VERBS:
        kept = [s for s in base.grants(verb) if s not in removed]
        for s in added:
            if s not in kept:
                kept.append(s)
        grants[verb] = kept
    visibility = "restricted" if new_members and base.visibility != "public" else base.visibility
    return msgspec.structs.replace(base, visibility=visibility, **grants)


def register_item_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    filestore: FileStore,
    get_user_id: Callable[[], str],
    activity: ActivityLog,
    registry: InvestigationRegistry,
    turn_engine: ChatTurnEngine,
    locator: ItemLocator,
    ingestor: Ingestor,
    insights_collection_id: str,
    kb_chat_pipeline: object | None,
    superusers: frozenset[str] = frozenset(),
) -> None:
    """Mount the App work-item create / close routes onto ``app``."""

    def _authorize_item(slug: str, item_id: str, verb: Verb) -> tuple[WorkItemBase, str]:
        """#306 — gate a hand-written WorkItem route: ``read_meta`` first (404, no
        existence leak) then ``verb`` (403). Delegates to the shared
        ``require_item_access`` so item routes, file/chat routes, and stream all gate
        the SAME way (and honour ``group:`` grants — the old inline check ignored
        them). Returns the item + its owner."""
        return require_item_access(
            spec, slug, item_id, verb, user=get_user_id(), superusers=superusers
        )

    @app.post("/a/{slug}/items")
    async def create_app_item(slug: str, body: dict) -> dict:
        """#89 P4b — create an App's WorkItem + seed its profile's files. The
        body carries the item's fields; `owner` comes from auth and `profile`
        defaults to the App's `default_profile`."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.manifest import load_app_manifest
        from ..apps.registry import app_model
        from ..apps.seeding import case_from_item, seed_item

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        manifest = load_app_manifest(slug)
        model = app_model(slug)
        payload = {**body, "owner": get_user_id()}
        payload.setdefault("profile", manifest.default_profile)
        # #306 PR3 (grill D6): NEW items default to PRIVATE (owner-only) — a
        # workspace is the creator's until they share it. The owner is `created_by`,
        # so authorize's owner-bypass keeps the creator's full access. Existing items
        # (no `permission`) stay public — absent ≡ public, no migration. A caller may
        # still pass an explicit `permission` to open it at create time.
        payload.setdefault("permission", {"visibility": "private"})
        try:
            item = msgspec.convert(payload, type=model)
        except msgspec.ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        rm = spec.get_resource_manager(model)
        # Seed the durable files BEFORE the WorkItem row exists, keyed on the id it
        # WILL have (pre-minted in specstar's own `{resource_name}:{uuid}` form).
        # Otherwise the item is discoverable — and its workspace warmable — while
        # its files are still being written one-by-one; a sandbox warm that lands in
        # that window restores a PARTIAL set and, because the facade serves any live
        # sandbox regardless of readiness, serves that partial set for as long as the
        # sandbox stays warm (the "PM item only has one file" data bug). Creating the
        # row LAST closes the window: durable is complete the instant the item
        # appears, so every warm restores the full set.
        item_id = f"{rm.resource_name}:{uuid.uuid4()}"
        # Seeding is BEST-EFFORT: if it raises, the item is still created (it just
        # starts emptier) rather than 500 and strand the user on a frozen modal. The
        # failure is logged (with the id) so an operator can see it.
        seeded: list[str] = []
        try:
            seeded = await seed_item(filestore, item_id, slug, item.profile, case_from_item(item))
            # #280: seed the item's collections.json from the profile's DEFAULT
            # collection set (declared by name + tier), resolving names → live ids. The
            # picker / Monaco then edit it. Unresolvable names are skipped; an
            # empty/undeclared default leaves whatever seed_item wrote alone.
            from ..apps.profiles import load_profile
            from ..kb.collections import resolve_profile_collections

            declared = [(c.name, c.tier) for c in load_profile(slug, item.profile).collections]
            rows = resolve_profile_collections(spec, declared)
            if rows:
                await filestore.write(
                    item_id, "/collections.json", json.dumps(rows, indent=2).encode()
                )
                seeded = sorted({*seeded, "/collections.json"})
        except Exception:  # noqa: BLE001 — best-effort seeding must not sink the create
            _LOGGER.exception(
                "create_app_item: pre-create seeding failed for %s (app=%s profile=%s) — "
                "creating the item anyway; it just starts emptier",
                item_id,
                slug,
                item.profile,
            )
        # Durable is now complete → create the row with the pre-minted id, making the
        # item discoverable (and warmable) ONLY after it is fully seeded.
        rev = rm.create(item, resource_id=item_id)
        try:
            activity.record("item_created", f"Created “{item.title}”", {"item_id": rev.resource_id})
        except Exception:  # noqa: BLE001 — the activity log is not worth failing a create over
            _LOGGER.exception("create_app_item: activity.record failed for %s", rev.resource_id)
        return {
            "resource_id": rev.resource_id,
            "app": slug,
            "profile": item.profile,
            "seeded": seeded,
        }

    @app.put("/a/{slug}/items/{item_id}/permission")
    async def set_item_permission(slug: str, item_id: str, body: PermissionBody) -> PermissionOut:
        """#306 — set an App item's access control (the FE share UI's backend).
        Only the owner / a superuser / a `change_permission` grantee may call it
        (404 if you can't see it, 403 if you can't change it). Mirrors the
        collection setter: persists AS THE OWNER (the per-verb write checker gates
        item updates on write_meta, which a change_permission-only delegate need
        not hold — and change_permission was just verified). Newly-granted users
        get a `share` notification."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.registry import app_model

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        model = app_model(slug)
        item, created_by = _authorize_item(slug, item_id, "change_permission")
        new_perm = build_permission(body)
        rm = spec.get_resource_manager(model)
        with rm.using(created_by):
            rm.update(item_id, msgspec.structs.replace(item, permission=new_perm))
        # #306 PR3: the item's read-visibility is denormalized onto its chats so the
        # Conversation auto-CRUD (which the item scope never covers) inherits the
        # change. Re-push ONLY when the fields the chat scope reads (visibility /
        # read_chat) actually moved. Item row is already persisted (404 immediately);
        # the per-chat loop runs OFF the loop but is AWAITED so shutdown can't strand it.
        old_perm = item.permission if item.permission is not None else Permission()
        if old_perm.visibility != new_perm.visibility or list(old_perm.read_chat) != list(
            new_perm.read_chat
        ):
            await asyncio.to_thread(
                push_item_mirror_to_conversations,
                spec,
                item_id,
                visibility=new_perm.visibility,
                read_chat=new_perm.read_chat,
                created_by=created_by,
            )
        me = get_user_id()
        notified = sorted(granted_user_ids(new_perm) - granted_user_ids(item.permission) - {me})
        for uid in notified:
            notify(
                spec,
                recipient=uid,
                kind="share",
                title=f'Shared an item: "{item.title}"',
                link=f"/a/{slug}/{item_id}",
                actor=me,
            )
        return PermissionOut(resource_id=item_id, visibility=new_perm.visibility, notified=notified)

    @app.put("/a/{slug}/items/{item_id}/members")
    async def set_item_members(slug: str, item_id: str, body: _MembersBody) -> PermissionOut:
        """grill D7 — set an item's member roster AND sync it into the Permission as
        Participant grants (so a private-default item's members can enter it). Gated
        on ``change_permission`` (editing members now grants ACCESS, so it's owner /
        superuser / delegate only — no longer a plain ``write_meta`` field edit).
        Fans out the conversation read-chat mirror and notifies newly-added members."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.registry import app_model

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        model = app_model(slug)
        item, created_by = _authorize_item(slug, item_id, "change_permission")
        if not isinstance(item.members, list):
            raise HTTPException(status_code=422, detail=f"app {slug!r} has no members concept")
        old_members = item.members
        new_perm = _reconcile_member_grants(item.permission, old_members, body.members)
        rm = spec.get_resource_manager(model)
        with rm.using(created_by):
            rm.update(
                item_id, msgspec.structs.replace(item, members=body.members, permission=new_perm)
            )
        await asyncio.to_thread(
            push_item_mirror_to_conversations,
            spec,
            item_id,
            visibility=new_perm.visibility,
            read_chat=new_perm.read_chat,
            created_by=created_by,
        )
        me = get_user_id()
        added = sorted(set(body.members) - set(old_members) - {me})
        for uid in added:
            notify(
                spec,
                recipient=uid,
                kind="share",
                title=f'Added you to "{item.title}"',
                link=f"/a/{slug}/{item_id}",
                actor=me,
            )
        return PermissionOut(resource_id=item_id, visibility=new_perm.visibility, notified=added)

    @app.post("/a/{slug}/items/{item_id}/request-access")
    async def request_item_access(slug: str, item_id: str) -> ItemAccessRequestOut:
        """Permission-disclosure (grill D4): the caller (who can SEE the item via
        read_meta — the 🔒 locked list row) asks its owner to grant access. A
        ``read_meta`` gate first (404 for someone who can't discover it, no leak).
        Sends ONE deduped ``access_request`` notification to the owner; a caller who
        can already enter the workspace (``read_chat``) has nothing to request.
        Reuses the notify/bell + the owner's permission dialog — no durable request
        state. Mirrors the collection request-access endpoint."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.registry import app_model

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        rm = spec.get_resource_manager(app_model(slug))
        try:
            item = rm.get(item_id).data
        except Exception as exc:  # noqa: BLE001 — a missing id is a 404, not a 500
            raise HTTPException(status_code=404, detail="item not found") from exc
        assert isinstance(item, WorkItemBase)
        owner = rm.get_meta(item_id).created_by
        me = get_user_id()
        actor = Actor.human(me, groups=groups_of(spec, me))
        perm = item.permission
        # Already in the workspace (owner / superuser / can read_chat) → nothing to
        # request; precedes the read_meta 404 gate (read_chat need not imply an
        # explicit read_meta grant).
        if me == owner or authorize(
            actor, "read_chat", perm, created_by=owner, superusers=superusers
        ):
            return ItemAccessRequestOut(item_id=item_id, requested=False, already_readable=True)
        if not authorize(actor, "read_meta", perm, created_by=owner, superusers=superusers):
            raise HTTPException(status_code=404, detail="item not found")
        dedup_key = f"access_request:item:{item_id}:{me}"
        if notification_sent(spec, dedup_key):
            return ItemAccessRequestOut(item_id=item_id, requested=False)
        notify(
            spec,
            recipient=owner,
            kind="access_request",
            title=f'{me} requests access to "{item.title}"',
            body=(
                f'{me} asked to enter the workspace "{item.title}". Open its sharing '
                "settings to grant access."
            ),
            link=f"/a/{slug}/{item_id}",
            actor=me,
            dedup_key=dedup_key,
        )
        return ItemAccessRequestOut(item_id=item_id, requested=True)

    @app.post(
        "/a/{slug}/items/{item_id}/close",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def close_app_item(slug: str, item_id: str, body: _CloseItemBody) -> Response:
        """#89 P8 — generic, lifecycle-driven close for any App's WorkItem.
        A non-null `status` must be one of the manifest's
        `lifecycle.closing_states` and is set onto `lifecycle.status_field`;
        null leaves the item's status untouched. Either way the workspace
        session is torn down."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.manifest import load_app_manifest
        from ..apps.registry import app_model

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        manifest = load_app_manifest(slug)
        model = app_model(slug)
        rm = spec.get_resource_manager(model)
        # #306: closing is a lifecycle write — gate on write_meta (404 hides an
        # item the caller can't see, 403 blocks an in-scope member). Explicit here
        # because the pure-close path does no rm.update, so the write checker never
        # fires on it.
        current, _ = _authorize_item(slug, item_id, "write_meta")
        title = current.title
        if body.status is not None:
            lifecycle = manifest.lifecycle
            if lifecycle is None:  # pragma: no cover - every closable App declares lifecycle
                raise HTTPException(status_code=422, detail=f"app {slug!r} has no close lifecycle")
            if body.status not in lifecycle.closing_states:
                raise HTTPException(
                    status_code=422,
                    detail=f"{body.status!r} is not a closing state for app {slug!r}",
                )
            data = msgspec.to_builtins(current)
            data[lifecycle.status_field] = body.status
            rm.update(item_id, msgspec.convert(data, type=model))
            activity.record(
                "item_closed",
                f"Closed “{title}” as {body.status}",
                {"item_id": item_id},
            )
            # chat → knowledge: schedule insight extraction in the background so
            # the close response doesn't wait on the LLM. Only when a chat
            # pipeline is wired (LLM available).
            if kb_chat_pipeline is not None:
                _, conv_for_promote = locator.conversation_for(item_id)
                task = asyncio.create_task(
                    promote_chat_to_kb(
                        ingestor=ingestor,
                        insights_collection_id=insights_collection_id,
                        actor=get_user_id(),
                        investigation_id=item_id,
                        investigation_title=title,
                        messages=conv_for_promote.messages,
                    )
                )
                _promote_tasks.add(task)
                task.add_done_callback(_promote_tasks.discard)
            # Notify the owner + watchers (members are Tier-2 / opt-in), except
            # whoever did it.
            actor = get_user_id()
            members = current.members
            if isinstance(members, msgspec.UnsetType):  # pragma: no cover - RCA enables members
                members = []
            for uid in {current.owner, *members} - {actor}:
                notify(
                    spec,
                    recipient=uid,
                    kind="status",
                    title=f"{title} → {body.status}",
                    link=f"/a/{slug}/{item_id}",
                    actor=actor,
                )
        else:
            # Pure close — leave status untouched, just release the workspace.
            activity.record(
                "session_closed",
                f"Closed the workspace for “{title}”",
                {"item_id": item_id},
            )
        await registry.close_session(item_id)
        await turn_engine.forget(item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
