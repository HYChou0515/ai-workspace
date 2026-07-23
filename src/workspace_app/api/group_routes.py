"""#307 + #608 — logical Group management routes.

A flat Group of user ids. #608 governance model:

  * a SUPERUSER creates a group and designates its `owner`.
  * the OWNER (single; `effective_owner` = `owner` field or `created_by`) manages
    members + maintainers, transfers ownership, and deletes the group.
  * a MAINTAINER (delegated list) manages MEMBERS only — not each other, not
    transfer, not deletion, so delegation can't cascade.
  * a superuser bypasses all of the above.

Granting `group:<id>` on a resource's Permission then covers every current member
(`resources.groups.groups_of` resolves a user → their groups, folded into
`Actor.groups` / `subjects_of`). There is no per-group Permission — membership is
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

from ..resources.groups import Group, effective_owner


class _GroupBody(BaseModel):
    name: str = ""
    description: str = ""
    members: list[str] = []
    # #608 — the superuser creating the group designates its owner. Omitted ⇒ the
    # creator (a superuser) is the owner.
    owner: str | None = None


class _MembersBody(BaseModel):
    user_ids: list[str]


class _OwnerBody(BaseModel):
    owner: str


class GroupOut(BaseModel):
    """One group as the FE renders it. `owner` is the EFFECTIVE owner (the field,
    or the creator); `maintainers` are the delegated member-managers."""

    resource_id: str
    name: str
    description: str
    members: list[str]
    owner: str | None = None
    maintainers: list[str] = []


class PickableGroupOut(BaseModel):
    """#608 — a group as the share-dialog picker sees it: enough to grant to it,
    but NOT the member ids (org-canonical groups are grantable by anyone, so this
    is world-readable — member ids would leak the whole org's membership)."""

    resource_id: str
    name: str
    description: str
    member_count: int


def register_group_routes(
    app: FastAPI | APIRouter,
    spec: SpecStar,
    get_user_id: Callable[[], str],
    *,
    superusers: frozenset[str] = frozenset(),
) -> None:
    """Mount the Group CRUD + membership + delegation routes."""
    rm = spec.get_resource_manager(Group)

    def _load(group_id: str) -> tuple[Group, str]:
        """(group, effective_owner). 404 if missing."""
        try:
            rev = rm.get(group_id)
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail="group not found") from exc
        data = rev.data
        assert isinstance(data, Group)
        return data, effective_owner(data, rev.info.created_by)

    def _is_superuser() -> bool:
        return get_user_id() in superusers

    def _require_superuser() -> None:
        if not _is_superuser():
            raise HTTPException(status_code=403, detail="only a superuser can do this")

    def _deny(group: Group, owner: str) -> HTTPException:
        """403 for someone who can SEE the group (owner/maintainer/member) but may
        not perform this action; 404 for a stranger (no existence leak)."""
        me = get_user_id()
        if me == owner or me in group.maintainers or me in group.members:
            return HTTPException(status_code=403, detail="not authorized to manage this group")
        return HTTPException(status_code=404, detail="group not found")

    def _require_owner(group_id: str) -> tuple[Group, str]:
        """Manage maintainers / transfer / delete: the effective owner or a superuser."""
        group, owner = _load(group_id)
        if get_user_id() == owner or _is_superuser():
            return group, owner
        raise _deny(group, owner)

    def _require_manager(group_id: str) -> tuple[Group, str]:
        """Manage MEMBERS: the effective owner, a maintainer, or a superuser."""
        group, owner = _load(group_id)
        me = get_user_id()
        if me == owner or me in group.maintainers or _is_superuser():
            return group, owner
        raise _deny(group, owner)

    def _out(rev: Any) -> GroupOut:
        data = rev.data
        assert isinstance(data, Group)
        return GroupOut(
            resource_id=rev.info.resource_id,
            name=data.name,
            description=data.description,
            members=data.members,
            owner=effective_owner(data, rev.info.created_by),
            maintainers=data.maintainers,
        )

    @app.post("/groups")
    async def create_group(body: _GroupBody) -> GroupOut:
        # #608: org-canonical groups are superuser-created; the superuser designates
        # the owner (defaults to themselves). The owner is never listed as a
        # "member" — they're the manager.
        _require_superuser()
        owner = body.owner or get_user_id()
        members = sorted({u for u in body.members if u != owner})
        rev = rm.create(
            Group(name=body.name, description=body.description, members=members, owner=body.owner)
        )
        return GroupOut(
            resource_id=rev.resource_id,
            name=body.name,
            description=body.description,
            members=members,
            owner=owner,
            maintainers=[],
        )

    @app.get("/groups")
    async def list_groups() -> list[GroupOut]:
        """The groups the caller owns, maintains, or belongs to (indexed queries on
        `owner` / `maintainers` / `members`, plus `created_by` for owner-less groups
        the caller created). De-duped by id — the sets overlap only across queries,
        never within."""
        me = get_user_id()
        seen: dict[str, Any] = {}
        for rev in [
            *rm.list_resources((QB.created_by() == me).build()),
            *rm.list_resources((QB["owner"] == me).build()),
            *rm.list_resources(QB["maintainers"].contains(me).build()),
            *rm.list_resources(QB["members"].contains(me).build()),
        ]:
            seen.setdefault(rev.info.resource_id, rev)
        return [_out(rev) for rev in seen.values()]

    @app.get("/groups/pickable")
    async def list_pickable_groups() -> list[PickableGroupOut]:
        # #608: every group, name + count only — the share dialog grants `group:<id>`
        # to any of them (org-canonical). Declared BEFORE `/groups/{group_id}` so the
        # literal path wins over the param match.
        return [
            PickableGroupOut(
                resource_id=rev.info.resource_id,
                name=data.name,
                description=data.description,
                member_count=len(data.members),
            )
            for rev in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
            if isinstance((data := rev.data), Group)
        ]

    @app.get("/groups/{group_id}")
    async def get_group(group_id: str) -> GroupOut:
        group, owner = _load(group_id)
        me = get_user_id()
        related = me == owner or me in group.maintainers or me in group.members
        if not related and not _is_superuser():
            raise HTTPException(status_code=404, detail="group not found")
        return _out(rm.get(group_id))

    @app.post("/groups/{group_id}/members", status_code=204)
    async def add_members(group_id: str, body: _MembersBody) -> Response:
        group, owner = _require_manager(group_id)
        merged = sorted({*group.members, *(u for u in body.user_ids if u != owner)})
        if merged != group.members:
            rm.update(group_id, msgspec.structs.replace(group, members=merged))
        return Response(status_code=204)

    @app.delete("/groups/{group_id}/members/{user_id}", status_code=204)
    async def remove_member(group_id: str, user_id: str) -> Response:
        group, _ = _require_manager(group_id)
        if user_id in group.members:
            rm.update(
                group_id,
                msgspec.structs.replace(group, members=[u for u in group.members if u != user_id]),
            )
        return Response(status_code=204)

    @app.post("/groups/{group_id}/maintainers", status_code=204)
    async def add_maintainers(group_id: str, body: _MembersBody) -> Response:
        # #608: delegating member-management. Owner-only (a maintainer can't add
        # maintainers → no cascade). The owner is never their own maintainer.
        group, owner = _require_owner(group_id)
        merged = sorted({*group.maintainers, *(u for u in body.user_ids if u != owner)})
        if merged != group.maintainers:
            rm.update(group_id, msgspec.structs.replace(group, maintainers=merged))
        return Response(status_code=204)

    @app.delete("/groups/{group_id}/maintainers/{user_id}", status_code=204)
    async def remove_maintainer(group_id: str, user_id: str) -> Response:
        group, _ = _require_owner(group_id)
        if user_id in group.maintainers:
            rm.update(
                group_id,
                msgspec.structs.replace(
                    group, maintainers=[u for u in group.maintainers if u != user_id]
                ),
            )
        return Response(status_code=204)

    @app.put("/groups/{group_id}/owner")
    async def transfer_owner(group_id: str, body: _OwnerBody) -> GroupOut:
        # #608: hand the group to a new owner. Owner-only. The new owner is dropped
        # from members/maintainers (they're the manager now, not a delegate).
        group, _ = _require_owner(group_id)
        new = body.owner
        rm.update(
            group_id,
            msgspec.structs.replace(
                group,
                owner=new,
                members=[u for u in group.members if u != new],
                maintainers=[u for u in group.maintainers if u != new],
            ),
        )
        return _out(rm.get(group_id))

    @app.delete("/groups/{group_id}", status_code=204)
    async def delete_group(group_id: str) -> Response:
        _require_owner(group_id)
        rm.permanently_delete(group_id)
        return Response(status_code=204)
