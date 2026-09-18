"""The WUI overview: which pages have been Deployed, and who may see them.

A WUI lives in the item that made it and nothing outside knows it exists
(`docs/plan-wui-overview.md`). Deploy is the one act that says "this page is
for other people", so it is the one door: the pane's Deploy — after its build
and its own verify read — posts here, and the row it writes is what `/wui`
lists. No write-time index of `view: wui` files: `*.ai.yaml` is also every
board / table / gantt view, so a filename index would have listed every PM
item and a content-aware hook would have had to read the file on every write
door — and the schedule index's history is a list of doors that were missed.

The listing NEVER widens access. A row is shown to a viewer only when they may
`read_content` the item — the verb `WuiPage`'s own file reads are gated on —
so what the overview lists is exactly what the viewer could open by address.
A deleted item, or one the viewer lost access to, simply contributes nothing;
the row stays in the store, because it is not this module's business to know
whether the item is coming back.

A row leaves on purpose only: `DELETE` (Remove on the overview). NOT when the
view file is gone — on this platform "the file does not exist" is also what a
sandbox mid-restore answers, and unlisting on that would make pages blink out
after every idle reap and need a fresh Deploy to come back.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

import yaml
from fastapi import APIRouter, FastAPI, HTTPException, Response
from msgspec import Struct
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..files import WorkspaceFiles
from ..filestore.protocol import FileNotFound
from ..perm.model import Verb
from ..sync.ignore import DEFAULT_IGNORES, should_ignore
from .file_routes import _workspace_path
from .locator import ItemLocator
from .timeutil import now_ms

#: What a view file's name ends in. The kind is inside the file (`view:`), so
#: the suffix alone never says "WUI" — it only says "worth reading".
VIEW_SUFFIX = ".ai.yaml"


class DeployedWui(Struct):
    """One Deployed page. ``resource_id`` is `deployed_id(item_id, path)`, so a
    second Deploy of the same page is an update of the same row — never a
    duplicate — and Remove is a point delete. Last Deploy wins is the meaning,
    so no CAS."""

    slug: str
    item_id: str
    path: str
    title: str
    deployed_by: str
    deployed_at: int
    # The view file's `icon:` as written — a file name in the page's folder, an
    # emoji, or a named-icon key — or "" for none (`page_icon`). Which form it
    # is, and whether it resolves, is the overview's call at render; a row
    # written before this field existed decodes to "" and draws the default.
    icon: str = ""


#: `filestore/specstar_impl._fid`'s spelling: specstar ids can't hold an ASCII
#: `/`, so every one in the path becomes U+2215. The quoted item id has neither,
#: and the path starts with `/` → U+2215, so the boundary is unambiguous.
_SLASH = "∕"


def deployed_id(item_id: str, path: str) -> str:
    return quote(item_id, safe="") + path.replace("/", _SLASH)


def register_deployed_wui(spec: SpecStar) -> None:
    """Idempotently register the model, post-``spec.apply`` so its auto-CRUD
    routes stay unemitted — an authenticated caller must not be able to PUT
    themselves onto the overview."""
    with contextlib.suppress(ValueError):
        spec.add_model(DeployedWui, indexed_fields=["item_id", "deployed_at"])


class DeployedPages:
    """The rows, newest first."""

    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec

    def _rm(self):
        return self._spec.get_resource_manager(DeployedWui)

    def record(self, row: DeployedWui) -> None:
        """Upsert: the same page Deployed again replaces its row."""
        rm = self._rm()
        rid = deployed_id(row.item_id, row.path)
        try:
            rm.get(rid)
        except ResourceIDNotFoundError:
            rm.create(row, resource_id=rid)
            return
        rm.update(rid, row)

    def forget(self, item_id: str, path: str) -> None:
        """Hard delete — a soft-deleted row still lists, and "removed from the
        overview" must mean gone from the listing. Absent is not an error:
        Remove pressed twice is not a fault."""
        with contextlib.suppress(ResourceIDNotFoundError):
            self._rm().permanently_delete(deployed_id(item_id, path))

    def newest_first(self) -> list[DeployedWui]:
        rows: list[DeployedWui] = []
        for res in self._rm().list_resources(QB.all().sort("-deployed_at").build()):
            data = res.data
            assert isinstance(data, DeployedWui)  # narrow for ty (coverage-clean)
            rows.append(data)
        return rows


def page_title(doc: Mapping[str, Any], path: str) -> str:
    """The row's name: the view file's ``title:``, else the page's folder name,
    else (a page at the workspace root, which has no folder) the file's stem."""
    title = doc.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    parts = path.strip("/").split("/")
    if len(parts) > 1:
        return parts[-2]
    # A file named exactly `.ai.yaml` has an empty stem, and an empty title is
    # a row nobody can read or press: the file's own name then.
    return parts[-1].removesuffix(VIEW_SUFFIX) or parts[-1]


def page_icon(doc: Mapping[str, Any]) -> str:
    """The view file's ``icon:``, stripped, when it is a non-empty string;
    anything else is "none". Not resolved here: a file that is not there or a
    key nobody knows draws the default circle on the overview, and a Deploy is
    not refused over a decoration (`plan-wui-overview-icon-favourites`)."""
    icon = doc.get("icon")
    if isinstance(icon, str) and icon.strip():
        return icon.strip()
    return ""


class DeployBody(BaseModel):
    path: str


