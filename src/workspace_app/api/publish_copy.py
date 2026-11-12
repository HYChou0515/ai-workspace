"""Making a copy of a published WUI or agent (`docs/plan-wui-publish-and-agent-hub.md`, P2).

A copy is an ORDINARY workspace (C6): a new item in the source's App, seeded
from the publication's snapshot, carrying the source's settings — preset, tool
and skill prefs, env policy, profile (C3) — and the Shared values the source
holds at the moment the copy is made (C4, not frozen at publish: a refreshed
value is the one given). It is the opener's: private, on their quota, created
as them (C2). Permission and members are NOT copied (C3); the publisher's
sandbox size is (C10), clamped on the way out like any item's own setting.

How many copies there are is the way of sharing: a new one on every open
(``fresh``, and an agent's ``per_conversation``), one per person
(``per_person``), one made at publish in the publisher's name and shared by
everyone (``shared``, W3), or none at all (``direct`` — the source itself).

Files are written straight into the durable store before the row exists — the
window `item_routes.create_app_item` seeds in, where nothing can be warm for an
id no one has seen yet.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
from specstar import QB, SpecStar
from specstar.types import DuplicateResourceError, ResourceIsDeletedError

from ..apps.agent_spec import agent_folder, overlay_destination
from ..apps.base import WorkItemBase, fields_a_copy_does_not_carry
from ..apps.registry import app_model
from ..apps.resolve import find_work_item
from ..apps.skills import WORKSPACE_SKILL_DIR
from ..filestore.protocol import FileNotFound, FileStore
from ..perm.model import ALL, Permission
from ..quota.disk_ledger import DiskLedger, UserDiskFull

if TYPE_CHECKING:
    from .publish import Published

#: The roles a shared copy's users may be given (W3) — the frontend's role
#: ladder (`web/src/lib/itemPermission.ts`), spelled once here for the server.
#: None holds change_permission or use_terminal, and none can share.
ROLE_VERBS: dict[str, tuple[str, ...]] = {
    "reader": ("read_meta", "read_chat", "read_content"),
    "participant": ("read_meta", "read_chat", "read_content", "use_wui", "converse"),
    "collaborator": (
        "read_meta",
        "read_chat",
        "read_content",
        "use_wui",
        "converse",
        "add_content",
        "edit_content",
        "execute",
    ),
}


@dataclass(frozen=True)
class Opened:
    slug: str
    item_id: str
    created: bool


def _subjects(row: Published) -> list[str]:
    """Who may use the publication, as permission subjects (C9)."""
    if row.reach == "everyone":
        return [ALL]
    if row.reach == "subjects":
        return list(row.subjects)
    return []


def shared_copy_permission(row: Published) -> Permission:
    """A shared copy's permission (W3): the publisher's item, opened to whoever
    may use the publication with the role it names."""
    subjects = _subjects(row)
    if not subjects:
        return Permission(visibility="private")
    grants = {verb: list(subjects) for verb in ROLE_VERBS[row.role]}
    return Permission(visibility="restricted", **grants)


def copy_plan(paths: list[str], agent_path: str | None) -> list[tuple[str, list[str]]]:
    """Which snapshot file lands where in a copy: ``(source, [destinations])``.

    Every file at its own path. A copy of an agent also gets its folder's
    skills and workflows where the root's are (A5, `apps.agent_spec`): a
    root skill the agent also has is left out WHOLE — its own wins, and a
    root file it does not have must not be left inside it — and its
    workflow file overwrites the root's of the same id."""
    if agent_path is None:
        return [(path, [path]) for path in paths]
    folder = agent_folder(agent_path)
    over = {path: dest for path in paths if (dest := overlay_destination(path, folder))}
    own_skills = {
        dest.split("/")[2] for dest in over.values() if dest.startswith(f"/{WORKSPACE_SKILL_DIR}/")
    }
    covered = set(over.values())
    out: list[tuple[str, list[str]]] = []
    for path in paths:
        parts = path.split("/")
        shadowed = (
            len(parts) > 3 and parts[1] == WORKSPACE_SKILL_DIR and parts[2] in own_skills
        ) or path in covered
        dests = [] if shadowed else [path]
        if path in over:
            dests.append(over[path])
        if dests:
            out.append((path, dests))
    return out


async def _copy_files(
    store: FileStore, source_ns: str, dest_ns: str, *, agent_path: str | None = None
) -> int:
    """Copy every file of ``source_ns`` into ``dest_ns`` by `copy_plan`;
    bytes written."""
    total = 0
    with tempfile.TemporaryDirectory(prefix="copy-") as tmp:
        scratch = Path(tmp) / "file"
        for path, dests in copy_plan(sorted(await store.ls(source_ns)), agent_path):
            await store.read_to_file(source_ns, path, scratch)
            for dest in dests:
                await store.write_from_path(dest_ns, dest, scratch, None)
                total += scratch.stat().st_size
    return total


