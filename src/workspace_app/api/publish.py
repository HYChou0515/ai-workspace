"""Publishing a WUI or an agent (`docs/plan-wui-publish-and-agent-hub.md`, P1).

Two things to people, one core underneath: a WUI (`view: wui`, a screen) and an
agent (`view: agent`, an assistant) are published, found and opened in
different places — but both are ONE `Published` row and, for every kind that
hands out copies, ONE snapshot of the workspace as it stood when it was
published (C3, C7).

The snapshot lives in a file-store namespace of its own, ``published:<row>:<at>``
— the same opaque-namespace precedent as skill hub's ``skill-hub:<id>`` — and is
charged to the PUBLISHER in the disk ledger (C10). Only the latest is kept: a
re-publish writes the new one first and purges the old after the row points at
the new one, so a copy being made never finds its source gone halfway.

What a copy must never carry is left out of the snapshot (C3): run journals
(`.workflow/`), every `schedules.json` (there is no per-row pause, and N copies
would all fire), and the platform's read-only mount (`.readonly/`, which the
zip download skips for the same reason — it is not the item's). Conversations,
permission and members are not files, so they are not in it to begin with.

Publishing needs `change_permission` (C9): it hands the workspace — its Shared
values included — to whoever the publisher names, and deciding who gets what
this item holds is that verb's. The AI can never hold it, so the AI can never
publish.
"""

from __future__ import annotations

import contextlib
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Response
from msgspec import Struct
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..apps.resolve import find_work_item
from ..files import WorkspaceFiles
from ..filestore.protocol import FileNotFound, FileStore
from ..perm.authorize import Actor, authorize
from ..perm.model import ALL, Permission
from ..quota.disk_ledger import DiskLedger
from ..resources.groups import groups_of
from .file_routes import _workspace_path
from .item_authz import user_may
from .locator import ItemLocator
from .schedule_index import is_schedule_file
from .timeutil import now_ms
from .wui_deploy import page_color, page_icon, page_title
from .wui_page import NotAPage, load_view_file

Kind = Literal["wui", "agent"]
#: How copies are shared. A WUI's four (W2) and an agent's one (A2).
Sharing = Literal["fresh", "per_person", "shared", "direct", "per_conversation"]
Reach = Literal["me", "subjects", "everyone"]
Role = Literal["reader", "participant", "collaborator"]

#: Which ways of sharing each kind takes. A WUI always asks (no default, W2); an
#: agent is one copy per conversation, fixed (A2).
SHARINGS: dict[Kind, frozenset[str]] = {
    "wui": frozenset({"fresh", "per_person", "shared", "direct"}),
    "agent": frozenset({"per_conversation"}),
}
#: The kinds that hand out copies, and so take a snapshot. "direct" is today's
#: Deploy: others act inside the source workspace itself.
COPYING: frozenset[str] = frozenset({"fresh", "per_person", "shared", "per_conversation"})

#: The namespace prefix every snapshot lives under.
SNAPSHOT_PREFIX = "published:"


class ReleaseNote(Struct):
    """One re-publish's "what changed" (A9)."""

    at: int
    by: str
    text: str


class Published(Struct):
    """One published WUI or agent. ``resource_id`` is `published_id(item_id,
    path)`: publishing the same file again is an update of the same row."""

    kind: Kind
    slug: str
    item_id: str
    path: str
    title: str
    sharing: Sharing
    published_by: str
    published_at: int
    icon: str = ""
    color: str = ""
    description: str = ""
    #: Who may use it (C9): the publisher only, named subjects
    #: (``user:<id>`` / ``group:<id>``), or everyone signed in.
    reach: Reach = "me"
    subjects: list[str] = []
    #: "One shared by everyone" only (W3): the role its users get.
    role: Role = "participant"
    #: The copies' sandbox size (C10); None ⇒ the App's.
    cpu_cores: float | None = None
    memory_bytes: int | None = None
    #: The snapshot's namespace; "" for "use my workspace directly".
    snapshot: str = ""
    files: int = 0
    bytes: int = 0
    notes: list[ReleaseNote] = []
    #: "One shared by everyone" (W3): the copy everybody lands in, made at
    #: publish in the publisher's name. A re-publish makes a new one; the old
    #: stays, an ordinary workspace of theirs.
    shared_copy: str = ""
    #: Published from a COPY of another publication (C14): what it is based on,
    #: as that copy recorded it — "based on X (by Y)".
    based_on_title: str = ""
    based_on_by: str = ""


