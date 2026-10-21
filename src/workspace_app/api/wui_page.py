"""What a WUI page is, as the server decides it — one definition for the routes
that must not trust the browser's word for it.

Deploy records a page; a WUI User's write is admitted on a page's behalf
(docs/plan-wui-user.md D12). Both ask the same two questions of the same file:
is this a page's view file, and which folder does it own.
"""

from __future__ import annotations

from typing import Any

import yaml

from ..files import WorkspaceFiles
from ..filestore.protocol import FileNotFound
from ..sync.ignore import DEFAULT_IGNORES, should_ignore

#: the view-file suffix every page's declaration carries
VIEW_SUFFIX = ".ai.yaml"


class NotAPage(Exception):
    """``path`` is not a page's view file; the message says why, for a 400/403."""


async def load_wui_view(files: WorkspaceFiles, workspace_id: str, path: str) -> dict[str, Any]:
    """The view file at workspace-absolute ``path``, parsed — or ``NotAPage``.

    Read from the item itself, so the answer is the server's: a body or a header
    the browser composed only names a path to check."""
    if not path.endswith(VIEW_SUFFIX):
        raise NotAPage(f"not a view file: {path}")
    if should_ignore(path, DEFAULT_IGNORES):
        # A vendored or unpacked copy under `node_modules/` is not a page
        # anybody made — the schedule index refuses these for the same
        # reason, and shares the list rather than re-spelling it.
        raise NotAPage(f"not a page of this item: {path}")
    try:
        raw = await files.read(workspace_id, path)
    except FileNotFound:
        raise NotAPage(f"view file not found: {path}") from None
    try:
        loaded = yaml.safe_load(raw.decode("utf-8", errors="replace"))
    except (yaml.YAMLError, RecursionError) as exc:
        # `RecursionError`: a document nested past the parser's depth
        # (`view: [[[[…`) is not valid yaml from where this stands, and a 500
        # would blame the platform for a file the caller wrote.
        raise NotAPage(f"view file is not valid yaml: {exc}") from None
    # A view file is a mapping; anything else has no `view:` to read.
    doc: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
    kind = doc.get("view")
    if kind != "wui":
        found = kind if isinstance(kind, str) else "none"
        raise NotAPage(f"not a WUI: {path} has view {found!r}")
    return doc


def page_folder(view_path: str) -> str:
    """The folder a page owns: its view file's directory, ``""`` at the root —
    `wuiFolder` in the browser (`web/src/renderers/wui/paths.ts`)."""
    cut = view_path.rfind("/")
    return "" if cut <= 0 else view_path[:cut]


def page_may_write(view_path: str, path: str) -> bool:
    """May the page declared at ``view_path`` write workspace-absolute ``path``?

    Strictly inside its folder — the folder itself is not a file to write — and
    never its own view file, which declares the workflows, tools and agents the
    page may call (D12). A page at the root owns nothing: "only its own folder"
    is then nothing, not everything (`resolveWritePath` in the browser)."""
    folder = page_folder(view_path)
    if folder == "" or path == view_path:
        return False
    return path.startswith(folder + "/")
