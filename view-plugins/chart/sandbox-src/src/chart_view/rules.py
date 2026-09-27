"""The rules the schema cannot state: each compares one part of a chart spec
with another, which JSON Schema has no way to say.

The renderer holds the same rules in `web/src/rules.ts`, word for word; the
spec corpus (`spec-corpus/`) and the message tests on both sides hold the two
to one verdict. Run only over a document the schema already accepted, so the
shapes here are trusted.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Mapping
from typing import Any, NamedTuple

_CHANNELS = ("x", "y", "x2", "y2", "color", "size", "theta", "text")

_NUMBER = re.compile(
    r"0[xXoObB][0-9a-fA-F_]+|[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][+-]?[0-9][0-9_]*)?[jJ]?"
)
_ASCII_WORD = re.compile(r"[A-Za-z0-9_]*")
# Python's words, pandas' own name for the row index (read as a column only
# when a column has that name; a field is never needed for it) and the
# globals `df.eval` resolves when no column has the name (P42 row 30)
_NOT_COLUMNS = {"and", "or", "not", "in", "is", "True", "False", "None", "index", "inf", "Inf"}
_TEXT_PREFIX = re.compile(r"[rRbBuUfF]{1,2}")


def _name_end(expr: str, i: int) -> int:
    """Where the name at `i` ends: Python's identifier rule (its characters
    XID_Continue, so a combining mark continues it), as `rules.ts` reads it
    with `\\p{XID_Continue}`."""
    n = len(expr)
    while True:
        word = _ASCII_WORD.match(expr, i)
        assert word is not None  # `*` matches the empty string
        i = word.end()
        if i < n and ("_" + expr[i]).isidentifier():
            i += 1
        else:
            return i


def _text_end(expr: str, i: int) -> int:
    """Where the text opening at `i` ends (after its closing quote, or at the
    end of `expr`): a triple-quoted text runs to the same three quotes."""
    quote = expr[i] * 3 if expr.startswith(expr[i] * 3, i) else expr[i]
    i, n = i + len(quote), len(expr)
    while i < n and not expr.startswith(quote, i):
        i += 2 if expr[i] == "\\" else 1
    return i + len(quote)


def where_names(expr: str) -> list[str]:
    """The columns a `where:` expression reads, as pandas reads them: each
    name that is not a Python word, one of pandas' globals (`inf`), an
    attribute (`.isin`), a function called (`abs(`), a keyword argument
    (`case=`), a local (`@limit`) or the prefix of a text (`r'...'`); inside
    backticks, the text between them. Every name is judged in its NFKC form,
    as Python reads a name (`ｉｎｆ` is `inf`). Texts in quotes, triple quotes
    too, are skipped, and a comment (`#`) ends the expression. One pass, looking
    around each name by index, so it takes time in proportion to `expr`.
    Held to pandas itself by `wire-corpus/where-names.json`."""
    names: list[str] = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch in "'\"":
            i = _text_end(expr, i)
        elif ch == "#":  # a comment ends the expression
            break
        elif ch == "`":
            end = expr.find("`", i + 1)
            if end < 0:
                break
            names.append(unicodedata.normalize("NFKC", expr[i + 1 : end]))
            i = end + 1
        elif "0" <= ch <= "9":  # (a leading dot is skipped, then its digits read)
            number = _NUMBER.match(expr, i)
            assert number is not None  # a digit starts one
            i = number.end()
        elif ch.isidentifier():  # (a letter or `_`)
            start, i = i, _name_end(expr, i + 1)
            word = unicodedata.normalize("NFKC", expr[start:i])
            b = start - 1
            while b >= 0 and expr[b].isspace():
                b -= 1
            a = i
            while a < n and expr[a].isspace():
                a += 1
            after = expr[a : a + 2]
            if (
                word in _NOT_COLUMNS
                or (b >= 0 and expr[b] in ".@")
                or after[:1] == "("
                or (after[:1] == "=" and after != "==")
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


class Unlinked(NamedTuple):
    """What a spec's `keys:` / `highlight:` name that a stack does not link by."""

    keys: list[str]
    highlight: dict[str, list[str]]  # "where" / "values" -> the fields each reads

    def lights(self) -> bool:
        """Whether the highlight tests only what the stack links by (or its value)."""
        return not any(self.highlight.values())