#: `filestore/specstar_impl._fid`'s spelling, as `wui_deploy.deployed_id`.
_SLASH = "∕"


def published_id(item_id: str, path: str) -> str:
    return quote(item_id, safe="") + path.replace("/", _SLASH)


def snapshot_namespace(row_id: str, at: int) -> str:
    """A snapshot's own namespace: the row it belongs to and when it was taken,
    so a re-publish writes beside the old one instead of over it."""
    return f"{SNAPSHOT_PREFIX}{row_id}:{at}"


def excluded_from_snapshot(path: str) -> bool:
    """What a copy must never carry (C3), by workspace-absolute ``path``."""
    if path.startswith(("/.workflow/", "/.readonly/")):
        return True
    return is_schedule_file(path)


def register_publications(spec: SpecStar) -> None:
    """Idempotently register the model, post-``spec.apply`` so it grows no
    auto-CRUD routes — nobody may PUT a publication into existence."""
    with contextlib.suppress(ValueError):
        spec.add_model(Published, indexed_fields=["item_id", "kind", "published_at"])


def reach_permission(row: Published) -> Permission:
    """Who may use ``row``, spelled as a `Permission` on ``read_meta`` — so the
    ONE rule engine (`perm.authorize`) decides it: the publisher passes as the
    owner, a superuser passes, ``all`` is everyone signed in, and
    ``group:<id>`` resolves the way it does on an item."""
    if row.reach == "everyone":
        return Permission(visibility="restricted", read_meta=[ALL])
    if row.reach == "subjects":
        return Permission(visibility="restricted", read_meta=list(row.subjects))
    return Permission(visibility="private")


def may_use(spec: SpecStar, row: Published, user: str, superusers: frozenset[str]) -> bool:
    """May ``user`` open ``row`` (C9)? "Use my workspace directly" lands them IN
    the source, so the source's own permission decides that one (W2)."""
    if row.sharing == "direct":
        return user_may(spec, row.item_id, user, "read_content")
    actor = Actor.human(user, groups=groups_of(spec, user))
    return authorize(
        actor,
        "read_meta",
        reach_permission(row),
        created_by=row.published_by,
        superusers=superusers,
    )


class Publications:
    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec

    def _rm(self):
        return self._spec.get_resource_manager(Published)

    def get(self, item_id: str, path: str) -> Published | None:
        try:
            data = self._rm().get(published_id(item_id, path)).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(data, Published)  # narrow for ty (coverage-clean)
        return data

    def record(self, row: Published) -> None:
        rm = self._rm()
        rid = published_id(row.item_id, row.path)
        try:
            rm.get(rid)
        except ResourceIDNotFoundError:
            rm.create(row, resource_id=rid)
            return
        rm.update(rid, row)

    def forget(self, item_id: str, path: str) -> None:
        with contextlib.suppress(ResourceIDNotFoundError):
            self._rm().permanently_delete(published_id(item_id, path))

    def newest_first(
        self, *, kind: Kind | None = None, item_id: str | None = None
    ) -> list[Published]:
        query = QB.all()
        if kind is not None:
            query = QB["kind"] == kind
        if item_id is not None:
            query = (
                (QB["item_id"] == item_id) if kind is None else (query & (QB["item_id"] == item_id))
            )
        rows: list[Published] = []
        for res in self._rm().list_resources(query.sort("-published_at").build()):
            data = res.data
            assert isinstance(data, Published)
            rows.append(data)
        return rows

    def by_id(self, row_id: str) -> Published | None:
        try:
            data = self._rm().get(row_id).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(data, Published)
        return data

    def holds_snapshot(self, namespace: str) -> bool:
        """Is ``namespace`` the CURRENT snapshot of some row? What the disk
        ledger's reconcile asks before forgetting a row that is not an item."""
        if not namespace.startswith(SNAPSHOT_PREFIX):
            return False
        return any(row.snapshot == namespace for row in self.newest_first())


async def snapshot_paths(files: WorkspaceFiles, workspace_id: str) -> list[tuple[str, int]]:
    """``(path, size)`` of every file a snapshot of the workspace would hold.

    Read through the facade, which answers from the LIVE sandbox when one is
    up — so dependency folders the durable mirror declines to persist
    (`node_modules/`, `.venv/`) are in it, as C3 asks."""
    return [
        (p, size)
        for p, size in await files.stat_all(workspace_id, "")
        if not excluded_from_snapshot(p)
    ]


