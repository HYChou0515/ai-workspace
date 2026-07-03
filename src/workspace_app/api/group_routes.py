"""#307 — logical Group management routes (minimal viable).

A flat, owner-managed group of user ids. The owner (`created_by`) creates the
group and adds / removes members; a member can list + read the groups they're in.
Granting `group:<id>` on a resource's `Permission` then covers every current
member (`resources.groups.groups_of` resolves a user → their groups, folded into
`Actor.groups` / `subjects_of`). There is no per-group `Permission` — membership is
the only authority — so a group can't be shared or nested (v1; the reserved
`group:` subject namespace means adding either later needs no data migration).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..resources.groups import Group


class _GroupBody(BaseModel):
    name: str = ""
    description: str = ""
    members: list[str] = []


class _MembersBody(BaseModel):
    user_ids: list[str]


class GroupOut(BaseModel):
    """One group as the FE renders it — the owner drives the member editor, a
    member sees the group they belong to."""

    resource_id: str
    name: str
    description: str
    members: list[str]
    owner: str | None = None


def register_group_routes(
    app: FastAPI | APIRouter,
    spec: SpecStar,
    get_user_id: Callable[[], str],
    *,
    superusers: frozenset[str] = frozenset(),
) -> None:
    """Mount the Group CRUD + membership routes."""
    rm = spec.get_resource_manager(Group)

    def _load(group_id: str) -> tuple[Group, str]:
        """(group, owner). 404 if missing."""
        try:
            rev = rm.get(group_id)
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail="group not found") from exc
        data = rev.data
        assert isinstance(data, Group)
        return data, rev.info.created_by

    def _is_superuser() -> bool:
        return get_user_id() in superusers

    def _require_owner(group_id: str) -> tuple[Group, str]:
        """The owner (or a superuser) manages the group; a stranger who can't see
        it gets 404 (no existence leak), a member who isn't the owner gets 403."""
        group, owner = _load(group_id)
        me = get_user_id()
        if me == owner or _is_superuser():
            return group, owner
        # A member may see the group exists (it's theirs to be in) → 403; anyone
        # else can't see it at all → 404.
        if me in group.members:
            raise HTTPException(status_code=403, detail="only the group owner can manage members")
        raise HTTPException(status_code=404, detail="group not found")

    def _out(rev: Any) -> GroupOut:
        data = rev.data
        assert isinstance(data, Group)
        return GroupOut(
            resource_id=rev.info.resource_id,
            name=data.name,
            description=data.description,
            members=data.members,
            owner=rev.info.created_by,
        )

    @app.post("/groups")
    async def create_group(body: _GroupBody) -> GroupOut:
        # The creator owns the group; members are de-duped and never include the
        # owner implicitly (the owner isn't a "member" — they're the manager).
        members = sorted({u for u in body.members if u != get_user_id()})
        rev = rm.create(Group(name=body.name, description=body.description, members=members))
        return GroupOut(
            resource_id=rev.resource_id,
            name=body.name,
            description=body.description,
            members=members,
            owner=get_user_id(),
        )

    @app.get("/groups")
    async def list_groups() -> list[GroupOut]:
        """The groups the caller owns + the groups they're a member of (two indexed
        queries). The two sets are disjoint by construction — the owner is never in
        their own group's `members` (create / add both drop the caller) — so a plain
        concatenation needs no dedup."""
        me = get_user_id()
        owned = rm.list_resources((QB.created_by() == me).build())
        member = rm.list_resources(QB["members"].contains(me).build())
        return [_out(rev) for rev in [*owned, *member]]

    @app.get("/groups/{group_id}")
    async def get_group(group_id: str) -> GroupOut:
        group, owner = _load(group_id)
        me = get_user_id()
        if me != owner and me not in group.members and not _is_superuser():
            raise HTTPException(status_code=404, detail="group not found")
        return _out(rm.get(group_id))

    @app.post("/groups/{group_id}/members", status_code=204)
    async def add_members(group_id: str, body: _MembersBody) -> Response:
        group, _ = _require_owner(group_id)
        merged = sorted({*group.members, *(u for u in body.user_ids if u != get_user_id())})
        if merged != group.members:
            rm.update(group_id, msgspec.structs.replace(group, members=merged))
        return Response(status_code=204)

    @app.delete("/groups/{group_id}/members/{user_id}", status_code=204)
    async def remove_member(group_id: str, user_id: str) -> Response:
        group, _ = _require_owner(group_id)
        if user_id in group.members:
            rm.update(
                group_id,
                msgspec.structs.replace(group, members=[u for u in group.members if u != user_id]),
            )
        return Response(status_code=204)

    @app.delete("/groups/{group_id}", status_code=204)
    async def delete_group(group_id: str) -> Response:
        _require_owner(group_id)
        rm.permanently_delete(group_id)
        return Response(status_code=204)