#: How long a second open waits for the person's copy another open is making,
#: and how often it looks (round 3 of review: a copy is one only once it is
#: finished). Bounded, so a maker that died mid-copy is an error, not a hang.
READY_WAIT_S = 120.0
READY_POLL_S = 0.2


class CopyPreparing(Exception):
    """The person's copy is still being made by another open, past
    `READY_WAIT_S` — try again (a 503, `api/publish.py`)."""


#: A person's disk cap in bytes, 0 for none — `quota.user_limits`' answer,
#: handed in by `create_app` so a copy is held to the same rule as any write.
DiskLimit = Callable[[str], Awaitable[int]]


async def ensure_room(
    ledger: DiskLedger, limit_of: DiskLimit | None, owner: str, size: int, *, exclude: str = ""
) -> None:
    """Refuse (`UserDiskFull`, a 507) bytes that would take ``owner`` past their
    disk cap (C8: the existing per-person quotas bound what copies cost). Asked
    BEFORE anything is written — a copy or a snapshot made first and charged
    after is the over-quota state the cap exists to prevent. ``exclude`` is a
    ledger row the new bytes replace (a re-publish's old snapshot)."""
    if limit_of is None:
        return
    limit = await limit_of(owner)
    if not limit:
        return
    used = await ledger.total_for(owner, exclude=exclude)
    if used + size > limit:
        raise UserDiskFull(owner=owner, used=used, quota=limit, attempted=size)


def copy_size(row: Published) -> int:
    """What a copy of ``row`` writes: its ``copy_bytes`` (the snapshot plus an
    agent's folder written a second time where the root's are, A5), or the
    snapshot's size for a row published before it was recorded."""
    return row.copy_bytes or row.bytes


async def make_copy(
    spec: SpecStar,
    store: FileStore,
    ledger: DiskLedger,
    row: Published,
    *,
    owner: str,
    permission: Permission,
    limit_of: DiskLimit | None = None,
    item_id: str | None = None,
    exclude: str = "",
) -> tuple[str, bool]:
    """One new item seeded from ``row``'s snapshot, created as ``owner``:
    ``(its id, whether this call made it)``.

    The item is created FIRST, with ``if_not_exists`` and no ``published_from``:
    ``item_id`` names a person's copy (`open_copy`), so of two opens at once
    one creates it and the other is refused — ``(id, False)``, having written
    none of its files, size or ledger row — and waits in `open_copy` until
    the copy is finished. It is finished in the LAST write, which sets
    ``published_from``: until then nothing (`copies_of`, a second open) can
    hand it out half made. Everything after the create belongs to the caller
    that made it; if any of it fails, the item and its files go. A snapshot
    that vanished meanwhile (unpublished) answers as gone. ``exclude`` — a
    ledger row the cap check leaves out (`ensure_room`)."""
    found = find_work_item(spec, row.item_id)
    if found is None:
        raise LookupError(f"the source of {row.path} is gone")
    _slug, source = found
    await ensure_room(ledger, limit_of, owner, copy_size(row), exclude=exclude)
    rm = spec.get_resource_manager(app_model(row.slug))
    item_id = item_id or f"{rm.resource_name}:{uuid.uuid4()}"
    changes: dict = {
        "title": row.title,
        "owner": owner,
        # C4: the Shared values the source holds NOW.
        "env_vars": dict(source.env_vars),
        "permission": permission,
        # Stated by the owner's own write below, never by the create: a create
        # may not state an environment size (`perm.checker._check_create`).
        "sandbox_cpu_cores": None,
        "sandbox_memory_bytes": None,
        "sandbox_disk_bytes": None,
        # Set in the LAST write, once the files are in: until then the item
        # is no copy of anything — `copies_of` cannot hand it out half made.
        "published_from": "",
        "published_kind": row.kind,
        "published_path": row.path,
        "published_title": row.title,
        "published_version": row.published_at,
        # Facts about the SOURCE, not settings (e.g. the legacy records it
        # absorbed, #700): a copy does not carry them.
        **fields_a_copy_does_not_carry(),
    }
    if isinstance(source.members, list):
        changes["members"] = []  # C3: members are not copied
    item = msgspec.structs.replace(source, **changes)
    with rm.using(owner):
        try:
            rm.create(
                item,
                resource_id=item_id,
                if_not_exists=True,  # ty: ignore[unknown-argument] — on the concrete manager
            )
        except DuplicateResourceError:
            return item_id, False
        try:
            copied = await _copy_files(
                store, row.snapshot, item_id, agent_path=row.path if row.kind == "agent" else None
            )
            await ledger.record(item_id, owner, copied)
            # The copy is finished: it becomes one (`published_from`), with the
            # publisher's size for the copies (C10) — written as the copy's
            # owner, who holds change_permission on it, the verb the size is
            # gated on; clamped on the way out like any item's own setting.
            rm.update(
                item_id,
                msgspec.structs.replace(
                    item,
                    published_from=publication_id(row),
                    sandbox_cpu_cores=row.cpu_cores,
                    sandbox_memory_bytes=row.memory_bytes,
                    # The SOURCE's disk (plan-item-disk-size, review round 1):
                    # the copy's files are the source's, and a source raised
                    # above its App's size and filled would otherwise make
                    # every copy full at birth — every turn refused. A ceiling,
                    # not a reservation: it costs the opener nothing until the
                    # copy actually grows.
                    sandbox_disk_bytes=source.sandbox_disk_bytes,
                ),
            )
        except BaseException as exc:
            rm.permanently_delete(item_id)
            await store.purge(item_id)
            if isinstance(exc, FileNotFound):
                raise LookupError(f"{row.path} was unpublished while the copy was made") from None
            raise
    return item_id, True


