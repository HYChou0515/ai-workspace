"""The one channel a tool result uses to declare "render these workspace files in
the chat": a final line ``[shown-files]{json}``.

``show_file`` writes it; the provisioned plotting tools get their stdout
normalised into it (``tooling.registry``). The FE reads that one form
(``web/src/renderers/shownFiles.ts`` — keep the marker in sync).

A marker rather than the whole result being JSON, because a tool result also has
to stay readable to the model and to the tool card — ``_format_exec``'s header
carries the exit code and anchors attribution for small models. A marker rather
than a new event / ``Message`` field, because the declaration then survives a
reload for free: it IS the persisted tool message.

Its own module so the output cap can protect it without importing ``tools``
(which imports the cap).
"""

from __future__ import annotations

import json
from typing import Any, Literal

import magic
from pydantic import BaseModel

from ..files import WorkspaceFiles, abs_path, rel_path
from ..filestore.protocol import FileNotFound

SHOWN_FILES_KEY = "shown_files"
SHOWN_FILES_MARKER = "\n[shown-files]"


def declare_shown_files(
    text: str,
    files: list[dict[str, Any]],
    *,
    layout: dict[str, Any] | None = None,
    caption: str | None = None,
) -> str:
    """`text` with `files` declared for the chat to render. No files ⇒ unchanged.

    A `layout` (#847) declares the arrangement as well: the FE draws ONE card
    that opens it, while `files` still lists every leaf so a consumer that only
    knows the flat list (chat video, chat export) keeps showing each file. Its
    `caption` names the arrangement, so it sits beside the tree, not on a file."""
    if not files:
        return text
    body: dict[str, Any] = {SHOWN_FILES_KEY: files}
    if layout is not None:
        body[LAYOUT_KEY] = layout
        if caption:
            body["caption"] = caption
    payload = json.dumps(body, ensure_ascii=False)
    return f"{text}{SHOWN_FILES_MARKER}{payload}"


LAYOUT_KEY = "layout"


# The `show_file(layout=…)` argument (#847 PR 3 P4): the FE's `PaneNode` shape
# (`web/src/pages/investigation/paneTree.ts`) with workspace paths as leaves.
#
# Four explicit depths rather than one recursive model: `build_tools` inlines
# `$defs` for local chat templates (#613), and a self-referencing model has no
# finite inlining — it would reach the model as a dangling `$ref`. Three levels
# of split is eight panes, more than a screen holds. Every field is present at
# every depth (strict mode lists them all as required), so a node is one shape
# the model fills the same way at any level; which fields a leaf vs a split
# may set is checked in `layout_tree`, which says what is wrong.


class _Pane0(BaseModel):
    """A pane showing one file (the deepest level: no further split)."""

    type: Literal["leaf"]
    path: str


class _Pane1(BaseModel):
    """A pane: `leaf` shows `path`; `split` divides into `a` and `b` along `dir`
    (`row` = side by side, `col` = stacked), `a` taking `ratio` (0–1, default 0.5)."""

    type: Literal["leaf", "split"]
    path: str | None
    dir: Literal["row", "col"] | None
    ratio: float | None
    a: _Pane0 | None
    b: _Pane0 | None


class _Pane2(_Pane1):
    a: _Pane1 | None
    b: _Pane1 | None


class PaneLayout(_Pane1):
    a: _Pane2 | None
    b: _Pane2 | None


class LayoutError(ValueError):
    """A layout the FE could not draw — the message says which node and why."""


def layout_tree(layout: BaseModel) -> dict[str, Any]:
    """The declared tree: leaves `{type, path}` with the path absolute, splits
    `{type, dir, ratio, a, b}` with the ratio defaulted. Raises `LayoutError`.

    A root leaf is refused: one file is `show_file(path)`, and a card that
    "opens a layout" of one pane would re-arrange the user's panes for nothing."""
    seen: set[str] = set()

    def walk(node: Any, where: str) -> dict[str, Any]:
        a, b = getattr(node, "a", None), getattr(node, "b", None)
        dir_, ratio = getattr(node, "dir", None), getattr(node, "ratio", None)
        if node.type == "leaf":
            if not node.path:
                raise LayoutError(f"{where} is a leaf with no path")
            if a is not None or b is not None or dir_ is not None:
                raise LayoutError(f"{where} is a leaf but also sets dir/a/b")
            path = abs_path(node.path)
            if path in seen:
                raise LayoutError(f"{rel_path(path)} appears twice — each file gets one pane")
            seen.add(path)
            return {"type": "leaf", "path": path}
        if node.path:
            raise LayoutError(f"{where} is a split but also sets a path")
        if dir_ is None:
            raise LayoutError(f"{where} is a split with no dir (row or col)")
        if a is None or b is None:
            raise LayoutError(f"{where} is a split missing {'a' if a is None else 'b'}")
        if ratio is not None and not 0 < ratio < 1:
            raise LayoutError(f"{where} has ratio {ratio}; it must be between 0 and 1")
        return {
            "type": "split",
            "dir": dir_,
            "ratio": 0.5 if ratio is None else ratio,
            "a": walk(a, f"{where}.a"),
            "b": walk(b, f"{where}.b"),
        }

    if layout.type == "leaf":  # ty: ignore[unresolved-attribute]
        raise LayoutError("the root is a single file — use show_file(path) for one file")
    return walk(layout, "layout")


def layout_paths(tree: dict[str, Any]) -> list[str]:
    """Leaf paths in visual order — the FE's `layoutPaths`."""
    if tree["type"] == "leaf":
        return [tree["path"]]
    return [*layout_paths(tree["a"]), *layout_paths(tree["b"])]


def split_declaration(text: str) -> tuple[str, str]:
    """`(body, declaration)` — the declaration includes its marker, or is `""`.

    Anything that rewrites a tool result (the output cap) has to put the
    declaration back verbatim: it sits at the very end, which is precisely what a
    head-and-tail truncation eats first, and losing it is silent — no error, no
    card, the user simply never sees the file they were told about.
    """
    at = text.rfind(SHOWN_FILES_MARKER)
    if at < 0:
        return text, ""
    return text[:at], text[at:]


async def describe_for_display(
    files: WorkspaceFiles, workspace_id: str, path: str
) -> dict[str, Any]:
    """One `shown_files` entry for `path`, or `{}` when it doesn't resolve.

    Reads the bytes: the mime has to be sniffed (it decides inline-image vs card,
    and an extension can lie) and a declaration must never name a file that isn't
    there — the FE renders whatever is declared."""
    try:
        data = await files.read(workspace_id, path)
    except FileNotFound:
        return {}
    return {
        # Absolute: the FE's fileUrl/openFile seams take that form, the agent
        # writes relative (#549).
        "path": abs_path(path),
        "mime": magic.from_buffer(data, mime=True),
        "size": len(data),
    }
