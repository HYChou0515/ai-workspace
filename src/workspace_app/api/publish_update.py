"""A WUI copy can take the new version (`docs/plan-wui-copy-update.md`).

Re-publishing changes only the copies made afterwards (C7): the platform never
touches a copy. What this adds is the copy ASKING whether there is a newer
publish than the one it was made from (P1), and its person TAKING it (P2): a
new copy beside the old one (D1), or — when there is no room — in its place,
the old one deleted by the delete route's own cascade (D5).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel
from specstar import SpecStar

from ..apps.base import WorkItemBase
from ..apps.registry import app_model
from ..apps.resolve import find_work_item
from ..filestore.protocol import FileStore
from ..perm.model import Permission
from ..quota.disk_ledger import DiskLedger
from .item_routes import DeleteItem
from .locator import ItemLocator
from .publish import Publications, Published, may_use
from .publish_copy import DiskLimit, await_finished, copy_size, make_copy

#: What the old copy's title says once a new one replaced it (D1).
OLD_SUFFIX = "（舊版）"


class NoteOut(BaseModel):
    at: int
    by: str
    text: str


class CopyUpdateOut(BaseModel):
    """What the banner needs. ``due`` false ⇒ nothing to say."""

    due: bool
    sharing: str = ""
    published_at: int = 0
    #: The release notes written since this copy was made, oldest first.
    notes: list[NoteOut] = []
    #: D5 — what a new copy writes, and the viewer's room for it: ``free`` is
    #: None without a cap; ``free_if_replaced`` counts this copy's bytes as
    #: freed (刪掉這份並換新版). A shared copy makes nothing: it always fits.
    needs: int = 0
    free: int | None = None
    free_if_replaced: int | None = None
    fits: bool = True


class TakeBody(BaseModel):
    #: D5: delete this copy (permanently) and make the new one in its place.
    replace: bool = False


class TakenOut(BaseModel):
    slug: str
    item_id: str


@dataclass(frozen=True)
class _Behind:
    item: WorkItemBase
    row: Published
    #: The publish the copy was made from (`copy_version`).
    made: int


def copy_version(spec: SpecStar, slug: str, item_id: str, recorded: int) -> int:
    """The publish a copy was made from: its stamp, or — a copy made before the
    stamp was recorded — its creation time."""
    if recorded:
        return recorded
    meta = spec.get_resource_manager(app_model(slug)).get_meta(item_id)
    return int(meta.created_time.timestamp() * 1000)


def register_publish_update_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    locator: ItemLocator,
    publications: Publications,
    get_user_id: Callable[[], str],
    ledger: DiskLedger,
    filestore: FileStore,
    delete_item: DeleteItem,
    limit_of: DiskLimit | None = None,
    superusers: frozenset[str] = frozenset(),
) -> None:
    def _creator(slug: str, item_id: str) -> str:
        # Who CREATED it — the person it was made for (#687: not `owner`, an
        # editable display field).
        return spec.get_resource_manager(app_model(slug)).get_meta(item_id).created_by

    def _behind(slug: str, item_id: str) -> _Behind | None:
        """The ONE rule both routes use: a WUI copy (D2) whose publication was
        published again after it was made, asked by its person — a shared
        copy's: anyone who may still use the publication."""
        found = find_work_item(spec, item_id)
        if found is None or not found[1].published_from:
            return None
        item = found[1]
        row = publications.by_id(item.published_from)
        # D2: a conversation with an agent stays the conversation it was, and
        # 「新對話」 already starts from the latest.
        if row is None or row.kind != "wui":
            return None
        made = copy_version(spec, slug, item_id, item.published_version)
        if row.published_at <= made:
            return None
        user = get_user_id()
        if row.sharing == "shared":
            told = may_use(spec, row, user, superusers)
        else:
            told = user == _creator(slug, item_id)
        return _Behind(item, row, made) if told else None

    @app.get("/a/{slug}/items/{item_id}/published-update", response_model=CopyUpdateOut)
    async def published_update(slug: str, item_id: str) -> CopyUpdateOut:
        """Is there a newer publish than the one this copy was made from?"""
        item_id = locator.require_access(slug, item_id, "read_meta")
        behind = _behind(slug, item_id)
        if behind is None:
            return CopyUpdateOut(due=False)
        row = behind.row
        out = CopyUpdateOut(
            due=True,
            sharing=row.sharing,
            published_at=row.published_at,
            notes=[NoteOut(at=n.at, by=n.by, text=n.text) for n in row.notes if n.at > behind.made],
        )
        if row.sharing != "shared":
            await _room(out, row, item_id)
        return out

    async def _room(out: CopyUpdateOut, row: Published, item_id: str) -> None:
        user = get_user_id()
        out.needs = copy_size(row)
        limit = await limit_of(user) if limit_of is not None else 0
        if not limit:
            return
        held = dict(await ledger.per_item_for(user)).get(item_id, 0)
        out.free = limit - await ledger.total_for(user)
        out.free_if_replaced = out.free + held
        out.fits = out.needs <= out.free

    @app.post("/a/{slug}/items/{item_id}/published-update", response_model=TakenOut)
    async def take_update(slug: str, item_id: str, body: TakeBody) -> TakenOut:
        """Take the new version: a new copy from the latest snapshot (D1).
        The old one stays as an ordinary item named as old — or, with
        ``replace``, is deleted once the new one is made (D5)."""
        item_id = locator.require_access(slug, item_id, "read_meta")
        behind = _behind(slug, item_id)
        if behind is None:
            raise HTTPException(status_code=409, detail="this copy is not behind")
        row, item = behind.row, behind.item
        if row.sharing == "shared":
            # D2: nothing to make — opening the publication lands in the
            # current shared copy.
            raise HTTPException(status_code=400, detail="open the publication instead")
        user = get_user_id()
        rm = spec.get_resource_manager(app_model(slug))
        # Derived from (this copy, this publish), as a person's copy is
        # (`person_copy_id`): two tabs taking it at once make THIS item, the
        # store keeps one, and only the one that made it touches the old copy.
        named = (
            f"{rm.resource_name}:{uuid.uuid5(uuid.NAMESPACE_URL, f'{item_id}|{row.published_at}')}"
        )
        new_id, made = await make_copy(
            spec,
            filestore,
            ledger,
            row,
            owner=user,
            permission=Permission(visibility="private"),
            limit_of=limit_of,
            item_id=named,
            # D5: room is judged with the old copy's bytes left out — they go.
            exclude=item_id if body.replace else "",
        )
        if not made:
            if not await await_finished(spec, row, named):
                raise HTTPException(status_code=409, detail="the new copy could not be made")
            return TakenOut(slug=slug, item_id=named)
        if body.replace:
            await delete_item(rm, item_id, item.title)
        else:
            with rm.using(user):
                rm.update(
                    item_id,
                    msgspec.structs.replace(
                        item, title=f"{item.title}{OLD_SUFFIX}", published_from=""
                    ),
                )
        return TakenOut(slug=slug, item_id=new_id)
