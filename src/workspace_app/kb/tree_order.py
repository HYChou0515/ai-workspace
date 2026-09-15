"""The document-tree order (plan-rag-context P2) — ONE rule, stated to users,
shared with the frontend.

    The order is the document tree read top to bottom.

Precisely: at each path level, directories sort before files; among siblings,
maximal ASCII-digit runs compare by numeric value (``9`` < ``10``), everything
else by Unicode code point after lower-casing, with the raw string as the final
tiebreak. CJK numerals and full-width digits are NOT numeric — making them so
needs a growing list of exceptions and the rule stops being explainable; the
user-facing rule is "prefix filenames with Arabic numerals to control order".

Locale-independent by construction: no `locale.strcoll`, no collator. An order
that depends on the machine is not well-defined, and this repo has already
been bitten by CI's Node locale differing from local.

The frontend file tree implements the same rule in TypeScript; both test suites
assert `tests/kb/tree_order.golden.json`, so the two cannot drift silently.
"""

from __future__ import annotations

import re

_RUNS = re.compile(r"[0-9]+|[^0-9]+")


def _name_key(name: str) -> tuple:
    """Natural-sort key for one path segment. Each run becomes a tagged tuple
    so a digit run (tag 0) sorts before a text run (tag 1) at the same
    position, digit runs compare by value, and text runs by lower-cased code
    point. The raw segment is appended as the total-order tiebreak."""
    parts: list[tuple] = []
    for run in _RUNS.findall(name):
        if run[0].isdigit() and run.isascii():
            parts.append((0, int(run), run))
        else:
            parts.append((1, run.lower(), run))
    return (tuple(parts), name)


def tree_sort_key(path: str) -> tuple:
    """Sort key for a document path such that ``sorted(paths, key=tree_sort_key)``
    is the tree read top to bottom. A segment that has segments after it is a
    directory (flag 0) and sorts before a file (flag 1) at the same level."""
    segments = path.split("/")
    last = len(segments) - 1
    return tuple((0 if i < last else 1, _name_key(seg)) for i, seg in enumerate(segments))
