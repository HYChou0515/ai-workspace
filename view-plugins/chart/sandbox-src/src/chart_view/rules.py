"""The rules the schema cannot state: each compares one part of a chart spec
with another, which JSON Schema has no way to say.

The renderer holds the same rules in `web/src/rules.ts`, word for word; the
spec corpus (`spec-corpus/`) and the message tests on both sides hold the two
to one verdict. Run only over a document the schema already accepted, so the
shapes here are trusted.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

_CHANNELS = ("x", "y", "x2", "y2", "color", "size", "theta", "text")


def _layers(doc: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """(the path its lines start with, the layer) for each layer of `doc`."""
    if "layer" in doc:
        for i, layer in enumerate(doc["layer"]):
            yield f"layer[{i}].", layer
    else:
        yield "", doc


def _fields(encoding: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """(channel, definition) for each channel naming a field, in the order
    `query` reads them -- a tooltip list's items as `tooltip[i]`."""
    for c in _CHANNELS:
        if "field" in encoding.get(c, {}):
            yield c, encoding[c]
    tips = encoding.get("tooltip")
    if isinstance(tips, list):
        for i, tip in enumerate(tips):
            yield f"tooltip[{i}]", tip
    elif tips:
        yield "tooltip", tips


def _one_op(path: str, encoding: Mapping[str, Any]) -> list[str]:
    """#847/#848 PR 5 P41 row 26: a layer aggregates a field once (`query`'s
    `_measures`: the first channel naming it decides), so a second op on the
    same field would show the first's value under its own label."""
    first: dict[str, tuple[str, str]] = {}
    lines = []
    for channel, d in _fields(encoding):
        op = d.get("aggregate")
        if not op:
            continue
        field = d["field"]
        if field not in first:
            first[field] = (channel, op)
            continue
        on, was = first[field]
        if op != was:
            lines.append(
                f"{path}encoding.{channel}: '{field}' is aggregated as {was} on {on} — a field "
                f"has one aggregate in a layer, so {op} here would show the {was}: use {was} "
                "here too, or compute both in a transform aggregate, each under its own name (as:)"
            )
    return lines


def rule_errors(doc: Mapping[str, Any]) -> list[str]:
    """Every way a schema-valid `doc` breaks these rules, one line each."""
    lines: list[str] = []
    for path, layer in _layers(doc):
        lines += _one_op(path, layer.get("encoding", {}))
    return lines
