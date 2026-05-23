"""Text search/replace primitives for the workspace FileStore — the grep
+ sed behind the VSCode-style search panel. Pure functions so they're
unit-testable independent of the HTTP layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch


class InvalidQuery(ValueError):
    """The user's regex didn't compile."""


@dataclass(frozen=True)
class Match:
    line: int  # 1-based
    col: int  # 1-based
    text: str  # the full source line (trimmed for transport)


def compile_query(
    query: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    whole_word: bool = False,
) -> re.Pattern[str]:
    pattern = query if regex else re.escape(query)
    if whole_word:
        pattern = rf"\b(?:{pattern})\b"
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise InvalidQuery(str(exc)) from exc


def path_selected(path: str, include: str, exclude: str) -> bool:
    """Honour comma/whitespace-separated glob lists. `path` is matched
    both as-is and without its leading slash so `*.md` and `data/**`
    both behave intuitively."""
    rel = path.lstrip("/")
    inc = _globs(include)
    exc = _globs(exclude)
    if inc and not any(_glob_match(path, rel, g) for g in inc):
        return False
    return not (exc and any(_glob_match(path, rel, g) for g in exc))


def _globs(spec: str) -> list[str]:
    return [g for g in re.split(r"[,\s]+", spec.strip()) if g]


def _glob_match(path: str, rel: str, glob: str) -> bool:
    g = glob.lstrip("/")
    # support "dir/**" meaning anything under dir/
    if g.endswith("/**"):
        prefix = g[:-3]
        return rel == prefix or rel.startswith(prefix + "/")
    return fnmatch(rel, g) or fnmatch(path, glob)


def search_text(text: str, pattern: re.Pattern[str], *, max_line_len: int = 400) -> list[Match]:
    out: list[Match] = []
    for i, line in enumerate(text.splitlines(), start=1):
        m = pattern.search(line)
        if m:
            out.append(Match(line=i, col=m.start() + 1, text=line[:max_line_len]))
    return out
