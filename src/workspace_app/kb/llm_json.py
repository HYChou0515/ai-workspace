"""Finding the JSON object in a model's prose.

A model asked for a JSON object rarely returns ONLY a JSON object: it fences it,
prefaces it, appends "hope this helps", and a reasoning model drafts a scratch
one in its thinking first. These two helpers are the string-aware scan the card
drafter grew for that (#494), lifted out of it so the skill hub reviewer reads
its verdict back with the same scanner instead of a third slice.

Not a sweep: the older roles (`answer_formatter`, `quality`, `insight_extractor`,
the graph extractors, `wiki/reflect`, `workflow/steer`, the sanity judge) still
carry their own first-``{``/last-``}`` slices. Each has tests pinning its
current behaviour, and a slice that takes the OUTERMOST braces disagrees with
this scanner on a reply holding two objects — so moving one is a regression
job of its own, not a drive-by.
"""

from __future__ import annotations

import json
from typing import Any


def balanced_objects(text: str) -> list[str]:
    """Every top-level, brace-balanced ``{…}`` substring of ``text``, left to
    right. String-aware: braces inside a double-quoted JSON string (respecting
    ``\\`` escapes) don't move the depth, so a ``}`` in a value can't close the
    object early."""
    out: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escaped = False
    for i, c in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                out.append(text[start : i + 1])
    return out


def try_object(candidate: str) -> dict[str, Any] | None:
    """``json.loads(candidate)`` if it parses, else ``None``. ``candidate`` is a
    brace-balanced ``{…}`` from :func:`balanced_objects`, so a successful parse is
    always a JSON object (never an array/scalar) — narrowed for ty."""
    try:
        obj = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    assert isinstance(obj, dict)  # a balanced {…} always parses to a JSON object
    return obj
