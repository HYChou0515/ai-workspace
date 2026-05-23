"""Starter file templates seeded into a new investigation on create.

Templates are organised into **profiles** — one subfolder per profile
under this package (e.g. `default/`, `smt-reflow-example/`). To add a
profile, drop a new folder of files here (with an `__init__.py` marker)
and redeploy; it shows up in the New Investigation picker automatically.

File-naming convention inside a profile:
  - `*.tpl`  → run through `string.Template(text).substitute(**case)`,
               then the `.tpl` suffix is stripped from the seeded path
               (so `brief.md.tpl` lands as `/brief.md`). Placeholders use
               `$name` / `${name}` and MUST all exist in `case` — a
               typo raises rather than silently emitting `$foo`.
  - anything else → copied byte-for-byte (notebooks, canvas, CSV…).

Substitution variables (`case`): title, owner, severity, status,
product, description, members, topics.
"""

from __future__ import annotations

from importlib import resources
from pathlib import PurePosixPath
from string import Template

from ...filestore.protocol import FileStore
from ...resources import Investigation

_TEMPLATES_PKG = "workspace_app.rca.templates"
_TPL_SUFFIX = ".tpl"
_NON_PROFILE = {"__pycache__"}


def list_profiles() -> list[str]:
    """Available template profile names, sorted. Each is a subfolder
    of this package holding one set of starter files."""
    root = resources.files(_TEMPLATES_PKG)
    names = [
        child.name for child in root.iterdir() if child.is_dir() and child.name not in _NON_PROFILE
    ]
    return sorted(names)


async def seed_investigation(
    filestore: FileStore,
    investigation_id: str,
    inv: Investigation,
    profile: str = "default",
) -> list[str]:
    """Copy a template profile's files into the investigation's FileStore.

    `.tpl` files are substituted with the investigation's fields and lose
    the suffix; everything else is copied verbatim. Returns the sorted
    list of seeded paths.
    """
    if profile not in list_profiles():
        raise ValueError(f"unknown template profile: {profile!r}")
    case = _case_info(inv)
    # Navigate into the profile via the Traversable API rather than
    # importing it as a subpackage — profile folder names may contain
    # hyphens (e.g. "smt-reflow-example") which aren't valid module names.
    root = resources.files(_TEMPLATES_PKG) / profile
    written: list[str] = []
    for path in _walk(root):
        rel = path.as_posix()
        raw = (root / rel).read_bytes()
        if rel.endswith(_TPL_SUFFIX):
            text = Template(raw.decode("utf-8")).substitute(case)
            dest = "/" + rel[: -len(_TPL_SUFFIX)]
            await filestore.write(investigation_id, dest, text.encode("utf-8"))
        else:
            dest = "/" + rel
            await filestore.write(investigation_id, dest, raw)
        written.append(dest)
    return sorted(written)


def _case_info(inv: Investigation) -> dict[str, str]:
    return {
        "title": inv.title,
        "owner": inv.owner,
        "severity": inv.severity.value,
        "status": inv.status.value,
        "product": inv.product or "—",
        "description": inv.description or "",
        "members": ", ".join(inv.members),
        "topics": ", ".join(inv.topics),
    }


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
