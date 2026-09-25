"""The chart bundle's commands, under the tool-bundle contract:

    launch                      → a JSON list of the commands
    launch <cmd>                → that command's metadata + JSON schema
    launch <cmd> '<json args>'  → run it (cwd = the workspace)

- ``validate {"path"}`` — what show_file's hook runs before it shows a view
  file (#854 P9): exit 0 with ONE summary line on stdout, or exit 2 with the
  refusal lines on stderr.
- ``query {"path", "rev"}`` — what the renderer runs (`useSandboxRun("chart",
  "query", …)`): the view file in, the per-layer answer (`chart_view.query`)
  out as JSON. The PATH, as ``validate`` takes it (#847/#848 P9): every
  argument travels as one argv string, which the kernel caps at 128 KiB, so a
  spec's text in argv let a big spec pass ``show_file`` and fail every render.
  ``rev`` is the renderer's digest of the text it holds, ignored here: it only
  makes an edited file a new call (the args are the renderer's cache key).
  ``query {"spec"}`` still takes the text, for a view with no file.

Hand-written (no pydantic): two small commands, and a bundle that stays small.

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
        # its arguments are facet_cli.VIEW_ARGUMENTS (see _schema)
    },
}


def _schema(name: str) -> dict[str, Any]:
    if name == "query":  # its file or its text, as facet_build takes a view
        return facet_cli.forms_schema(facet_cli.VIEW_ARGUMENTS, facet_cli.VIEW_FORMS, frozenset())
    c = COMMANDS[name]
    return {
        "type": "object",
        "properties": {c["argument"]: {"type": "string", "description": c["about"]}},
        "required": [c["argument"]],
        "additionalProperties": False,
    }


def _json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as e:  # nested past the decoder
        raise ValueError(f"argument is not JSON this command can read: {e}") from None


def _argument(name: str, raw: str) -> str:
    key = COMMANDS[name]["argument"]
    args = _json(raw)
    if not isinstance(args, dict) or not isinstance(args.get(key), str):
        raise ValueError(f"argument must be {{{key!r}: <string>}}")
    return args[key]


def _query_argument(raw: str) -> tuple[str, str]:
    """``("path", <file>)`` or ``("spec", <text>)``: what ``query`` was given."""
    args = _json(raw)
    shape = "argument must be {'path': <string>, 'rev': <string>} or {'spec': <string>}"
    if not isinstance(args, dict) or set(args) not in facet_cli.VIEW_FORMS:
        raise ValueError(shape)
    if not all(isinstance(v, str) for v in args.values()):
        raise ValueError(shape)
    return ("path", args["path"]) if "path" in args else ("spec", args["spec"])


def read_view(path: str) -> str | None:
    """The view file at `path` in the workspace (the cwd), or None with the
    reason on stderr. What ``validate``, ``query`` and ``facet_build`` read."""
    from chart_view.sources import SourceError, inside_workspace

    try:
        return inside_workspace(Path.cwd(), path).read_text(encoding="utf-8")
    except SourceError as e:
        print(str(e), file=sys.stderr)
    except (OSError, UnicodeDecodeError):
        print(f"{path} is not a readable text file in the workspace", file=sys.stderr)
    return None


def _validate(path: str) -> int:
    from chart_view.sources import read_source
    from chart_view.validate import check

    root = Path.cwd()
    text = read_view(path)
    if text is None:
        return 2
    result = check(text, lambda source: read_source(root, source))
    if result.summary is None:
        print("\n".join(result.errors), file=sys.stderr)
        return 2
    print(result.summary)
    return 0


def _query(text: str) -> int:
    from chart_view.datums import datum_errors
    from chart_view.query import answer, layer_rows
    from chart_view.sources import SourceError, read_source
    from chart_view.spec import SpecError, parse_spec, spec_errors
    from chart_view.transforms import TransformError

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
        if name == "validate":
            form, value = "validate", _argument(name, a[1])
        else:
            form, value = _query_argument(a[1])
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if form == "validate":
        return _validate(value)
    if form == "spec":
        return _query(value)
    text = read_view(value)
    return 2 if text is None else _query(text)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
