"""Starter file templates seeded into a new investigation on create.

Lands the user on the design's 6-tab editor with skeleton content;
the agent fills in details as the investigation progresses.
"""

from __future__ import annotations

from importlib import resources
from pathlib import PurePosixPath

from ...filestore.protocol import FileStore
from ...resources import Investigation

_DEFAULT_PKG = "workspace_app.rca.templates.default"


async def seed_investigation(
    filestore: FileStore, investigation_id: str, inv: Investigation
) -> list[str]:
    """Copy the default template files into the investigation's FileStore.

    Returns the (sorted) list of paths written. Markdown templates get
    `{title}` / `{owner}` / `{severity}` / `{status}` / `{product}` /
    `{description}` substitution; other files are copied byte-for-byte.
    """
    written: list[str] = []
    root = resources.files(_DEFAULT_PKG)
    for path in _walk(root):
        rel = "/" + path.as_posix()
        raw = (root / path.as_posix()).read_bytes()
        data = _substitute(raw.decode("utf-8"), inv).encode("utf-8") if rel.endswith(".md") else raw
        await filestore.write(investigation_id, rel, data)
        written.append(rel)
    return sorted(written)


def _walk(node, prefix: PurePosixPath | None = None) -> list[PurePosixPath]:
    """Recursively list relative paths to regular files under a
    Traversable resource, skipping Python package noise."""
    prefix = prefix or PurePosixPath()
    out: list[PurePosixPath] = []
    for child in node.iterdir():
        name = child.name
        if name in ("__init__.py", "__pycache__") or name.endswith(".pyc"):
            continue
        here = prefix / name
        if child.is_dir():
            out.extend(_walk(child, here))
        else:
            out.append(here)
    return out


def _substitute(text: str, inv: Investigation) -> str:
    return (
        text.replace("{title}", inv.title)
        .replace("{owner}", inv.owner)
        .replace("{severity}", inv.severity.value)
        .replace("{status}", inv.status.value)
        .replace("{product}", inv.product or "—")
        .replace("{description}", inv.description or "")
    )