def publication_id(row: Published) -> str:
    from .publish import published_id

    return published_id(row.item_id, row.path)


def copies_of(spec: SpecStar, row: Published) -> list[tuple[str, str]]:
    """``(item_id, created_by)`` of every copy of ``row`` the caller may list —
    the access scope applies, so a request sees its own copies (and shared
    ones it is in), never everyone's."""
    rm = spec.get_resource_manager(app_model(row.slug))
    out: list[tuple[str, str]] = []
    query = (QB["published_from"] == publication_id(row)).build()
    for res in rm.list_resources(query):
        assert isinstance(res.data, WorkItemBase)
        item_id = res.info.resource_id  # ty: ignore[unresolved-attribute]
        # A copy in the trash is not one to land in or to list: reading it
        # answers "deleted", and a person whose copy is there must still be
        # able to open the publication (they get a new one, `open_copy`).
        try:
            meta = rm.get_meta(item_id)
        except ResourceIsDeletedError:
            continue
        out.append((item_id, meta.created_by))
    return out


def _copy_state(spec: SpecStar, row: Published, item_id: str) -> str:
    """``absent`` / ``deleted`` (in the trash) / ``preparing`` (made, not yet
    finished) / ``ready`` — what the derived id of a person's copy holds."""
    if find_work_item(spec, item_id) is None:
        return "deleted" if find_work_item(spec, item_id, include_deleted=True) else "absent"
    found = find_work_item(spec, item_id)
    assert found is not None
    return "ready" if found[1].published_from == publication_id(row) else "preparing"


async def await_finished(spec: SpecStar, row: Published, item_id: str) -> bool:
    """Wait for the copy another open is making: True once it is finished,
    False if it is gone (that maker failed). Bounded by `READY_WAIT_S`."""
    waited = 0.0
    while waited < READY_WAIT_S:
        state = _copy_state(spec, row, item_id)
        if state == "ready":
            return True
        if state != "preparing":
            return False
        await asyncio.sleep(READY_POLL_S)
        waited += READY_POLL_S
    raise CopyPreparing(f"the copy of {row.path} is still being made")


def person_copy_id(spec: SpecStar, row: Published, opener: str) -> str:
    """The id ``opener``'s one copy of ``row`` is made under (W3): derived, not
    drawn, so two opens at once — two tabs, two pods — both make THIS item and
    the store keeps one."""
    rm = spec.get_resource_manager(app_model(row.slug))
    return f"{rm.resource_name}:{uuid.uuid5(uuid.NAMESPACE_URL, f'{publication_id(row)}|{opener}')}"


async def open_copy(
    spec: SpecStar,
    store: FileStore,
    ledger: DiskLedger,
    row: Published,
    *,
    opener: str,
    limit_of: DiskLimit | None = None,
) -> Opened:
    """The workspace ``opener`` lands in when they open ``row``."""
    if row.sharing == "direct":
        return Opened(row.slug, row.item_id, created=False)
    if row.sharing == "shared":
        # The one copy everyone uses: if it was deleted, the publication has
        # nothing to hand out — gone, not a link to a missing item.
        if not row.shared_copy or find_work_item(spec, row.shared_copy) is None:
            raise LookupError(f"the shared copy of {row.path} is gone")
        return Opened(row.slug, row.shared_copy, created=False)
    private = Permission(visibility="private")
    if row.sharing == "per_person":
        # Who CREATED it — the person its access is decided for — not `owner`,
        # an editable display field (#687).
        for item_id, created_by in copies_of(spec, row):
            if created_by == opener:
                return Opened(row.slug, item_id, created=False)
        named = person_copy_id(spec, row, opener)
        # Refused (`if_not_exists`) when the id is held at all — by a copy
        # another open is making, or one in the trash.
        item_id, made = await make_copy(
            spec,
            store,
            ledger,
            row,
            owner=opener,
            permission=private,
            limit_of=limit_of,
            item_id=named,
        )
        if made:
            return Opened(row.slug, item_id, created=True)
        # Another open is making it: the person gets it once it is whole. Not
        # coming — its maker failed, or it is a copy they deleted, whose files
        # stay for a restore — then a fresh copy below, never one over it.
        if await await_finished(spec, row, named):
            return Opened(row.slug, named, created=False)
    item_id, made = await make_copy(
        spec, store, ledger, row, owner=opener, permission=private, limit_of=limit_of
    )
    return Opened(row.slug, item_id, created=made)