def unlinked(doc: Mapping[str, Any], parts: StackParts) -> Unlinked:
    """The fields `doc`'s `keys:` and `highlight:` name that a stack with
    `parts` does not link by: any but its slot and colour, and for a
    highlight, its value (each segment's sum, or mean...)."""
    keys = [k for k in doc.get("keys", []) if k not in parts.links]
    highlight = doc.get("highlight", {})
    reads = {
        "where": lambda: where_names(highlight["where"]),
        "values": lambda: list(highlight["values"]),
    }
    other = {
        how: [c for c in read() if c not in (*parts.links, parts.value)]
        for how, read in reads.items()
        if how in highlight
    }
    return Unlinked(keys, other)


def sum_note(parts: StackParts) -> str:
    """What a chart says of a stack beside a layer that links by more
    (#847/#848 PR 5 P42 row 29) -- the renderer's `sumNote`, word for word."""
    return f"the stack links by {', '.join(parts.links)} only (each a {parts.op})"


def _carried(doc: Mapping[str, Any], field: str, highlight: bool) -> bool:
    """Whether some layer of `doc` may link by `field`: one that is not a
    stack (its rows may have any field -- `validate` reads the data), or a
    stack whose slot or colour it is (for a highlight, its value too)."""
    for _, layer in _layers(doc):
        parts = stack_parts(layer)
        if parts is None or field in parts.links or (highlight and field == parts.value):
            return True
    return False


def _stack_links(doc: Mapping[str, Any], path: str, layer: Mapping[str, Any]) -> list[str]:
    """#847/#848 PR 5 P41 row 21 [user, 2026-09-26]: a stack links by its
    slot and colour only. A segment is the sum of its rows (P40 row 18) --
    or the value's own aggregate of them (the words name which: P42 row 33)
    -- so it has no single value of any other field: a `keys:` naming one
    wrote nothing a linked view could light, and a `highlight:` reading one
    lit nothing. A highlight may also test the value, which is each
    segment's sum (or mean...).

    P42 row 29 [user, 2026-09-26]: the rule limits the stack layer only. A
    layer that is not stacked links by any field its rows have, so these
    are refused only when no layer may link by them (every layer a stack,
    none by that slot or colour). Beside a layer that may, the stack
    neither writes nor lights by such a field (`query`, `selection.ts`),
    `validate` refuses a key no layer's rows hold and a highlight no layer
    can see (it reads the data), and the chart says what the stack links by
    (`sum_note`)."""
    parts = stack_parts(layer)
    if parts is None:
        return []
    links, value, op = parts.links, parts.value, parts.op
    subject = f"a stack ({path.rstrip('.')})" if path else "a stack"
    head = (
        f"{subject} links by its slot and colour only ({_quoted(links)}) — a segment is the"
        f" {op} of its rows, so it has no single"
    )
    lines = []
    other = unlinked(doc, parts)
    keys = [k for k in other.keys if not _carried(doc, k, highlight=False)]
    if keys:
        lines.append(
            f"keys: {head} {_quoted(keys)}: key the view by its slot and colour, or"
            " drop stack so single rows link"
        )
    for how, read in other.highlight.items():
        fields = [f for f in read if not _carried(doc, f, highlight=True)]
        if fields:
            lines.append(
                f"highlight.{how}: {head} {_quoted(fields)}: test its slot and colour, or its"
                f" value '{value}' (each segment's {op}), or drop stack so single rows light"
            )
    return lines


def rule_errors(doc: Mapping[str, Any]) -> list[str]:
    """Every way a schema-valid `doc` breaks these rules, one line each."""
    lines: list[str] = []
    for path, layer in _layers(doc):
        lines += _one_op(path, layer)
        lines += _stack_links(doc, path, layer)
    return lines
