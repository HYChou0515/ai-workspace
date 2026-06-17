"""Seed a new item's workspace from its profile's starter files (#89 P4b).

Ports the old ``rca.templates.seed_investigation`` to the per-App ``apps/<slug>/
profiles/<name>/`` layout, keyed on the opaque ``item_id`` (FileStore is
domain-agnostic). ``.tpl`` files are ``string.Template`` substituted with the
item's ``case`` and lose the ``.tpl`` suffix; everything else is copied verbatim.
Profile metadata (``_profile.json`` / ``_prompt.md`` / ``.skill/``) is NOT seeded.
"""

from __future__ import annotations

from importlib import resources
from pathlib import PurePosixPath
from string import Template
from typing import Any

import msgspec

from ..filestore.protocol import FileStore

_APPS_PKG = "workspace_app.apps"
_PROFILES_DIR = "profiles"
_TPL_SUFFIX = ".tpl"
# Profile metadata + package noise — never seeded into the workspace. ``run.py``
# is the workflow's orchestration code (#100), not a starter file — it runs on the
# host, never in the item's workspace.
_SKIP = {"__init__.py", "__pycache__", "_profile.json", "_prompt.md", ".skill", "run.py"}


def case_from_item(item: Any) -> dict[str, str]:
    """Flatten a WorkItem Struct into ``$var`` substitution values: scalars
    stringified, lists joined with ``, ``. ``UNSET`` fields are simply absent
    (msgspec omits them), so a profile `.tpl` only references fields its App has."""
    out: dict[str, str] = {}
    for key, val in msgspec.to_builtins(item).items():
        if isinstance(val, list):
            out[key] = ", ".join(str(x) for x in val)
        elif val is None:
            out[key] = ""
        else:
            out[key] = str(val)
    return out


async def seed_item(
    filestore: FileStore, item_id: str, app_slug: str, profile: str, case: dict[str, str]
) -> list[str]:
    """Copy ``apps/<app_slug>/profiles/<profile>/`` into ``item_id``'s FileStore.
    Returns the sorted list of seeded paths."""
    root = resources.files(_APPS_PKG) / app_slug / _PROFILES_DIR / profile
    written: list[str] = []
    for path in _walk(root):
        rel = path.as_posix()
        raw = (root / rel).read_bytes()
        if rel.endswith(_TPL_SUFFIX):
            text = Template(raw.decode("utf-8")).substitute(case)
            dest = "/" + rel[: -len(_TPL_SUFFIX)]
            await filestore.write(item_id, dest, text.encode("utf-8"))
        else:
            dest = "/" + rel
            await filestore.write(item_id, dest, raw)
        written.append(dest)
    return sorted(written)


def _walk(node, prefix: PurePosixPath | None = None) -> list[PurePosixPath]:
    prefix = prefix or PurePosixPath()
    out: list[PurePosixPath] = []
    for child in node.iterdir():
        name = child.name
        if name in _SKIP or name.endswith(".pyc"):
            continue
        here = prefix / name
        if child.is_dir():
            out.extend(_walk(child, here))
        else:
            out.append(here)
    return out
