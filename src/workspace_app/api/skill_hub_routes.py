"""The skill hub's read routes, and the item-side install door (plan P6).

Three surfaces read the same store: the skill hub page (list + detail + zip),
the Skills panel's "install from the skill hub" (the install route here), and
the agent's three tools (`agent/tools.py`). Visibility is applied once, in
`SkillHubStore.visible` / `state_for`, and every route here goes through one
of those — an entry the viewer may not read is a 404 worded exactly like one
that never existed (Q10).

The install route and the `install_skill` tool share `install_hub_skill` and
the same refusals, so the two doors into a workspace cannot drift. Management
(unpublish / delete / transfer / visibility) is P7, on the detail page and
owner-only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..apps.skill_hub import (
    SkillHubEntry,
    SkillHubStore,
    UpstreamState,
    missing_tools_for,
    nest_forks,
)
from ..apps.skills import install_hub_skill, skill_folder_in_the_way
from ..files import WorkspaceFiles
from ..files.zip_download import (
    DownloadPrepared,
    prepare_zip,
    prepared_path,
    safe_zip_filename,
    stream_prepared_zip,
    write_zip_members,
)
from .locator import ItemLocator

logger = logging.getLogger(__name__)

#: One wording for every "not for you" — a private entry, a deleted one and an
#: id that never existed all answer this, so a 404 never says "exists, not for you".
_NOT_FOUND = "no such skill hub entry"


class SkillHubCard(BaseModel):
    """One row of the list. `forks` is filled for a root; a fork's own `forks`
    is always empty — one level, per `nest_forks` (a fork of a fork is listed
    as a root of its own)."""

    id: str
    owner: str
    name: str
    description: str
    source_app: str
    referenced_tools: list[str]
    forked_from: str
    review_verdict: Literal["ok", "notes"]
    is_mine: bool
    forks: list[SkillHubCard] = []


class SkillHubList(BaseModel):
    entries: list[SkillHubCard]


class SkillHubLineage(BaseModel):
    """What a fork was forked from, as THIS viewer may know it. `owner` / `name`
    are filled only when the root is `live` for them."""

    entry: str
    state: UpstreamState
    owner: str = ""
    name: str = ""


class SkillHubReviewOut(BaseModel):
    verdict: Literal["ok", "notes"]
    notes: list[str]
    model: str


class SkillHubDetail(BaseModel):
    id: str
    owner: str
    name: str
    description: str
    source_app: str
    source_profile: str
    #: The item it was published from — the owner's business (the edit route,
    #: P8, starts there); "" for everyone else.
    source_item: str
    referenced_tools: list[str]
    review: SkillHubReviewOut
    forked_from: SkillHubLineage | None
    forks: list[SkillHubCard]
    files: list[str]
    skill_md: str
    is_owner: bool
    visibility: Literal["public", "restricted", "private"]
    #: `referenced_tools` minus the ceiling of the App asked about (`?app=`);
    #: empty when no App was asked about.
    missing_tools: list[str]


class SkillInstallRequest(BaseModel):
    entry_id: str


class SkillInstalled(BaseModel):
    name: str
    missing_tools: list[str]


def register_skill_hub_routes(
    app: FastAPI | APIRouter,
    *,
    hub: SkillHubStore,
    files: WorkspaceFiles,
    locator: ItemLocator,
    get_user_id: Callable[[], str],
) -> None:
    """Mount the skill hub read routes + the item install route onto ``app``."""

    def _card(entry_id: str, entry: SkillHubEntry, viewer: str) -> SkillHubCard:
        return SkillHubCard(
            id=entry_id,
            owner=entry.owner,
            name=entry.name,
            description=entry.description,
            source_app=entry.source_app,
            referenced_tools=list(entry.referenced_tools),
            forked_from=entry.forked_from,
            review_verdict=entry.review.verdict,
            is_mine=entry.owner == viewer,
        )

    def _readable(entry_id: str, viewer: str) -> SkillHubEntry:
        _state, entry = hub.state_for(entry_id, viewer)
        if entry is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        return entry

    def _visible_forks(entry_id: str, viewer: str) -> list[SkillHubCard]:
        out: list[SkillHubCard] = []
        for fork_id in hub.forks_of(entry_id):
            _state, fork = hub.state_for(fork_id, viewer)
            if fork is not None:
                out.append(_card(fork_id, fork, viewer))
        out.sort(key=lambda c: (c.name, c.owner))
        return out

    @app.get("/skill-hub/entries")
    async def list_skill_hub(q: str = "", mine: bool = False) -> SkillHubList:
        """The page's list: roots with their forks beneath, in name order.
        `q` matches name and description (case-insensitive); `mine` keeps the
        viewer's own. A fork whose root is out of view — filtered by `q`,
        private to the viewer, or deleted — is listed on its own, so a skill is
        never hidden by what it was forked from."""
        viewer = get_user_id()
        needle = q.strip().lower()
        hits = {
            i: e
            for i, e in hub.visible(viewer)
            if (not mine or e.owner == viewer)
            and (not needle or needle in e.name.lower() or needle in e.description.lower())
        }
        roots: list[SkillHubCard] = []
        for root, forks in nest_forks(hits):
            card = _card(root, hits[root], viewer)
            card.forks = [_card(j, hits[j], viewer) for j in forks]
            roots.append(card)
        return SkillHubList(entries=roots)

    @app.get("/skill-hub/entries/{entry_id}")
    async def skill_hub_detail(entry_id: str, app: str = "") -> SkillHubDetail:
        """One entry in full. `?app=<slug>` adds the tool diff against that
        App's ceiling — what the install告知 shows before the person decides."""
        viewer = get_user_id()
        entry = _readable(entry_id, viewer)
        payload = await hub.payload_of(entry_id)
        lineage: SkillHubLineage | None = None
        if entry.forked_from:
            state, root = hub.state_for(entry.forked_from, viewer)
            lineage = SkillHubLineage(
                entry=entry.forked_from,
                state=state,
                owner=root.owner if root is not None else "",
                name=root.name if root is not None else "",
            )
        is_owner = entry.owner == viewer
        return SkillHubDetail(
            id=entry_id,
            owner=entry.owner,
            name=entry.name,
            description=entry.description,
            source_app=entry.source_app,
            source_profile=entry.source_profile,
            source_item=entry.source_item if is_owner else "",
            referenced_tools=list(entry.referenced_tools),
            review=SkillHubReviewOut(
                verdict=entry.review.verdict,
                notes=list(entry.review.notes),
                model=entry.review.model,
            ),
            forked_from=lineage,
            forks=_visible_forks(entry_id, viewer),
            files=sorted(payload),
            skill_md=payload.get("SKILL.md", b"").decode("utf-8", errors="replace"),
            is_owner=is_owner,
            visibility=entry.permission.visibility,
            missing_tools=missing_tools_for(entry.referenced_tools, app) if app else [],
        )

    @app.post("/skill-hub/entries/{entry_id}/download/prepare")
    async def prepare_skill_hub_download(entry_id: str) -> DownloadPrepared:
        """A zip of the entry's files, rooted at `<name>/` so it unpacks as the
        folder another tool (opencode, a `.skill/` dir) expects. Same two-step
        shape as the workspace download."""
        viewer = get_user_id()
        entry = _readable(entry_id, viewer)
        payload = await hub.payload_of(entry_id)
        members = [(f"{entry.name}/{rel}", data) for rel, data in sorted(payload.items())]
        download_id, size = await prepare_zip(lambda out: write_zip_members(out, members))
        logger.info(
            "skill_hub_routes: prepared download %s of entry %s (%d bytes)",
            download_id,
            entry_id,
            size,
        )
        return DownloadPrepared(
            download_id=download_id,
            filename=safe_zip_filename(entry.name, fallback="skill"),
            size=size,
        )

    @app.get("/skill-hub/entries/{entry_id}/download/{download_id}")
    async def stream_skill_hub_download(entry_id: str, download_id: str) -> FileResponse:
        viewer = get_user_id()
        entry = _readable(entry_id, viewer)
        path = prepared_path(download_id)
        if path is None:
            raise HTTPException(status_code=404, detail="download not found")
        return stream_prepared_zip(path, safe_zip_filename(entry.name, fallback="skill"))

    @app.post("/a/{slug}/items/{item_id}/skills/install")
    async def install_skill_into_item(
        slug: str, item_id: str, body: SkillInstallRequest
    ) -> SkillInstalled:
        """The Skills panel's install button. The same core and the same
        refusals as the `install_skill` tool: 404 for an entry the viewer may
        not read, 409 when a folder of that name is already there (naming
        whose copy it is when it is one), and the tools this App lacks named
        in the answer rather than enforced."""
        viewer = get_user_id()
        investigation_id = locator.require_access(slug, item_id, "edit_content")
        entry = _readable(body.entry_id, viewer)
        if taken := await skill_folder_in_the_way(files, investigation_id, hub, entry.name, viewer):
            raise HTTPException(status_code=409, detail=taken)
        name = await install_hub_skill(files, investigation_id, hub, body.entry_id)
        return SkillInstalled(
            name=name, missing_tools=missing_tools_for(entry.referenced_tools, slug)
        )
