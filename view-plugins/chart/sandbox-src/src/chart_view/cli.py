"""The chart bundle's commands, under the tool-bundle contract:

    launch                      → a JSON list of the commands
    launch <cmd>                → that command's metadata + JSON schema
    launch <cmd> '<json args>'  → run it (cwd = the workspace)

- ``validate {"path"}`` — what show_file's hook runs before it shows a view
  file (#854 P9): exit 0 with ONE summary line on stdout, or exit 2 with the
  refusal lines on stderr.
- ``query {"spec"}`` — what the renderer runs (`useSandboxRun("chart",
  "query", …)`): the spec's text in, the per-layer answer (`chart_view.query`)
  out as JSON. The TEXT, not a path: the renderer holds the file's current
  text, and a new text is a new cache key.

Hand-written (no pydantic): two commands with one string argument each, and a
bundle that stays small.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from chart_view.datums import datum_errors
from chart_view.query import answer, layer_rows
from chart_view.sources import SourceError, inside_workspace, read_source
from chart_view.spec import SpecError, parse_spec, spec_errors
from chart_view.transforms import TransformError
from chart_view.validate import check

COMMANDS: dict[str, dict[str, Any]] = {
    "validate": {
        "description": "Check a view: chart file before it is shown; print a one-line summary.",
        "argument": "path",
        "about": "The .ai.yaml file, relative to the workspace.",
    },
    "query": {
        "description": "Compute what a view: chart spec draws, as the renderer's JSON answer.",
        "argument": "spec",
        "about": "The chart file's YAML text.",
    },
}


def _schema(name: str) -> dict[str, Any]:
    c = COMMANDS[name]
    return {
        "type": "object",
        "properties": {c["argument"]: {"type": "string", "description": c["about"]}},
        "required": [c["argument"]],
        "additionalProperties": False,
    }


def _argument(name: str, raw: str) -> str:
    key = COMMANDS[name]["argument"]
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"argument is not JSON: {e}") from e
    if not isinstance(args, dict) or not isinstance(args.get(key), str):
        raise ValueError(f"argument must be {{{key!r}: <string>}}")
    return args[key]


def _validate(path: str) -> int:
    root = Path.cwd()
    try:
        text = inside_workspace(root, path).read_text(encoding="utf-8")
    except SourceError as e:
        print(str(e), file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError):
        print(f"{path} is not a readable text file in the workspace", file=sys.stderr)
        return 2
    result = check(text, lambda source: read_source(root, source))
    if result.summary is None:
        print("\n".join(result.errors), file=sys.stderr)
        return 2
    print(result.summary)
    return 0


def _query(text: str) -> int:
    try:
        spec = parse_spec(text)
        errors = spec_errors(spec)
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 2
        layers = layer_rows(spec, read_source(Path.cwd(), spec["source"]))
        # What validate refuses, query refuses too: a hand-edited file
        # would otherwise draw with the rule silently missing.
        reply = answer(spec, layers)
        errors = datum_errors(spec, lambda: reply)
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 2
    except (SpecError, SourceError, TransformError) as e:
        print(str(e), file=sys.stderr)
        return 2
    json.dump(reply, sys.stdout, separators=(",", ":"))
    return 0


def main(argv: list[str] | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    if not a:
        print(
            json.dumps([{"name": n, "description": c["description"]} for n, c in COMMANDS.items()])
        )
        return 0
    name = a[0]
    if name not in COMMANDS:
        print(f"unknown command: {name}. available: {', '.join(COMMANDS)}", file=sys.stderr)
        return 2
    if len(a) == 1:
        c = COMMANDS[name]
        print(
            json.dumps(
                {"name": name, "description": c["description"], "params_json_schema": _schema(name)}
            )
        )
        return 0
    try:
        value = _argument(name, a[1])
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return _validate(value) if name == "validate" else _query(value)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
