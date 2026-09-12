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

A THIRD consumer reads a DERIVED list, not this one: `TREE_PRUNE` (below) is
what the file tree lists without walking into — the directory patterns here
plus the build outputs the mirror does keep. Widening `TREE_PRUNE` changes
only what preloads; widening `DEFAULT_IGNORES` changes what is backed up AND
what is scheduled AND what preloads. Put a pattern where its consequences are
the ones intended.

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

# The file tree's "do not PRELOAD" list — a third meaning, and deliberately a
# separate name: `DEFAULT_IGNORES` decides what is not backed up and what is not
# taken instructions from (above); this decides only which directories the
# tree lists WITHOUT walking into, so they draw as collapsed nodes and load on
# expand. It DERIVES from `DEFAULT_IGNORES` — "what counts as machine-generated"
# is one question, answered once — and adds the build outputs the mirror does
# keep. Only directory patterns belong here: a file cannot be collapsed, so a
# file pattern would not defer it, it would hide it. Widening `DEFAULT_IGNORES`
# is a persistence + scheduling decision; widening this is a UI one.
TREE_PRUNE: list[str] = [p for p in DEFAULT_IGNORES if p.endswith("/")] + ["dist/", "build/"]

# The structural bound on one tree listing. Past this many entries the walk
# stops and reports the directories it did not reach as `unwalked`, so a
# workspace with fifty thousand CSVs answers in bounded time instead of
# hanging — a hang is the one failure nothing reports. A constant, not a
# config knob: no deployment today needs a different value, and a knob is a
# migration note plus an example yaml for a number nobody has asked to tune.
TREE_MAX_ENTRIES = 5000


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
