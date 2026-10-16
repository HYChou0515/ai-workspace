"""What an agent step is told about the shape of its answer
(docs/plan-workflow-output-repair.md §3.3).

One module builds both texts the model reads about its format — the instruction
appended to a step's first prompt (D8) and the request a repair turn sends (D3) —
from the step's own declaration, so the two can never describe different shapes.
Model-facing, so English like every other prompt the engine writes.
"""

from __future__ import annotations

import json
from typing import Any

#: the artifact kinds with a machine format a reply can get wrong
STRUCTURED_KINDS = ("json", "yaml", "csv")

_SAMPLE: dict[str, Any] = {
    "str": "text",
    "int": 0,
    "float": 0.0,
    "bool": False,
    "list": [],
    "obj": {},
}


def _type_enum(spec: Any) -> tuple[Any, Any]:
    # the same split as `dsl._spec_type_enum`; kept local so this module does not
    # import the interpreter it is imported by
    if isinstance(spec, dict):
        return spec.get("type"), spec.get("enum")
    return spec, None


def example(outputs: dict[str, Any]) -> str:
    """A JSON object of the declared shape: each field a sample of its type, an enum
    field its first allowed value."""
    sample: dict[str, Any] = {}
    for name, spec in outputs.items():
        tname, enum = _type_enum(spec)
        sample[name] = enum[0] if enum else _SAMPLE.get(tname, "text")
    return json.dumps(sample, ensure_ascii=False)


def _field_lines(outputs: dict[str, Any]) -> list[str]:
    lines = []
    for name, spec in outputs.items():
        tname, enum = _type_enum(spec)
        allowed = (
            f", one of {', '.join(json.dumps(v, ensure_ascii=False) for v in enum)}" if enum else ""
        )
        lines.append(f"- {name}: {tname}{allowed}")
    return lines


def instruction(outputs: dict[str, Any] | None, kind: str) -> str:
    """The format paragraph for a step's prompt: the JSON shape for an ``outputs``
    step, "only the content" for a structured ``out``, nothing for prose."""
    if outputs:
        return "\n".join(
            [
                "Reply with ONLY one JSON object — no introduction, no explanation, "
                "no code fence. Its fields:",
                *_field_lines(outputs),
                "Example:",
                example(outputs),
            ]
        )
    if kind in STRUCTURED_KINDS:
        return (
            f"Reply with ONLY the {kind.upper()} content itself — no introduction, "
            "no explanation, no code fence."
        )
    return ""


#: how much of a failed reply a repair request quotes before it shortens it
QUOTE_LIMIT = 16_000
_QUOTE_HALF = 8_000
#: characters either side of a JSON parse error's position
_AROUND = 40


def _quoted(reply: str) -> str:
    if len(reply) <= QUOTE_LIMIT:
        return reply
    left_out = len(reply) - 2 * _QUOTE_HALF
    return f"{reply[:_QUOTE_HALF]}\n[… {left_out} characters left out …]\n{reply[-_QUOTE_HALF:]}"


def _json_fragment(reply: str) -> str | None:
    """Where a reply that should be one JSON object goes wrong — the text before the
    object when it does not start with one (the usual preamble), else the text around
    the parser's position. None when it parses."""
    try:
        json.loads(reply)
    except (json.JSONDecodeError, ValueError) as exc:
        start = reply.find("{")
        lead = reply[:start] if start > 0 else ""
        if lead.strip():
            return lead.rstrip()
        pos = getattr(exc, "pos", 0)
        return reply[max(0, pos - _AROUND) : pos + _AROUND]
    return None


def _yaml_fragment(reply: str) -> str | None:
    import yaml

    try:
        yaml.safe_load(reply)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        lines = reply.splitlines()
        if mark is not None and 0 <= mark.line < len(lines):
            return lines[mark.line]
        return lines[0] if lines else ""
    return None


def _csv_fragment(reply: str) -> str | None:
    """The rows whose column count differs from the most common one — a prepended
    sentence is a one-cell row in a wider table. Rebuilt from the parsed rows, not the
    file's lines: a quoted field may hold a newline."""
    import csv
    import io
    from collections import Counter

    rows = [row for row in csv.reader(io.StringIO(reply)) if row]
    if not rows:
        return None
    width = Counter(len(row) for row in rows).most_common(1)[0][0]
    odd = [",".join(row) for row in rows if len(row) != width]
    return "\n".join(odd) if odd else None


_FRAGMENT = {"json": _json_fragment, "yaml": _yaml_fragment, "csv": _csv_fragment}


def repair_request(reply: str, verdict: Any, outputs: dict[str, Any] | None, kind: str) -> str:
    """The prompt of a repair turn (D3): that the reply is still not valid / does not
    fit, the part that is wrong, the whole failed reply quoted (a `map` element must not
    repair a sibling's answer from the shared history), and the format instruction."""
    fmt = "json" if outputs else kind
    fragment = _FRAGMENT[fmt](reply) if fmt in _FRAGMENT and reply.strip() else None
    if not reply.strip():
        # a turn that only called tools: nothing to quote, the answer itself is missing
        head = f"Your reply had no answer in it. Give the answer now, as {fmt.upper()}."
    elif fragment is not None:
        head = f"Your reply is still not valid {fmt.upper()}."
    else:
        head = f"Your reply still does not match the required format: {verdict.reason}."
    parts = [head]
    if fragment is not None:
        parts.append(f"The problem is here:\n<<<\n{fragment}\n>>>")
    if reply.strip():
        parts.append(f"Your whole reply was:\n<<<\n{_quoted(reply)}\n>>>")
    parts.append(instruction(outputs, kind))
    parts.append(
        "Do not redo any work and do not call any tool; rewrite the answer you already have."
    )
    return "\n\n".join(p for p in parts if p)
