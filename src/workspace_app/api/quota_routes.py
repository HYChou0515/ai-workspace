"""Routes for the resource limits: what I am using, and (for an admin) what any
one person is allowed.

`GET /me/resources` is the read model behind the "my resource usage" panel. It
answers with the two things a refused person needs — what they are holding and
what their ceiling is — plus the item behind each live environment, because a
list of things to close is useless without knowing what they are.

The panel is not decoration. The limits refuse outright rather than evicting
anything, so without somewhere to see and release what you hold, being at your
limit is a dead end.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from specstar import SpecStar

from ..config.schema import PerUserResources
from ..files import WorkspaceFiles
from ..quota.disk_ledger import DiskLedger
from ..quota.limits import parse_size
from ..quota.user_limits import UserLimits
from .locator import ItemLocator
from .registry import InvestigationRegistry
from .sandbox_activity import IActivityStore


class _LiveEnvironment(BaseModel):
    """One live sandbox the person is holding."""

    item_id: str
    slug: str = ""
    title: str = ""
    cpu_cores: float = 0.0
    memory_bytes: int = 0


class _OwnedWorkspace(BaseModel):
    """One item whose stored bytes are charged to this person."""

    item_id: str
    slug: str = ""
    title: str = ""
    bytes_used: int = 0


class _Limits(BaseModel):
    count: int = 0
    cpu: float = 0.0
    memory_bytes: int = 0
    disk_bytes: int = 0


class _MyResources(BaseModel):
    """Everything the panel renders. Usage and limits together, so the FE never
    has to pair up two calls that could disagree."""

    owner: str
    limits: _Limits
    live: list[_LiveEnvironment] = []
    workspaces: list[_OwnedWorkspace] = []
    cpu_in_use: float = 0.0
    memory_in_use: int = 0
    disk_in_use: int = 0


class _SetLimits(BaseModel):
    """An admin's override. Every field optional in the "0/empty means not
    stated" sense — an override grants exactly what it names."""

    count: int = 0
    cpu: float = 0.0
    memory: str = ""
    disk: str = ""


def register_quota_routes(
    app: FastAPI,
    *,
    spec: SpecStar,
    locator: ItemLocator,
    registry: InvestigationRegistry,
    files: WorkspaceFiles,
    activity: IActivityStore | None,
    disk_ledger: DiskLedger,
    user_limits: UserLimits,
    get_user_id: Callable[[], str],
    idle_window_ms: int,
    now_ms: Callable[[], int],
    superusers: frozenset[str] = frozenset(),
) -> None:
    """Mount the resource-usage + per-user-limit routes."""

    def _describe(item_id: str) -> tuple[str, str]:
        return locator.slug_of(item_id) or "", locator.title_of(item_id) or ""

    async def _resources_of(owner: str) -> _MyResources:
        limits = await user_limits.for_user(owner)
        live_rows = (
            await activity.live_for(owner, since_ms=now_ms() - idle_window_ms)
            if activity is not None
            else []
        )
        live = []
        for row in live_rows:
            slug, title = _describe(row.item_id)
            live.append(
                _LiveEnvironment(
                    item_id=row.item_id,
                    slug=slug,
                    title=title,
                    cpu_cores=row.cpu_milli / 1000,
                    memory_bytes=row.memory_bytes,
                )
            )
        owned = []
        for item_id, used in await disk_ledger.per_item_for(owner):
            slug, title = _describe(item_id)
            owned.append(_OwnedWorkspace(item_id=item_id, slug=slug, title=title, bytes_used=used))
        return _MyResources(
            owner=owner,
            limits=_Limits(
                count=limits.count,
                cpu=limits.cpu,
                memory_bytes=parse_size(limits.memory),
                disk_bytes=parse_size(limits.disk),
            ),
            live=live,
            workspaces=sorted(owned, key=lambda w: -w.bytes_used),
            cpu_in_use=sum(e.cpu_cores for e in live),
            memory_in_use=sum(e.memory_bytes for e in live),
            disk_in_use=sum(w.bytes_used for w in owned),
        )

    @app.get("/me/resources")
    async def my_resources() -> _MyResources:
        """What I am holding, and what I may hold."""
        return await _resources_of(get_user_id())

    @app.delete("/me/resources/live/{item_id}", status_code=204)
    async def close_environment(item_id: str) -> None:
        """Release one live environment I am holding.

        This really shuts the sandbox down; it does not merely stop counting it.
        The machine resource is one thing, and a panel that freed the tally
        without freeing the machine would be lying about what it did — and would
        make the next person's limit meaningless."""
        owner = locator.owner_of(item_id)
        if owner != get_user_id() and get_user_id() not in superusers:
            raise HTTPException(status_code=404, detail="unknown environment")
        await registry.close_session(item_id)
        if activity is not None:
            await activity.forget(item_id)

    @app.get("/admin/user-resources/{user_id}")
    async def admin_get(user_id: str) -> _MyResources:
        _require_admin(get_user_id(), superusers)
        return await _resources_of(user_id)

    @app.put("/admin/user-resources/{user_id}", status_code=204)
    async def admin_set(user_id: str, body: _SetLimits) -> None:
        """Grant one person an exception. Takes effect on their next check —
        the gate resolves limits per check, not at boot."""
        _require_admin(get_user_id(), superusers)
        await user_limits.set_for(
            user_id,
            PerUserResources(count=body.count, cpu=body.cpu, memory=body.memory, disk=body.disk),
        )

    @app.delete("/admin/user-resources/{user_id}", status_code=204)
    async def admin_clear(user_id: str) -> None:
        """Drop the override so this person falls back to the deploy default."""
        _require_admin(get_user_id(), superusers)
        await user_limits.clear_for(user_id)


def _require_admin(me: str, superusers: frozenset[str]) -> None:
    if me not in superusers:
        # 404, not 403: whether a given person has an override is not something
        # a non-admin should be able to probe for.
        raise HTTPException(status_code=404, detail="not found")
