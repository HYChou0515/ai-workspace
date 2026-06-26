"""Issue #254: chunk provenance (page / section / sheet / …) aggregation and
labelling.

A retrieved passage merges several chunks, each carrying the structural
location its parser knew (``DocChunk.provenance``). Here we fold those into a
single passage-level summary and render a compact, agent-facing location label
so the LLM can cite "p.3 §…" instead of an opaque char span.

The aggregation is deliberately GENERIC — it never special-cases a key — so a
new parser locator works with no change here. The FE renders the same
structured summary with its own i18n labels (this module's ``format_location``
is for the LLM-facing tool output only, where an English label is fine).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Display order + label prefix for the known locators. Unknown keys still
# render (prefix = the key itself) so a new parser locator is never silently
# dropped from the agent-facing header.
_LABELS: list[tuple[str, str]] = [
    ("page", "p."),
    ("slide", "slide "),
    ("sheet", "sheet "),
    ("section", ""),
    ("jsonl_line", "line "),
    ("row", "row "),
]


def aggregate_provenance(provenances: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Union the per-chunk provenance dicts (already in seq order) into
    ``{key: [distinct values…]}``. Order-preserving + deduped, so a passage
    spanning pages 3 and 4 yields ``{"page": [3, 4]}`` and a single repeated
    section collapses to one entry. Empty in → empty out (graceful degrade)."""
    agg: dict[str, list[Any]] = {}
    for prov in provenances:
        for key, value in prov.items():
            bucket = agg.setdefault(key, [])
            if value not in bucket:
                bucket.append(value)
    return agg


def _render_values(values: list[Any]) -> str:
    """Contiguous run of ints → ``"3–4"``; otherwise the values joined by ", ".
    Strings (sections, sheet names) pass through verbatim."""
    if len(values) > 1 and all(isinstance(v, int) for v in values):
        lo, hi = min(values), max(values)
        if hi - lo == len(values) - 1:  # a gap-free run
            return f"{lo}–{hi}"
    return ", ".join(str(v) for v in values)


def format_location(provenance: dict[str, Any]) -> str:
    """A compact agent-facing header for an aggregated passage provenance —
    ``"p.3–4 · Failure Analysis > Root Cause"``. Empty provenance → ``""`` so
    callers can omit the parenthetical entirely. Known keys render in a stable
    order; any extra (future-locator) keys follow with the raw key as prefix."""
    if not provenance:
        return ""
    known = {key for key, _ in _LABELS}
    extra = [(k, f"{k} ") for k in provenance if k not in known]
    parts: list[str] = []
    for key, prefix in [*_LABELS, *extra]:
        values = provenance.get(key)
        if not values:
            continue
        parts.append(f"{prefix}{_render_values(list(values))}")
    return " · ".join(parts)
