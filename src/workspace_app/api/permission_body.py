"""Shared permission-setter plumbing (#262 / #306).

The PUT-body schema, the output schema, the newly-granted-users diff, and the
body→``Permission`` builder — reused by every resource's `PUT …/permission`
endpoint (collection, App WorkItem, and KbChat next), so the setter is written
once. Enforcement lives in ``perm`` (`authorize` + `access_scope` + the write
checker); this module is only the HTTP shape.
"""

from __future__ import annotations

from typing import cast

from fastapi import HTTPException
from pydantic import BaseModel

from ..perm import Permission
from ..perm.model import VERBS, Visibility


class PermissionBody(BaseModel):
    """Body of `PUT …/permission`: the full desired access state (PUT = replace).
    `visibility` decides whether the grant lists are enforced; the lists always
    persist, so toggling public↔restricted↔private never loses settings. Each grant
    entry is a subject token (`user:<id>` / `group:<id>` / `all`)."""

    visibility: str  # public | restricted | private (validated against Permission)
    read_meta: list[str] = []
    write_meta: list[str] = []
    read_content: list[str] = []
    add_content: list[str] = []
    edit_content: list[str] = []
    read_chat: list[str] = []
    converse: list[str] = []
    execute: list[str] = []
    use_terminal: list[str] = []
    change_permission: list[str] = []


class PermissionOut(BaseModel):
    """The persisted permission after a set — the FE refreshes the resource from it
    (and re-reads the list, since visibility may now hide it)."""

    resource_id: str
    visibility: str
    notified: list[str]  # users newly granted access who got a `share` notification


def build_permission(body: PermissionBody) -> Permission:
    """Validate `visibility` and construct the full `Permission` (400 on a bad
    visibility)."""
    if body.visibility not in ("public", "restricted", "private"):
        raise HTTPException(status_code=400, detail=f"invalid visibility {body.visibility!r}")
    return Permission(
        visibility=cast(Visibility, body.visibility),
        read_meta=body.read_meta,
        write_meta=body.write_meta,
        read_content=body.read_content,
        add_content=body.add_content,
        edit_content=body.edit_content,
        read_chat=body.read_chat,
        converse=body.converse,
        execute=body.execute,
        use_terminal=body.use_terminal,
        change_permission=body.change_permission,
    )


def granted_user_ids(perm: Permission | None) -> set[str]:
    """The concrete user ids appearing in ANY of a permission's grant lists (the
    `group:` namespace + the `all` wildcard are not addressable recipients, so
    they're skipped). Used to diff old→new for share notifications."""
    if perm is None:
        return set()
    prefix = "user:"
    return {
        subj[len(prefix) :]
        for verb in VERBS
        for subj in perm.grants(verb)
        if subj.startswith(prefix)
    }