async def take_snapshot(
    files: WorkspaceFiles, store: FileStore, workspace_id: str, namespace: str
) -> tuple[int, int]:
    """Copy the workspace into ``namespace``; ``(files, bytes)`` written.

    File by file through a temporary file, so a big file is streamed rather
    than held in memory. A file that vanished between the listing and its read
    is skipped: the workspace is live, and "what it held when published" is
    what it holds while this runs."""
    count = total = 0
    with tempfile.TemporaryDirectory(prefix="publish-") as tmp:
        scratch = Path(tmp) / "file"
        for path, _size in await snapshot_paths(files, workspace_id):
            try:
                await files.read_to_file(workspace_id, path, scratch)
            except FileNotFound:
                continue
            await store.write_from_path(namespace, path, scratch, None)
            count += 1
            total += scratch.stat().st_size
    return count, total


async def unpublish_item(
    publications: Publications, store: FileStore, ledger: DiskLedger | None, item_id: str
) -> None:
    """Everything ``item_id`` published goes with it (C13): the rows, their
    snapshots and the snapshots' charge. Copies already made stay their
    openers' — they are items of their own. Part of the item-delete cascade."""
    for row in publications.newest_first(item_id=item_id):
        publications.forget(row.item_id, row.path)
        if row.snapshot:
            await store.purge(row.snapshot)
            if ledger is not None:
                await ledger.forget(row.snapshot)


class PublishBody(BaseModel):
    path: str
    #: No default (W2): a body that did not answer is refused by validation.
    sharing: Sharing
    reach: Reach = "me"
    subjects: list[str] = []
    role: Role = "participant"
    cpu_cores: float | None = None
    memory_bytes: int | None = None
    #: A re-publish's "what changed" (A9); "" ⇒ none.
    note: str = ""


class PublishedOut(BaseModel):
    #: The row id (`published_id`): what a copy's `published_from` holds.
    id: str
    kind: Kind
    slug: str
    item_id: str
    path: str
    title: str
    sharing: Sharing
    reach: Reach
    subjects: list[str]
    role: Role
    published_by: str
    published_at: int
    icon: str
    color: str
    description: str
    files: int
    bytes: int
    based_on_title: str
    based_on_by: str


def published_out(row: Published) -> PublishedOut:
    return PublishedOut(
        id=published_id(row.item_id, row.path),
        kind=row.kind,
        slug=row.slug,
        item_id=row.item_id,
        path=row.path,
        title=row.title,
        sharing=row.sharing,
        reach=row.reach,
        subjects=list(row.subjects),
        role=row.role,
        published_by=row.published_by,
        published_at=row.published_at,
        icon=row.icon,
        color=row.color,
        description=row.description,
        files=row.files,
        bytes=row.bytes,
        based_on_title=row.based_on_title,
        based_on_by=row.based_on_by,
    )


class OpenBody(BaseModel):
    item_id: str
    path: str


class OpenedOut(BaseModel):
    slug: str
    item_id: str
    created: bool


class FolderSize(BaseModel):
    folder: str
    files: int
    bytes: int


class Estimate(BaseModel):
    files: int
    bytes: int
    #: Top-level folders (the workspace root's own files as "/"), largest first.
    biggest: list[FolderSize]


def _top_folder(path: str) -> str:
    parts = path.strip("/").split("/")
    return "/" if len(parts) == 1 else f"/{parts[0]}"


def estimate_of(paths: list[tuple[str, int]]) -> Estimate:
    groups: dict[str, list[int]] = {}
    for path, size in paths:
        groups.setdefault(_top_folder(path), []).append(size)
    biggest = sorted(
        (FolderSize(folder=f, files=len(s), bytes=sum(s)) for f, s in groups.items()),
        key=lambda g: (-g.bytes, -g.files, g.folder),
    )
    return Estimate(files=len(paths), bytes=sum(s for _p, s in paths), biggest=biggest)