class DeployedPage(BaseModel):
    slug: str
    item_id: str
    item_title: str
    path: str
    title: str
    deployed_by: str
    deployed_at: int
    icon: str
    can_remove: bool


class Overview(BaseModel):
    pages: list[DeployedPage]


def register_wui_deploy_routes(
    app: FastAPI | APIRouter,
    *,
    locator: ItemLocator,
    files: WorkspaceFiles,
    pages: DeployedPages,
    get_user_id: Callable[[], str],
    now: Callable[[], int] | None = None,
) -> None:
    @app.post("/a/{slug}/items/{item_id}/wui/deploy", response_model=DeployedPage)
    async def deploy_wui(slug: str, item_id: str, body: DeployBody) -> DeployedPage:
        """List this page on the overview.

        The verb is `edit_content`: the people who may change what the item
        holds may put it up and take it down. A NEW gate — the Deploy of a page
        with no build touched no server route at all before this, so a reader
        could press it and be handed the address. The address still WORKS for
        them (`/w/` is gated on `read_content` — a shortcut, not a grant); the
        pane no longer hands it over, because Deploy now ends here and theirs
        is refused.

        The server reads the view file itself. The row is the server's claim
        that this is a WUI with this title, so the server checks it rather than
        trusting a body the pane composed.
        """
        workspace_id = locator.require_access(slug, item_id, "edit_content")
        path = _workspace_path(body.path)
        if not path.endswith(VIEW_SUFFIX):
            raise HTTPException(status_code=400, detail=f"not a view file: {path}")
        if should_ignore(path, DEFAULT_IGNORES):
            # A vendored or unpacked copy under `node_modules/` is not a page
            # anybody made — the schedule index refuses these for the same
            # reason, and shares the list rather than re-spelling it.
            raise HTTPException(status_code=400, detail=f"not a page of this item: {path}")
        try:
            raw = await files.read(workspace_id, path)
        except FileNotFound:
            raise HTTPException(status_code=400, detail=f"view file not found: {path}") from None
        try:
            loaded = yaml.safe_load(raw.decode("utf-8", errors="replace"))
        except (yaml.YAMLError, RecursionError) as exc:
            # `RecursionError`: a document nested past the parser's depth
            # (`view: [[[[…`) is not valid yaml from where this route stands,
            # and a 500 would blame the platform for a file the caller wrote.
            raise HTTPException(
                status_code=400, detail=f"view file is not valid yaml: {exc}"
            ) from None
        # A view file is a mapping; anything else has no `view:` to read.
        doc: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
        kind = doc.get("view")
        if kind != "wui":
            found = kind if isinstance(kind, str) else "none"
            raise HTTPException(status_code=400, detail=f"not a WUI: {path} has view {found!r}")
        row = DeployedWui(
            slug=slug,
            item_id=workspace_id,
            path=path,
            title=page_title(doc, path),
            icon=page_icon(doc),
            deployed_by=get_user_id(),
            # Resolved at call time so a test can hold the clock still.
            deployed_at=(now or now_ms)(),
        )
        pages.record(row)
        return DeployedPage(
            **{f: getattr(row, f) for f in DeployedWui.__struct_fields__},
            item_title=locator.title_of(workspace_id) or "",
            can_remove=True,
        )

    @app.delete("/a/{slug}/items/{item_id}/wui/deploy", status_code=204)
    async def undeploy_wui(slug: str, item_id: str, path: str) -> Response:
        """Take the page off the overview. The page and its folder stay; only
        the listing goes. Same verb as Deploy — whoever may put it up may take
        it down. 204 whether or not a row existed: Remove pressed twice is not
        an error, and a row that is already gone is the state asked for."""
        workspace_id = locator.require_access(slug, item_id, "edit_content")
        pages.forget(workspace_id, _workspace_path(path))
        return Response(status_code=204)

    def _may(slug: str, item_id: str, verb: Verb) -> bool:
        """The locator's gate as a yes/no. A refusal of any kind — 404 unknown,
        410 deleted, 403 — is "no": the listing must never let one item take the
        page down, and which refusal it was is nobody's business here."""
        try:
            locator.require_access(slug, item_id, verb)
        except HTTPException:
            return False
        return True

    @app.get("/wui", response_model=Overview)
    async def wui_overview() -> Overview:
        """Every Deployed page the viewer may open, newest Deploy first.

        One access lookup and one title read per ITEM, not per row:
        `require_access` holds the facts for its window, and an item with
        several pages asks once.
        """
        decided: dict[tuple[str, str], tuple[bool, bool, str]] = {}
        out: list[DeployedPage] = []
        for row in pages.newest_first():
            key = (row.slug, row.item_id)
            if key not in decided:
                readable = _may(row.slug, row.item_id, "read_content")
                decided[key] = (
                    readable,
                    readable and _may(row.slug, row.item_id, "edit_content"),
                    # The title is a store read of its own, so it is memoised
                    # with the decision — and only for an item that will be
                    # shown, so a hidden item costs nothing but its refusal.
                    (locator.title_of(row.item_id) or "") if readable else "",
                )
            readable, removable, item_title = decided[key]
            if not readable:
                continue
            out.append(
                DeployedPage(
                    **{f: getattr(row, f) for f in DeployedWui.__struct_fields__},
                    item_title=item_title,
                    can_remove=removable,
                )
            )
        return Overview(pages=out)
