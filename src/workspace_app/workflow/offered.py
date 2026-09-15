"""Which workflows an item may start — ONE answer for every entrance.

Three entrances start a workflow on an item: the Workflows panel's Run button,
a page's `startRun`, and a `schedules.json` row. They used to consult two
different lists. The panel resolved a profile workflow OR one the item authored
in `.workflows/<id>.json`; the page and the schedule consulted the profile's
list alone — which on an interactive profile is EMPTY. So a workflow the agent
had just saved was startable from the panel and refused from a page and from a
schedule, with an error that called it an authorisation problem ("This app does
not offer …"). There was nothing to authorise: the gate never looked in the
folder, and nothing could be configured to make it.

The rule, stated once: an item offers the workflows its profile declares AND
the ones saved under its own `.workflows/`, a workspace one shadowing a package
one of the same id (the orchestrator already resolves that way — this makes the
gates agree with the thing that runs). Nothing here widens what a workspace
workflow may do: `save_workflow` validated it against the profile's tool ceiling
when it was written, and the panel already let anyone with access to the item
run it.

Two shapes, because the callers ask two questions:

* `offered_workflow_ids` — the LIST, for a gate ("is `run` one of these?"). A
  listing of `.workflows/` and nothing more: it does not read the files, so a
  malformed one is still named here and fails LOUDLY at start, where the
  orchestrator says why, rather than vanishing from the list in silence. The
  listing itself is handed in (`ListFiles`), because WHICH store answers it is
  the caller's responsibility — see the note on that type.
* `resolve_offered_workflow` — ONE manifest, for a route that needs the phases
  or the title. Workspace first, so a shadowed package workflow's manifest is
  never handed out for a run that will execute the workspace one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection

from ..apps.profiles import load_profile_workflow, profile_workflows
from ..files import WorkspaceFiles
from .manifest import WorkflowManifest
from .workspace_store import (
    WORKSPACE_WORKFLOW_DIR,
    is_workspace_workflow_path,
    load_workspace_workflow,
)

#: `ls(workspace_id, prefix) -> paths`. The SOURCE is the caller's choice and
#: the difference is not cosmetic: `WorkspaceFiles.ls` (the facade) is warm-first,
#: and on the hosted backend that probe is the recovery trigger — a reaped
#: sandbox whose address is still held gets REBUILT by it. Right for a request
#: (somebody is about to use the item); wrong for the schedule sweep, which
#: would then resurrect every reaped sandbox that has a schedule, once per tick,
#: on every pod — the exact thing its `read=filestore.read` wiring avoids. The
#: sweep hands in the durable store's `ls`; a request hands in the facade's.
ListFiles = Callable[[str, str], Awaitable[list[str]]]


async def workspace_workflow_ids(ls: ListFiles, item_id: str) -> list[str]:
    """The ids of the workflows saved under this item's `.workflows/` — flat
    `<id>.json` only, the same shape `workspace_workflow_metas` lists, without
    reading any of them. The item's `schedules.json` lives in the same folder
    and is not one of them."""
    prefix = f"/{WORKSPACE_WORKFLOW_DIR}/"
    return sorted(
        path[len(prefix) : -len(".json")]
        for path in await ls(item_id, prefix)
        if is_workspace_workflow_path(path)
    )


async def offered_workflow_ids(
    ls: ListFiles, item_id: str, *, slug: str, profile: str
) -> list[str]:
    """Every workflow id this item may start: the profile's plus the item's own.
    Empty when the item belongs to no app."""
    if not slug:
        return []
    package = [w.id for w in profile_workflows(slug, profile)]
    own = await workspace_workflow_ids(ls, item_id)
    return sorted(set(package) | set(own))


async def resolve_offered_workflow(
    files: WorkspaceFiles, item_id: str, *, slug: str, profile: str, workflow_id: str
) -> WorkflowManifest | None:
    """The manifest of ONE offered workflow, or None when the item offers no such
    id. Workspace first — it shadows a package workflow of the same id, which is
    the order the orchestrator runs them in.

    An EMPTY id keeps its old meaning: the profile's default workflow (its
    legacy singular, else the first declared), so a single-workflow profile still
    runs without naming it. A workspace workflow always has a name."""
    if not slug:
        return None
    if not workflow_id:
        return load_profile_workflow(slug, profile, "")
    # `load_workspace_workflow` itself refuses the reserved id, so a workflow
    # body written by hand to `.workflows/schedules.json` is no workflow here,
    # in the orchestrator, or anywhere else that loads one.
    own = await load_workspace_workflow(files, item_id, workflow_id)
    if own is not None:
        return own[1]
    return load_profile_workflow(slug, profile, workflow_id)


def no_such_workflow(workflow_id: str, offered: Collection[str]) -> str:
    """The one sentence for "this item has no workflow by that id" — the page's
    error panel, the agent's refusal and the sweep's log line all say it the
    same way, and all name what the item DOES have, because "which one, and why
    not" is all a reader can act on. No tool name: the page's reader is a person
    pressing a button, not the agent."""
    has = f" (it has: {', '.join(sorted(offered))})" if offered else " (it has none yet)"
    return f"This item has no workflow named {workflow_id!r}{has}."
