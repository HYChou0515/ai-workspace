"""#589 — what a baked-in skill actually ships.

A skill is a FOLDER whose only required member is ``SKILL.md``; the industry
format lets it carry ``references/``, ``scripts/`` and data files alongside.
Every reader in this package hardcoded the ``SKILL.md`` filename, so those
siblings were silently dropped — committing one was a no-op with no error, no
log and no validation.

This module is the pure half of fixing that: it turns a source skill directory
(``sample-skills/<name>/`` or ``apps/<slug>/profiles/<p>/.skill/<name>/``) into
the bytes to materialize. Nothing here touches a FileStore or a sandbox.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, Literal

from msgspec import Struct

# Build artefacts, not skill content. Deliberately TINY: `seeding._SKIP` also
# drops `run.py` / `__init__.py`, but that list describes PROFILE-level noise
# (a workflow's host-side orchestration code, package markers). Inside a skill
# folder those are ordinary scripts, and dropping one would recreate the exact
# silent-loss this module exists to fix — so the profile list is NOT reused.
_EXCLUDED_DIRS = {"__pycache__"}
_EXCLUDED_SUFFIXES = (".pyc",)

#: Bookkeeping written NEXT TO a materialized copy — never part of a payload.
#: A source folder should not have one, but a materialized copy does, and a copy
#: can become a source (download a skill, hand it to a dev, commit it under
#: ``sample-skills/``). Carrying it would nest one generation's manifest inside
#: the next and make "was this file edited locally?" answer about the wrong file.
ORIGIN_FILE = ".origin"


def _is_noise(rel: PurePosixPath) -> bool:
    if rel.as_posix() == ORIGIN_FILE:
        return True
    return bool(_EXCLUDED_DIRS.intersection(rel.parts)) or rel.name.endswith(_EXCLUDED_SUFFIXES)


def skill_payload(source_dir: Any) -> dict[str, bytes]:
    """Every file a skill ships, keyed by its POSIX path relative to the skill
    folder. Sub-folders are kept — ``SKILL.md`` refers to its siblings by
    relative path (``see references/glossary.md``), so the shape has to survive
    the copy or the body's own instructions stop resolving."""
    out: dict[str, bytes] = {}
    _walk(source_dir, PurePosixPath(), out)
    return dict(sorted(out.items()))


def _walk(node: Any, prefix: PurePosixPath, out: dict[str, bytes]) -> None:
    """Recursive ``iterdir`` rather than ``rglob``: a profile skill is reached
    through ``importlib.resources``, whose Traversable has no ``rglob`` and no
    ``relative_to``. Both sources have to walk the same way or only one of them
    would ever ship its files."""
    for child in node.iterdir():
        here = prefix / child.name
        if _is_noise(here):
            continue
        if child.is_dir():
            _walk(child, here, out)
        else:
            out[here.as_posix()] = child.read_bytes()


SkillSource = Literal["shared", "profile"]


class SkillOrigin(Struct):
    """What a materialized skill copy remembers about where it came from.

    Written to ``.skill/<name>/.origin`` beside the copy — deliberately IN the
    folder rather than on the WorkItem: it is born and dies with the files it
    describes, and it needs no schema migration or backfill to exist.

    ``files`` maps each shipped file to the digest of the bytes we shipped. That
    single map answers both later questions: a newer version exists when it
    differs from the current source's map, and a file was edited locally when the
    workspace copy no longer digests to its recorded entry. There is deliberately
    no whole-folder digest alongside it — a second field could only ever disagree
    with this one.
    """

    source: SkillSource
    files: dict[str, str]


def origin_for(source: SkillSource, payload: Mapping[str, bytes]) -> SkillOrigin:
    """The manifest for a payload. Digests the bytes we actually ship — not the
    source directory — so an excluded artefact can never move the answer."""
    return SkillOrigin(
        source=source,
        files={rel: hashlib.sha256(data).hexdigest() for rel, data in payload.items()},
    )
