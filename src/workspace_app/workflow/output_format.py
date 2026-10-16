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
