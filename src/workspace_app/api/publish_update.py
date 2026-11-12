"""A WUI copy can take the new version (`docs/plan-wui-copy-update.md`).

Re-publishing changes only the copies made afterwards (C7): the platform never
touches a copy. What this adds is the copy ASKING whether there is a newer
publish than the one it was made from, so its person can choose to take it.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from specstar import SpecStar

from ..apps.registry import app_model
from ..apps.resolve import find_work_item
from ..quota.disk_ledger import DiskLedger
from .locator import ItemLocator
from .publish import Publications, Published, may_use
from .publish_copy import DiskLimit, copy_size


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
    limit_of: DiskLimit | None = None,
    superusers: frozenset[str] = frozenset(),
) -> None:
    def _told(slug: str, item_id: str, row: Published) -> bool:
        """Who the banner is for: a shared copy's people who may still use the
        publication; any other copy's person — the one it was made for, who
        CREATED it (#687: not `owner`, an editable display field)."""
        user = get_user_id()
        if row.sharing == "shared":
            return may_use(spec, row, user, superusers)
        created_by = spec.get_resource_manager(app_model(slug)).get_meta(item_id).created_by
        return user == created_by

    @app.get("/a/{slug}/items/{item_id}/published-update", response_model=CopyUpdateOut)
    async def published_update(slug: str, item_id: str) -> CopyUpdateOut:
        """Is there a newer publish than the one this copy was made from?"""
        item_id = locator.require_access(slug, item_id, "read_meta")
        found = find_work_item(spec, item_id)
        if found is None or not found[1].published_from:
            return CopyUpdateOut(due=False)
        item = found[1]
        row = publications.by_id(item.published_from)
        # D2: a WUI's copies only — a conversation with an agent stays the
        # conversation it was, and 「新對話」 already starts from the latest.
        if row is None or row.kind != "wui":
            return CopyUpdateOut(due=False)
        made = copy_version(spec, slug, item_id, item.published_version)
        if row.published_at <= made or not _told(slug, item_id, row):
            return CopyUpdateOut(due=False)
        out = CopyUpdateOut(
            due=True,
            sharing=row.sharing,
            published_at=row.published_at,
            notes=[NoteOut(at=n.at, by=n.by, text=n.text) for n in row.notes if n.at > made],
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
