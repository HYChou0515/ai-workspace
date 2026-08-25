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

from fastapi import APIRouter, HTTPException
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


class _Override(BaseModel):
    """One person's exception, RAW — a dimension left at 0/"" is one they do not
    have an exception for. Deliberately not merged with the deploy default: the
    list exists to answer "who is above the baseline, and in what", and merging
    would make every row look overridden in every dimension."""

    user_id: str
    count: int = 0
    cpu: float = 0.0
    memory: str = ""
    disk: str = ""


class _Overrides(BaseModel):
    """The exceptions, plus the baseline they are exceptions TO — one payload,
    because a number is meaningless without the default beside it."""

    defaults: _Limits
    overrides: list[_Override] = []


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
    disk_tracked: bool = True
    """Whether the disk figures above mean anything.

    The ledger is only written for people who actually have a disk cap — a
    deploy with none should not pay a durable write per file write for an answer
    nobody asked for. The cost is that `disk_in_use` is then 0 and `workspaces`
    empty, which is NOT the same statement as "you are using nothing", and the
    panel is visible to everyone by design. So say which of the two it is rather
    than let the UI render a number that happens to be false."""


class _SetLimits(BaseModel):
    """An admin's override. Every field optional in the "0/empty means not
    stated" sense — an override grants exactly what it names."""

    count: int = 0
    cpu: float = 0.0
    memory: str = ""
    disk: str = ""


def register_quota_routes(
    app: APIRouter,
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

    async def _found_running(owner: str, already_listed: set[str]) -> list[_LiveEnvironment]:
        """This person's environments that are RUNNING but that no ledger row
        names — and, on the way past, put them back in the ledger.

        The list used to be drawn entirely from the heartbeat, which is belief,
        and belief goes missing: a pod that died between `create` and its first
        bump, a row cleared by a close that killed nothing, a heartbeat that
        aged out of the window while the sandbox kept running. Whatever the
        cause, the environment disappeared from the one page that offers a Close
        button — so there was nothing left to click — while it went on costing
        its owner. Only the backend can settle it, because no record can be
        checked against another record.

        Re-arming the heartbeat is half the point. The per-person limit counts
        the ledger, not this page, so a panel that were merely honest would
        leave the gate blind — the environment would be visible and still not
        charged. It runs, so it costs.

        Scoped to the reader: the backend answers about every sandbox on the
        replica that took the request, and one of them belonging to somebody
        else must not appear on this page, let alone with a Close button. An
        item nobody owns any more is skipped for the same reason.

        A backend that cannot say (`None`) simply adds nothing; this only ever
        finds MORE, so its absence leaves today's behaviour untouched."""
        running = await registry.running_items()
        found = []
        for item_id in running or []:
            if item_id in already_listed or locator.owner_of(item_id) != owner:
                continue
            await registry.record_running(item_id)
            cost = await registry.would_cost(item_id)
            slug, title = _describe(item_id)
            found.append(
                _LiveEnvironment(
                    item_id=item_id,
                    slug=slug,
                    title=title,
                    cpu_cores=cost.cpu_cores or 0.0,
                    memory_bytes=cost.memory_bytes or 0,
                )
            )
        return found

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
        live += await _found_running(owner, {row.item_id for row in live_rows})
        owned = []
        for item_id, used in await disk_ledger.per_item_for(owner):
            slug, title = _describe(item_id)
            owned.append(_OwnedWorkspace(item_id=item_id, slug=slug, title=title, bytes_used=used))
        return _MyResources(
            owner=owner,
            disk_tracked=bool(parse_size(limits.disk)),
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
        # `close_session` owns the whole teardown, INCLUDING clearing the
        # heartbeat. Clearing it here as well was the shape of the bug: the close
        # could quietly do nothing — no session on this replica — and this line
        # still ran, so the panel stopped listing an environment that was still
        # running, and there was no longer anything to click. A refusal to close
        # must leave the row alone, which it can only do if one place owns both.
        # A close that cannot be done RIGHT NOW raises rather than returning:
        # a reachable-but-slow host is `SandboxBusy` → 503 + Retry-After, and
        # every record stays put so there is something left to retry against.
        # Nothing to close is not a failure — a stale page or a double click
        # both arrive that way, and refusing them would teach people the button
        # is broken.
        await registry.close_session(item_id)

    # Registered BEFORE the `/{user_id}` route: a bare GET on the collection
    # would otherwise be matched as a person literally called "".
    @app.get("/admin/user-resources")
    async def admin_list() -> _Overrides:
        """Everyone above the deploy default, and the default itself.

        The by-id read only answers "does THIS person have an exception", so an
        operator could only find one they already knew about — leaving "who is
        above the baseline?" unanswerable, which is the question someone
        inheriting the system actually has."""
        _require_admin(get_user_id(), superusers)
        d = user_limits.default
        return _Overrides(
            defaults=_Limits(
                count=d.count,
                cpu=d.cpu,
                memory_bytes=parse_size(d.memory),
                disk_bytes=parse_size(d.disk),
            ),
            overrides=[
                _Override(user_id=uid, count=o.count, cpu=o.cpu, memory=o.memory, disk=o.disk)
                for uid, o in await user_limits.list_overrides()
            ],
        )

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
