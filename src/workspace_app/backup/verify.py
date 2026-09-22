"""Check that an archive holds the blobs its records point at.

Invariant three of `docs/plan-backup.md`. The run's own exit status cannot carry
this: `ResourceManager.dump` reads each blob inside `try: ... except Exception:
pass` (specstar#450 S2) and yields no statistics, so a blob that fails to read is
dropped and the dump finishes normally. The archive then holds records that
reference bytes it does not contain, and nothing says so until a restore.

**Referential integrity, not counts.** A count comparison cannot work here:
`blob-gc` deletes orphaned blobs on purpose, so a smaller archive is as likely to
be right as wrong, and any threshold that fires on the real failure also fires
after every GC pass — which is how a guard gets loosened until it guards nothing.
A blob a live record points at is by definition not an orphan, so GC cannot
remove it, so its absence is unambiguous.

**A sample, not a sweep.** Collecting every blob id in a 2 TB archive would cost
memory proportional to the blob count. Instead a small sample of live references
is taken first and the archive is streamed once, checking membership against that
fixed-size set.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec
from specstar.resource_manager.dump_format import BlobRecord, DumpStreamReader
from specstar.types import Binary

if TYPE_CHECKING:  # pragma: no cover - typing only
    from specstar import SpecStar

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE = 32


class IncompleteArchive(Exception):
    """An archive is missing blobs its own records reference.

    Raised rather than logged: a run that knows it is short and reports success
    is worse than a run that fails, because the failure is at least visible on
    the day it happens rather than on the day of the restore.
    """


def _binaries(value: Any, depth: int = 0) -> Iterator[Binary]:
    """Every `Binary` reachable from a record's data.

    Generic on purpose. The alternative — a hand-listed set of
    "models with a blob field" — is the same mistake `build_bundle` warns about
    for blob GC: specstar builds a collector for any `list` / `dict` / union
    field whatever its value type, so the real set is nearly every model and a
    hand-written list silently shrinks as models are added.
    """
    if depth > 8:  # pragma: no cover - guards a pathological nesting, not a real shape
        return
    if isinstance(value, Binary):
        yield value
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _binaries(item, depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _binaries(item, depth + 1)
    elif isinstance(value, msgspec.Struct):
        for f in msgspec.structs.fields(value):
            yield from _binaries(getattr(value, f.name, None), depth + 1)


def sample_referenced_blobs(
    spec: SpecStar, query_for: Any, *, limit: int = DEFAULT_SAMPLE
) -> dict[str, str]:
    """`file_id -> the model it was referenced from`, for up to `limit` blobs.

    `query_for` is called with a model name and returns the query bounding this
    archive's window, so the sample only asks for records the archive claims to
    hold. Sampling outside the window would fail an incremental for records it
    was never supposed to carry.
    """
    found: dict[str, str] = {}
    for name in sorted(spec.resource_managers):
        if len(found) >= limit:
            break
        try:
            rows = list(spec.resource_managers[name].list_resources(query_for(name)))
        except Exception:  # pragma: no cover - a model whose backend refuses the query
            logger.warning("backup: could not sample %s for verification", name, exc_info=True)
            continue
        for row in rows:
            for binary in _binaries(getattr(row, "data", None)):
                file_id = getattr(binary, "file_id", None)
                if isinstance(file_id, str) and file_id:
                    found.setdefault(file_id, name)
                    if len(found) >= limit:
                        break
            if len(found) >= limit:
                break
    return found


def blobs_present_in(archives: list[Path], wanted: set[str]) -> set[str]:
    """Which of `wanted` appear as blob records across `archives`.

    Streams each archive one frame at a time and holds only the wanted set, so
    this costs the same on a 2 TB archive as on a 2 MB one.
    """
    seen: set[str] = set()
    for archive in archives:
        if not archive.exists():
            continue
        with archive.open("rb") as fh:
            for record in DumpStreamReader(fh):
                if isinstance(record, BlobRecord) and record.file_id in wanted:
                    seen.add(record.file_id)
                    if seen == wanted:
                        return seen
    return seen


def verify_archives(
    spec: SpecStar, query_for: Any, archives: list[Path], *, limit: int = DEFAULT_SAMPLE
) -> int:
    """Return how many blob references were checked, or raise `IncompleteArchive`.

    The return value matters as much as the exception: a sample of zero passes
    every check ever written, so the caller records the count and a reader can
    tell "verified" from "found nothing to verify".
    """
    wanted = sample_referenced_blobs(spec, query_for, limit=limit)
    if not wanted:
        return 0
    present = blobs_present_in(archives, set(wanted))
    missing = sorted(set(wanted) - present)
    if missing:
        raise IncompleteArchive(
            f"{len(missing)} of {len(wanted)} sampled blob(s) are referenced by live "
            f"records but absent from the archive — e.g. {missing[0]!r} "
            f"(from {wanted[missing[0]]!r}). specstar's dump skips a blob it cannot "
            "read and still exits cleanly (specstar#450 S2), so this run would "
            "otherwise have reported success while holding less than it claims."
        )
    return len(wanted)
