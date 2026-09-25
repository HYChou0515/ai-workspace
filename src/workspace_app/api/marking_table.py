"""Save a marking as a table (#847/#848, plan-view-plugins-pr5-finish P7).

"Save as table" on a marking's header control, or on its chip in the chat,
writes the rows the marking lights — every column — as a new CSV in the
workspace, at ``/markings/<name>-<yyyymmdd-hhmm>.csv``:

1. the rows are selected in the item's sandbox by the chart plugin's
   ``lit_rows`` command, over the view the action was taken in (its source,
   its transforms, then the platform's one lighting rule — the SPA's
   ``isLit``). It runs through the same runner the chart's ``query`` takes
   (`view_plugin_routes.RunPluginCommand`);
2. the CSV is written through the file facade, so the workspace quota applies
   exactly as to any other write, and the caller must hold ``add_content``.

The header control sends the marking's values (it holds them); the chip sends
none, and the sandbox reads the file the send wrote (``.markings/<name>.json``).

Every refusal is a sentence the control shows as it is: no internals, no
sandbox paths.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from ..files import WorkspaceFiles, WorkspaceFull
from ..quota.disk_ledger import UserDiskFull
from ..sandbox.protocol import SandboxBusy, SandboxNotFound
from .markings import MARKINGS_DIR, _name_problem
from .view_plugin_routes import RunPluginCommand

logger = logging.getLogger(__name__)

#: The plugin whose sandbox half selects the rows: it is the one that reads a
#: view's `source:` and applies its `transform:`.
PLUGIN, COMMAND = "chart", "lit_rows"
TABLES_DIR = "/markings"
_STAMP = re.compile(r"^\d{8}-\d{4}$")
#: A file name holds 255 bytes.
_NAME_BYTES = 255
#: Saving twice in one minute makes `-2`, `-3`, …; past this many, something
#: is saving in a loop, and the answer is a refusal rather than a search.
MAX_SUFFIX = 99

UNREACHABLE = "the workspace could not be reached — try again"


class SaveTableBody(BaseModel):
    name: str
    #: The view the rows come from — a view file, or a table file itself.
    view: str
    #: The marking's values (the header control holds them); None → read the
    #: marking the chat send wrote, `.markings/<name>.json` (the chip).
    columns: dict[str, list[str]] | None = None
    #: `yyyymmdd-hhmm` in the saver's own clock: the name they will look for.
    stamp: str


class SaveTableOut(BaseModel):
    path: str
    rows: int


def _answer(stdout: str) -> tuple[int, str] | None:
    """`lit_rows`'s `{"rows", "csv"}`, or None when it is not that."""
    try:
        answer = json.loads(stdout)
        rows, csv = answer["rows"], answer["csv"]
    except (ValueError, TypeError, KeyError):
        return None
    if type(rows) is not int or not isinstance(csv, str):
        return None
    return rows, csv


async def _free_path(files: WorkspaceFiles, item_id: str, base: str) -> str:
    for n in range(1, MAX_SUFFIX + 1):
        path = f"{TABLES_DIR}/{base}{'' if n == 1 else f'-{n}'}.csv"
        if not await files.exists(item_id, path):
            return path
    raise HTTPException(
        status_code=409, detail="too many tables were saved from this marking this minute"
    )


def register_marking_table_route(
    app: FastAPI | APIRouter,
    *,
    locator: Any,
    files: WorkspaceFiles,
    run_plugin: RunPluginCommand,
) -> None:
    @app.post("/a/{slug}/items/{item_id}/markings/table")
    async def save_marking_table(slug: str, item_id: str, body: SaveTableBody) -> SaveTableOut:
        # Selecting the rows reads the item; saving them adds a file.
        iid = locator.require_access(slug, item_id, "read_content")
        try:
            locator.require_access(slug, item_id, "add_content")
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
            raise HTTPException(
                status_code=403, detail="you may not add files in this workspace"
            ) from None
        if (problem := _name_problem(body.name)) is not None:
            raise HTTPException(status_code=422, detail=problem)
        if not _STAMP.fullmatch(body.stamp):
            raise HTTPException(status_code=422, detail="the time stamp must be yyyymmdd-hhmm")
        base = f"{body.name}-{body.stamp}"
        # `-NN.csv` at most: the name that lands must fit a file name.
        if len(f"{base}-{MAX_SUFFIX}.csv".encode()) > _NAME_BYTES:
            raise HTTPException(status_code=422, detail="the name is too long for a file name")
        if not body.view.strip():
            raise HTTPException(
                status_code=422, detail="this marking has no view to take its rows from"
            )
        args: dict[str, Any] = {"view": body.view}
        if body.columns is None:
            args["marking"] = f"{MARKINGS_DIR}/{body.name}.json"
        else:
            args["columns"] = body.columns
        try:
            result = await run_plugin(iid, PLUGIN, COMMAND, args)
        except HTTPException as exc:
            if exc.status_code == 413:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "this marking holds too many values to save from here — "
                        "send it in the chat and save it from its chip"
                    ),
                ) from None
            if exc.status_code == 404:
                raise HTTPException(
                    status_code=501,
                    detail="saving a marking as a table is not available in this deployment",
                ) from None
            raise
        except (SandboxNotFound, SandboxBusy):
            raise HTTPException(status_code=503, detail=UNREACHABLE) from None
        if result.exit_code == 2:
            raise HTTPException(status_code=422, detail=result.stderr.strip())
        answer = _answer(result.stdout) if result.exit_code == 0 else None
        if answer is None:
            logger.warning(
                "save marking table: item %s, %s exited %s: %s",
                iid,
                COMMAND,
                result.exit_code,
                result.stderr.strip()[:2000],
            )
            raise HTTPException(status_code=502, detail="the rows could not be selected")
        rows, csv = answer
        try:
            path = await _free_path(files, iid, base)
            await files.write(iid, path, csv.encode())
        except (WorkspaceFull, UserDiskFull) as exc:
            raise HTTPException(status_code=507, detail=str(exc)) from None
        except (SandboxNotFound, SandboxBusy):
            raise HTTPException(status_code=503, detail=UNREACHABLE) from None
        except OSError as exc:  # `markings` a file, say: the disk said no
            raise HTTPException(
                status_code=409,
                detail=f"the table could not be saved: {exc.strerror or 'the disk refused it'}",
            ) from None
        return SaveTableOut(path=path, rows=rows)
