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

The facet pager's commands (``facet_index`` / ``facet_page`` / ``facet_exact``,
#848, PR #857) live in ``chart_view.facet.cli``. They run on every scroll, so nothing
here imports pandas at module level: ``validate`` and ``query`` import what
they need when they run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from chart_view.facet import cli as facet_cli

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
    except (json.JSONDecodeError, RecursionError) as e:  # nested past the decoder
        raise ValueError(f"argument is not JSON this command can read: {e}") from None
    if not isinstance(args, dict) or not isinstance(args.get(key), str):
        raise ValueError(f"argument must be {{{key!r}: <string>}}")
    return args[key]


def _validate(path: str) -> int:
    from chart_view.sources import read_source
    from chart_view.validate import check

    root = Path.cwd()
    try:
        text = (root / path.lstrip("/")).read_text(encoding="utf-8")
    except OSError:
        print(f"{path} is not a file in the workspace", file=sys.stderr)
        return 2
    result = check(text, lambda source: read_source(root, source))
    if result.summary is None:
        print("\n".join(result.errors), file=sys.stderr)
        return 2
    print(result.summary)
    return 0


def _query(text: str) -> int:
    from chart_view.query import build
    from chart_view.sources import SourceError, read_source
    from chart_view.spec import SpecError, parse_spec, spec_errors
    from chart_view.transforms import TransformError

    try:
        spec = parse_spec(text)
        errors = spec_errors(spec)
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 2
        answer = build(spec, read_source(Path.cwd(), spec["source"]))
    except (SpecError, SourceError, TransformError) as e:
        print(str(e), file=sys.stderr)
        return 2
    json.dump(answer, sys.stdout, separators=(",", ":"))
    return 0


def main(argv: list[str] | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    every = {**COMMANDS, **facet_cli.COMMANDS}
    if not a:
        print(json.dumps([{"name": n, "description": c["description"]} for n, c in every.items()]))
        return 0
    name = a[0]
    if name not in every:
        print(f"unknown command: {name}. available: {', '.join(every)}", file=sys.stderr)
        return 2
    if len(a) == 1:
        params = facet_cli.schema(name) if name in facet_cli.COMMANDS else _schema(name)
        print(
            json.dumps(
                {
                    "name": name,
                    "description": every[name]["description"],
                    "params_json_schema": params,
                }
            )
        )
        return 0
    if name in facet_cli.COMMANDS:
        return facet_cli.run(name, a[1])
    try:
        value = _argument(name, a[1])
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return _validate(value) if name == "validate" else _query(value)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
