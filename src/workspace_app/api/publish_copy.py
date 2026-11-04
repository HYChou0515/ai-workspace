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

import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
from specstar import QB, SpecStar

from ..apps.base import WorkItemBase
from ..apps.registry import app_model
from ..apps.resolve import find_work_item
from ..filestore.protocol import FileStore
from ..perm.model import ALL, Permission
from ..quota.disk_ledger import DiskLedger

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


async def _copy_files(store: FileStore, source_ns: str, dest_ns: str) -> int:
    """Copy every file of ``source_ns`` into ``dest_ns``; bytes written."""
    total = 0
    with tempfile.TemporaryDirectory(prefix="copy-") as tmp:
        scratch = Path(tmp) / "file"
        for path in await store.ls(source_ns):
            await store.read_to_file(source_ns, path, scratch)
            await store.write_from_path(dest_ns, path, scratch, None)
            total += scratch.stat().st_size
    return total


async def make_copy(
    spec: SpecStar,
    store: FileStore,
    ledger: DiskLedger,
    row: Published,
    *,
    owner: str,
    permission: Permission,
) -> str:
    """One new item seeded from ``row``'s snapshot, created as ``owner``; its id."""
    found = find_work_item(spec, row.item_id)
    if found is None:
        raise LookupError(f"the source of {row.path} is gone")
    _slug, source = found
    rm = spec.get_resource_manager(app_model(row.slug))
    item_id = f"{rm.resource_name}:{uuid.uuid4()}"
    copied = await _copy_files(store, row.snapshot, item_id)
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
        "published_from": publication_id(row),
        "published_kind": row.kind,
        "published_path": row.path,
        "published_title": row.title,
    }
    if isinstance(source.members, list):
        changes["members"] = []  # C3: members are not copied
    item = msgspec.structs.replace(source, **changes)
    with rm.using(owner):
        rm.create(item, resource_id=item_id)
        if row.cpu_cores is not None or row.memory_bytes is not None:
            # C10: the publisher's size for the copies — written as the copy's
            # owner, who holds change_permission on it, the verb these fields
            # are gated on; clamped on the way out like any item's own setting.
            sized = msgspec.structs.replace(
                item, sandbox_cpu_cores=row.cpu_cores, sandbox_memory_bytes=row.memory_bytes
            )
            rm.update(item_id, sized)
    await ledger.record(item_id, owner, copied)
    return item_id


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
        out.append((item_id, rm.get_meta(item_id).created_by))
    return out


async def open_copy(
    spec: SpecStar, store: FileStore, ledger: DiskLedger, row: Published, *, opener: str
) -> Opened:
    """The workspace ``opener`` lands in when they open ``row``."""
    if row.sharing == "direct":
        return Opened(row.slug, row.item_id, created=False)
    if row.sharing == "shared":
        return Opened(row.slug, row.shared_copy, created=False)
    if row.sharing == "per_person":
        # Who CREATED it — the person its access is decided for — not `owner`,
        # an editable display field (#687).
        for item_id, created_by in copies_of(spec, row):
            if created_by == opener:
                return Opened(row.slug, item_id, created=False)
    item_id = await make_copy(
        spec, store, ledger, row, owner=opener, permission=Permission(visibility="private")
    )
    return Opened(row.slug, item_id, created=True)
