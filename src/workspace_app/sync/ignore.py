"""Ignore rules for reverse-syncing sandbox files into the FileStore.

The defaults cover the standard noise (build/cache directories, compiled
artifacts) — regenerable derivatives, never the agent's own data. Per-workspace
customization can override DEFAULT_IGNORES when constructing SandboxSync.

⚠️ THIS LIST HAS A SECOND CONSUMER, and it decides behaviour, not just backups.
`api.schedule_index.is_schedule_file` reuses `DEFAULT_IGNORES` so that a file
the platform declines to back up is not one it takes instructions from, and the
mirror skips ignored paths before reporting a write — so BOTH doors into the
schedule index are closed by a pattern added here. Adding one that covers a
folder users keep pages in therefore switches those pages' schedules off, with
no error anywhere: the page saves, shows its file, and nothing ever runs.

Adding a pattern is a product decision about scheduling, not only about disk.
`tests/api/test_schedule_index.py` pins the ordinary page shapes, so a pattern
that swallows one fails there rather than in somebody's missing report.

There is deliberately NO per-file size cap: the mirror is a COMPLETE backup, so
a big agent-produced file (a model dump, a generated dataset) is persisted like
any other — else it would silently vanish on sandbox reap and under-count in the
usage bar (#374). Streaming to the blob store (#219) keeps a big file off the
heap, so size is not a durability concern.

Pattern conventions:
- `name/` — directory anywhere in the path (matches if the trailing-slash
  segment appears between slashes).
- `*.ext` — suffix match on the file name.
- `name` — any path segment exactly equal to `name`.
"""

from __future__ import annotations

DEFAULT_IGNORES: list[str] = [
    ".venv/",
    "node_modules/",
    "__pycache__/",
    ".git/",
    ".pytest_cache/",
    ".ruff_cache/",
    "*.pyc",
    "*.pyo",
]


def should_ignore(path: str, patterns: list[str]) -> bool:
    segments = [s for s in path.split("/") if s]
    for pat in patterns:
        if pat.endswith("/"):
            if pat[:-1] in segments:
                return True
        elif pat.startswith("*."):
            if path.endswith(pat[1:]):
                return True
        elif pat in segments:
            return True
    return False
