"""Ignore rules for reverse-syncing sandbox files into the FileStore.

The defaults cover the standard noise (build/cache directories, compiled
artifacts) plus a per-file size cap so a stray model dump doesn't blow
up specstar. Per-workspace customization can override DEFAULT_IGNORES
when constructing SandboxSync.

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

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB; spilling specstar with one big blob is rarely intentional


def should_ignore(path: str, patterns: list[str], size: int = 0) -> bool:
    if size > MAX_FILE_SIZE:
        return True
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
