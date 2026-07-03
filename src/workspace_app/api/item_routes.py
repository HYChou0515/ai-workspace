"""App work-item lifecycle routes (#54).

Create an App's ``WorkItem`` (seeding its profile's files + collections) and close
one (the generic, manifest-driven lifecycle close that tears the sandbox down and,
when a chat pipeline is wired, promotes the dialogue to the insights KB).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException, Response, status
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ..apps.base import WorkItemBase
from ..filestore.protocol import FileStore
from ..kb.ingest import Ingestor
from ..perm import Actor, authorize
from ..perm.model import Verb
from .activity import ActivityLog
from .locator import ItemLocator
from .notifications import notify
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

    def _authorize_item(model: type, item_id: str, verb: Verb) -> tuple[WorkItemBase, str]:
        """#306 — gate a hand-written WorkItem route. Loads the item (`rm.get` is
        NOT access-scoped), then sequences the two checks the auto-CRUD layer
        composes: `read_meta` first — an actor who can't see the item gets a
        uniform 404 (no existence leak) — then `verb` → 403. Returns the item + its
        owner. `permission is None` ≡ public (back-compat)."""
        rm = spec.get_resource_manager(model)
        try:
            item = rm.get(item_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        assert isinstance(item, WorkItemBase)
        created_by = rm.get_meta(item_id).created_by
        perm = item.permission
        actor = Actor.human(get_user_id())
        if not authorize(actor, "read_meta", perm, created_by=created_by, superusers=superusers):
            raise HTTPException(status_code=404, detail="item not found")
        if not authorize(actor, verb, perm, created_by=created_by, superusers=superusers):
            raise HTTPException(status_code=403, detail=f"not authorized to {verb}")
        return item, created_by

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
        try:
            item = msgspec.convert(payload, type=model)
        except msgspec.ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        rev = spec.get_resource_manager(model).create(item)
        seeded = await seed_item(
            filestore, rev.resource_id, slug, item.profile, case_from_item(item)
        )
        # #280: seed the item's collections.json from the profile's DEFAULT collection
        # set (declared by name + tier), resolving names → live ids. The picker / Monaco
        # then edit it. Unresolvable names are skipped; an empty/undeclared default
        # leaves whatever seed_item wrote (e.g. topic-hub's collections.json.tpl) alone.
        from ..apps.profiles import load_profile
        from ..kb.collections import resolve_profile_collections

        declared = [(c.name, c.tier) for c in load_profile(slug, item.profile).collections]
        rows = resolve_profile_collections(spec, declared)
        if rows:
            await filestore.write(
                rev.resource_id, "/collections.json", json.dumps(rows, indent=2).encode()
            )
            seeded = sorted({*seeded, "/collections.json"})
        activity.record(
            "item_created",
            f"Created “{item.title}”",
            {"item_id": rev.resource_id},
        )
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
        item, created_by = _authorize_item(model, item_id, "change_permission")
        new_perm = build_permission(body)
        rm = spec.get_resource_manager(model)
        with rm.using(created_by):
            rm.update(item_id, msgspec.structs.replace(item, permission=new_perm))
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
        current, _ = _authorize_item(model, item_id, "write_meta")
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
