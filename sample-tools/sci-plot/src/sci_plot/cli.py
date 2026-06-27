"""CLI: 3-stage contract dispatcher (same shape as csv-column-summary).

    ./launch                    → JSON list of commands
    ./launch chart              → chart's metadata + JSON schema
    ./launch chart '<json>'     → pydantic-validate args + render

The dispatcher is generic over ``COMMANDS``; sci-plot registers a single
``chart`` command whose schema is the registry's discriminated union.
"""

from __future__ import annotations

import json
import sys

from pydantic import ValidationError

from sci_plot.commands import COMMANDS


def _list_payload() -> str:
    return json.dumps([{"name": n, "description": m.DESCRIPTION} for n, m in COMMANDS.items()])


def _schema_payload(cmd_name: str) -> str:
    mod = COMMANDS[cmd_name]
    return json.dumps(
        {
            "name": cmd_name,
            "description": mod.DESCRIPTION,
            "params_json_schema": mod.Args.model_json_schema(),
        }
    )


def main(argv: list[str] | None = None) -> int:
    a = argv if argv is not None else sys.argv[1:]
    if not a:  # Stage 1: list commands.
        print(_list_payload())
        return 0
    cmd_name = a[0]
    mod = COMMANDS.get(cmd_name)
    if mod is None:
        avail = ", ".join(COMMANDS)
        print(f"unknown command: {cmd_name}. available: {avail}", file=sys.stderr)
        return 2
    if len(a) == 1:  # Stage 2: schema dump.
        print(_schema_payload(cmd_name))
        return 0
    # Stage 3: validate + run.
    try:
        args = mod.Args.model_validate_json(a[1])
    except ValidationError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        mod.run(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