def register_publish_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    locator: ItemLocator,
    files: WorkspaceFiles,
    filestore: FileStore,
    publications: Publications,
    ledger: DiskLedger,
    get_user_id: Callable[[], str],
    superusers: frozenset[str] = frozenset(),
    now: Callable[[], int] | None = None,
) -> None:
    async def _view(workspace_id: str, path: str) -> tuple[Kind, dict]:
        try:
            kind, doc = await load_view_file(files, workspace_id, path)
        except NotAPage as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if kind not in SHARINGS:
            raise HTTPException(
                status_code=400,
                detail=f"{path} is neither a WUI nor an agent (view {kind or 'none'!r})",
            )
        return kind, doc  # ty: ignore[invalid-return-type]

    @app.post("/a/{slug}/items/{item_id}/publish", response_model=PublishedOut)
    async def publish(slug: str, item_id: str, body: PublishBody) -> PublishedOut:
        workspace_id = locator.require_access(slug, item_id, "change_permission")
        path = _workspace_path(body.path)
        kind, doc = await _view(workspace_id, path)
        if body.sharing not in SHARINGS[kind]:
            allowed = ", ".join(sorted(SHARINGS[kind]))
            raise HTTPException(
                status_code=400,
                detail=f"a {kind} is not shared {body.sharing!r}; it takes: {allowed}",
            )
        publisher = get_user_id()
        at = (now or now_ms)()
        previous = publications.get(workspace_id, path)
        snapshot, count, size = "", 0, 0
        if body.sharing in COPYING:
            snapshot = snapshot_namespace(published_id(workspace_id, path), at)
            count, size = await take_snapshot(files, filestore, workspace_id, snapshot)
        notes = list(previous.notes) if previous is not None else []
        if body.note.strip():
            notes.append(ReleaseNote(at=at, by=publisher, text=body.note.strip()))
        description = doc.get("description")
        based_on_title = based_on_by = ""
        found = find_work_item(spec, workspace_id)
        if found is not None and found[1].published_from:
            # C14: a copy published again says what it is based on.
            based_on_title = found[1].published_title
            origin = publications.by_id(found[1].published_from)
            based_on_by = origin.published_by if origin is not None else ""
        row = Published(
            kind=kind,
            slug=slug,
            item_id=workspace_id,
            path=path,
            title=page_title(doc, path),
            sharing=body.sharing,
            published_by=publisher,
            published_at=at,
            icon=page_icon(doc),
            color=page_color(doc),
            description=description.strip() if isinstance(description, str) else "",
            reach=body.reach,
            subjects=list(body.subjects),
            role=body.role,
            cpu_cores=body.cpu_cores,
            memory_bytes=body.memory_bytes,
            snapshot=snapshot,
            files=count,
            bytes=size,
            notes=notes,
            based_on_title=based_on_title,
            based_on_by=based_on_by,
        )
        if body.sharing == "shared":
            from .publish_copy import make_copy, shared_copy_permission

            row.shared_copy = await make_copy(
                spec,
                filestore,
                ledger,
                row,
                owner=publisher,
                permission=shared_copy_permission(row),
            )
        publications.record(row)
        if snapshot:
            await ledger.record(snapshot, publisher, size)
        # Only the latest is kept (C7/C10): the old snapshot goes once nothing
        # points at it any more.
        if previous is not None and previous.snapshot and previous.snapshot != snapshot:
            await filestore.purge(previous.snapshot)
            await ledger.forget(previous.snapshot)
        return published_out(row)

    @app.delete("/a/{slug}/items/{item_id}/publish", status_code=204)
    async def unpublish(slug: str, item_id: str, path: str) -> Response:
        """Take it down. Copies already made stay their openers' (C8)."""
        workspace_id = locator.require_access(slug, item_id, "change_permission")
        path = _workspace_path(path)
        row = publications.get(workspace_id, path)
        publications.forget(workspace_id, path)
        if row is not None and row.snapshot:
            await filestore.purge(row.snapshot)
            await ledger.forget(row.snapshot)
        return Response(status_code=204)

    @app.post("/published/open", response_model=OpenedOut)
    async def open_published(body: OpenBody) -> OpenedOut:
        """The workspace the caller lands in when they open a publication —
        theirs, made now if the way of sharing says so (P2)."""
        from .publish_copy import open_copy

        row = publications.get(body.item_id, _workspace_path(body.path))
        user = get_user_id()
        # Not usable reads as not published: whether it exists is not news for
        # someone who may not open it.
        if row is None or not may_use(spec, row, user, superusers):
            raise HTTPException(status_code=404, detail="not published")
        try:
            opened = await open_copy(spec, filestore, ledger, row, opener=user)
        except LookupError as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from None
        return OpenedOut(slug=opened.slug, item_id=opened.item_id, created=opened.created)

    @app.get("/a/{slug}/items/{item_id}/publish/estimate", response_model=Estimate)
    async def estimate(slug: str, item_id: str, path: str) -> Estimate:
        """What a snapshot would hold (C11): asked by the publish dialog, so it
        needs what publishing needs."""
        workspace_id = locator.require_access(slug, item_id, "change_permission")
        await _view(workspace_id, _workspace_path(path))
        return estimate_of(await snapshot_paths(files, workspace_id))
