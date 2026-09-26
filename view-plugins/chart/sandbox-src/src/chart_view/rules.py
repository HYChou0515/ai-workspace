"""The rules the schema cannot state: each compares one part of a chart spec
with another, which JSON Schema has no way to say.

The renderer holds the same rules in `web/src/rules.ts`, word for word; the
spec corpus (`spec-corpus/`) and the message tests on both sides hold the two
to one verdict. Run only over a document the schema already accepted, so the
shapes here are trusted.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any, NamedTuple

_CHANNELS = ("x", "y", "x2", "y2", "color", "size", "theta", "text")

_NUMBER = re.compile(r"0[xXoObB][0-9a-fA-F_]+|\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d[\d_]*)?[jJ]?")
_NAME = re.compile(r"[^\W\d]\w*")
# Python's words, and pandas' own name for the row index (read as a column
# only when a column has that name; a field is never needed for it)
_NOT_COLUMNS = {"and", "or", "not", "in", "is", "True", "False", "None", "index"}
_TEXT_PREFIX = re.compile(r"[rRbBuUfF]{1,2}")


def where_names(expr: str) -> list[str]:
    """The columns a `where:` expression reads, as pandas reads them: each
    name that is not a Python word, an attribute (`.isin`), a function called
    (`abs(`), a local (`@limit`) or the prefix of a text (`r'...'`); inside
    backticks, the text between them. Texts in quotes are skipped. Held to
    pandas itself by `wire-corpus/where-names.json`."""
    names: list[str] = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch in "'\"":
            i += 1
            while i < n and expr[i] != ch:
                i += 2 if expr[i] == "\\" else 1
            i += 1
        elif ch == "`":
            end = expr.find("`", i + 1)
            if end < 0:
                break
            names.append(expr[i + 1 : end])
            i = end + 1
        elif ch.isdigit():  # (a leading dot is skipped, then its digits read)
            number = _NUMBER.match(expr, i)
            assert number is not None  # a digit starts one
            i = number.end()
        elif name := _NAME.match(expr, i):
            word, i = name.group(), name.end()
            before = expr[: name.start()].rstrip()[-1:]
            after = expr[i:].lstrip()[:1]
            if (
                word in _NOT_COLUMNS
                or before in (".", "@")
                or after == "("
                or (expr[i : i + 1] in ("'", '"') and _TEXT_PREFIX.fullmatch(word))
            ):
                continue
            names.append(word)
        else:
            i += 1
    return list(dict.fromkeys(names))


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


def _one_op(path: str, layer: Mapping[str, Any]) -> list[str]:
    """#847/#848 PR 5 P41 row 26: a layer aggregates a field once (`query`'s
    `_measures`: the first channel naming it decides), so a second op on the
    same field would show the first's value under its own label. A stack's
    value channel carries the op its segments are (P42 row 31): its own
    `aggregate`, else sum."""
    encoding = layer.get("encoding", {})
    stack = stack_parts(layer)
    first: dict[str, tuple[str, str]] = {}
    lines = []
    for channel, d in _fields(encoding):
        op = stack.op if stack and channel == stack.channel else d.get("aggregate")
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


class StackParts(NamedTuple):
    links: list[str]  # its slot and colour fields: what it links by
    value: str  # its value field
    channel: str  # the channel naming it, x or y
    op: str  # what each segment is of its rows: the value's aggregate, else sum


def stack_parts(layer: Mapping[str, Any]) -> StackParts | None:
    """A stacked layer's parts; None for a layer that is not a stack -- a
    bar or an area with `stack: true`, as `query.stacked` reads it. The slot
    is y when y is a category (a horizontal bar), else x; a colour by value
    is refused by the schema."""
    mark = layer.get("mark")
    if not (
        isinstance(mark, Mapping)
        and mark.get("type") in ("bar", "area")
        and mark.get("stack") is True
    ):
        return None
    encoding = layer["encoding"]
    horizontal = encoding["y"]["type"] in ("nominal", "ordinal")
    slot, value = ("y", "x") if horizontal else ("x", "y")
    colour = encoding.get("color", {}).get("field")
    links = [encoding[slot]["field"], *([colour] if colour is not None else [])]
    op = encoding[value].get("aggregate", "sum")
    return StackParts(list(dict.fromkeys(links)), encoding[value]["field"], value, op)


def _quoted(names: list[str]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _stack_links(doc: Mapping[str, Any], path: str, layer: Mapping[str, Any]) -> list[str]:
    """#847/#848 PR 5 P41 row 21 [user, 2026-09-26]: a stack links by its
    slot and colour only. A segment is the sum of its rows (P40 row 18), so
    it has no single value of any other field: a `keys:` naming one wrote
    nothing a linked view could light, and a `highlight:` reading one lit
    nothing. A highlight may also test the value, which is each segment's
    sum."""
    parts = stack_parts(layer)
    if parts is None:
        return []
    links, value = parts.links, parts.value
    subject = f"a stack ({path.rstrip('.')})" if path else "a stack"
    head = (
        f"{subject} links by its slot and colour only ({_quoted(links)}) — a segment is the"
        " sum of its rows, so it has no single"
    )
    lines = []
    keys = [k for k in doc.get("keys", []) if k not in links]
    if keys:
        lines.append(
            f"keys: {head} {_quoted(keys)}: key the view by its slot and colour, or drop"
            " stack so single rows link"
        )
    highlight = doc.get("highlight", {})
    for how, read in (
        ("where", lambda: where_names(highlight["where"])),
        ("values", lambda: list(highlight["values"])),
    ):
        other = [c for c in read() if c not in (*links, value)] if how in highlight else []
        if other:
            lines.append(
                f"highlight.{how}: {head} {_quoted(other)}: test its slot and colour, or its"
                f" value '{value}' (each segment's sum), or drop stack so single rows light"
            )
    return lines


def rule_errors(doc: Mapping[str, Any]) -> list[str]:
    """Every way a schema-valid `doc` breaks these rules, one line each."""
    lines: list[str] = []
    for path, layer in _layers(doc):
        lines += _one_op(path, layer)
        lines += _stack_links(doc, path, layer)
    return lines
