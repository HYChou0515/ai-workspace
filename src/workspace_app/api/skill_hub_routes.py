"""The skill hub's read routes, and the item-side install door (plan P6).

Three surfaces read the same store: the skill hub page (list + detail), the
Skills panel's "install from the skill hub" (the install route here), and
the agent's three tools (`agent/tools.py`). Visibility is applied once, in
`SkillHubStore.visible` / `state_for`, and every route here goes through one
of those — an entry the viewer may not read is a 404 worded exactly like one
that never existed (Q10).

The install route and the `install_skill` tool share `install_hub_skill` and
the same refusals, so the two doors into a workspace cannot drift. The
management routes (unpublish / republish / permission / delete / transfer) are
owner-only — `owner` the explicit field, not `created_by` — and a non-owner
who can read the entry gets 403 on every one of them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel
from specstar import SpecStar

from ..apps.skill_hub import (
    SkillHubEntry,
    SkillHubStore,
    UpstreamState,
    matches_query,
    missing_tools_for,
    nest_forks,
)
from ..apps.skills import install_hub_skill, skill_folder_in_the_way, workspace_skill_payload
from ..files import WorkspaceFiles
from ..resources.groups import groups_of
from .item_authz import check_access, load_access_facts
from .locator import ItemLocator
from .permission_body import PermissionBody, PermissionOut, build_permission

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
    #: `referenced_tools` minus the ceiling of the App the list was asked for
    #: (`?app=`) — the install告知 the Skills panel's picker shows per row.
    #: Empty when no App was asked about.
    missing_tools: list[str] = []
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
    #: The full access state, for the owner's share dialog — the same shape
    #: `PUT …/permission` takes, so it round-trips. `None` for everyone else.
    permission: PermissionBody | None
    #: `referenced_tools` minus the ceiling of the App asked about (`?app=`);
    #: empty when no App was asked about.
    missing_tools: list[str]


class SkillTransferRequest(BaseModel):
    owner: str


class SkillTransferred(BaseModel):
    id: str
    owner: str


class SkillEditTarget(BaseModel):
    """Where the owner goes to edit an entry (plan P8's four-branch table).
    `open`: go to `item_id` (its `.skill/<name>/` was put back first when it
    had gone missing). `new_item`: the source item cannot take the edit —
    `reason` says why (`no_access` / `deleted` / `closed`) — so the page offers
    a new `app`/`profile` item to install into; re-publishing from there moves
    `source_item`."""

    action: Literal["open", "new_item"]
    app: str
    profile: str
    item_id: str = ""
    reason: Literal["", "no_access", "deleted", "closed"] = ""


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
    spec: SpecStar,
    superusers: frozenset[str] = frozenset(),
) -> None:
    """Mount the skill hub routes (reads, owner management, the edit resolver)
    + the item install route onto ``app``."""

    def _card(entry_id: str, entry: SkillHubEntry, viewer: str, app: str = "") -> SkillHubCard:
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
            missing_tools=missing_tools_for(entry.referenced_tools, app) if app else [],
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
    async def list_skill_hub(q: str = "", mine: bool = False, app: str = "") -> SkillHubList:
        """The page's list: roots with their forks beneath, in name order.
        `q` matches name and description (case-insensitive); `mine` keeps the
        viewer's own. A fork whose root is out of view — filtered by `q`,
        private to the viewer, or deleted — is listed on its own, so a skill is
        never hidden by what it was forked from. `app` (a slug) adds each
        row's `missing_tools` against that App's ceiling — the Skills panel's
        picker asks for the item's App, so the告知 is on the row it picks from."""
        viewer = get_user_id()
        hits = {
            i: e
            for i, e in hub.visible(viewer)
            if (not mine or e.owner == viewer) and matches_query(e, q)
        }
        roots: list[SkillHubCard] = []
        for root, forks in nest_forks(hits):
            card = _card(root, hits[root], viewer, app)
            card.forks = [_card(j, hits[j], viewer, app) for j in forks]
            roots.append(card)
        return SkillHubList(entries=roots)

    @app.get("/skill-hub/entries/{entry_id}")
    async def skill_hub_detail(entry_id: str, app: str = "") -> SkillHubDetail:
        """One entry in full. `?app=<slug>` adds the tool diff against that
        App's ceiling — what the install告知 shows before the person decides."""
        viewer = get_user_id()
        entry = _readable(entry_id, viewer)
        # One read: the file names are on the row (`origin.files`, the same
        # map an installed copy carries). Reading the folder to list it cost
        # up to the cap per page view (review round 2).
        skill_md = await hub.skill_md_of(entry_id)
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
            files=sorted(entry.origin.files),
            skill_md=skill_md.decode("utf-8", errors="replace"),
            is_owner=is_owner,
            visibility=entry.permission.visibility,
            permission=PermissionBody(**msgspec.to_builtins(entry.permission))
            if is_owner
            else None,
            missing_tools=missing_tools_for(entry.referenced_tools, app) if app else [],
        )

    # ── management: owner-only (plan Q7 / P7) ────────────────────────────

    def _owned(entry_id: str, viewer: str) -> SkillHubEntry:
        """404 when the viewer may not read it (the same 404 as never-was),
        403 when they may read it but do not own it. Owner is the explicit,
        transferable field — not `created_by` — so a transferred entry answers
        to its new owner at once."""
        entry = _readable(entry_id, viewer)
        if entry.owner != viewer:
            raise HTTPException(status_code=403, detail="only the owner may manage this entry")
        return entry

    @app.post("/skill-hub/entries/{entry_id}/unpublish")
    async def unpublish_skill_hub_entry(entry_id: str) -> PermissionOut:
        """Take it down: `visibility: private`. It leaves every other viewer's
        list, search and install; copies already made stay and read
        `unpublished`; the grant lists are kept for a later republish."""
        viewer = get_user_id()
        entry = _owned(entry_id, viewer)
        hub.set_permission(
            entry_id, msgspec.structs.replace(entry.permission, visibility="private")
        )
        return PermissionOut(resource_id=entry_id, visibility="private", notified=[])

    @app.post("/skill-hub/entries/{entry_id}/republish")
    async def republish_skill_hub_entry(entry_id: str) -> PermissionOut:
        """Back up, public. A restricted republish is a `permission` PUT."""
        viewer = get_user_id()
        entry = _owned(entry_id, viewer)
        hub.set_permission(entry_id, msgspec.structs.replace(entry.permission, visibility="public"))
        return PermissionOut(resource_id=entry_id, visibility="public", notified=[])

    @app.put("/skill-hub/entries/{entry_id}/permission")
    async def set_skill_hub_permission(entry_id: str, body: PermissionBody) -> PermissionOut:
        """The same body every other resource's share UI sends. Only
        `visibility` and `read_content` mean anything on an entry (reading is
        the only thing anyone but the owner does to one); the rest persist
        untouched so the shared UI round-trips."""
        viewer = get_user_id()
        _owned(entry_id, viewer)
        hub.set_permission(entry_id, build_permission(body))
        return PermissionOut(resource_id=entry_id, visibility=body.visibility, notified=[])

    @app.delete("/skill-hub/entries/{entry_id}", status_code=204)
    async def delete_skill_hub_entry(entry_id: str) -> Response:
        """Soft, final, no restore (Q5). Copies and forks read `deleted` from
        now on; the owner's own list drops it; a re-publish of the name is a
        new entry."""
        viewer = get_user_id()
        _owned(entry_id, viewer)
        await hub.delete(entry_id)
        return Response(status_code=204)

    @app.post("/skill-hub/entries/{entry_id}/transfer")
    async def transfer_skill_hub_entry(
        entry_id: str, body: SkillTransferRequest
    ) -> SkillTransferred:
        """Move ownership. The id — what every copy and fork points at — stays,
        so nothing downstream breaks (Q4). 409 when the new owner already
        publishes that name: `(owner, name)` is the identity."""
        viewer = get_user_id()
        entry = _owned(entry_id, viewer)
        new_owner = body.owner.strip()
        if not new_owner or new_owner == entry.owner:
            raise HTTPException(
                status_code=400, detail="transfer needs a different, non-empty owner"
            )
        if hub.find(new_owner, entry.name) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{new_owner} already publishes a skill named {entry.name!r}",
            )
        hub.transfer(entry_id, new_owner)
        return SkillTransferred(id=entry_id, owner=new_owner)

    @app.post("/skill-hub/entries/{entry_id}/edit")
    async def edit_skill_hub_entry(entry_id: str) -> SkillEditTarget:
        """「修改」: resolve the entry's source item into one of the table's
        branches (module docstring of the test file). A POST because the
        second branch WRITES — the folder goes back into the item before it
        opens. "Closed" is the App's own `lifecycle.closing_states`, read from
        its manifest here, never a status name this module knows."""
        from ..apps.manifest import load_app_manifest

        viewer = get_user_id()
        entry = _owned(entry_id, viewer)
        target = SkillEditTarget(
            action="new_item", app=entry.source_app, profile=entry.source_profile
        )
        facts = load_access_facts(spec, entry.source_item, include_deleted=True)
        if facts is None or facts.is_deleted:
            return target.model_copy(update={"reason": "deleted"})
        # The ONE item gate, not a copy of its body: editing a skill writes to
        # the item, so the verb is `edit_content`, and a superuser passes here
        # exactly as they pass `GET …/skills` on the same item.
        try:
            check_access(
                facts,
                facts.slug,
                entry.source_item,
                "edit_content",
                user=viewer,
                groups=groups_of(spec, viewer),
                superusers=superusers,
            )
        except HTTPException:
            return target.model_copy(update={"reason": "no_access"})
        lifecycle = load_app_manifest(facts.slug).lifecycle
        if lifecycle is not None:
            status = getattr(facts.item, lifecycle.status_field, None)
            if status is not None and str(status) in lifecycle.closing_states:
                return target.model_copy(update={"reason": "closed"})
        if not await workspace_skill_payload(files, entry.source_item, entry.name):
            # The folder went missing after publishing: the entry holds the
            # latest published version, so it goes back in as a copy.
            await install_hub_skill(files, entry.source_item, hub, entry_id)
        return target.model_copy(update={"action": "open", "item_id": entry.source_item})

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
