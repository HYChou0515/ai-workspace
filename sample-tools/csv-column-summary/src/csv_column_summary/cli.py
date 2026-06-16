"""CLI: 3-stage contract dispatcher for ``summarise`` + ``plot``.

Reference example for **multi-command packages** under the contract
(see docs/plan-skills-and-tools.md §B.2). Both commands share one
venv (pandas + matplotlib installed once) — the main win over a
"one tool per package" layout.

    ./launch                       → JSON list with both commands
    ./launch summarise             → summarise's metadata + JSON schema
    ./launch summarise '<json>'    → run with pydantic-validated args
    ./launch plot                  → plot's metadata + JSON schema
    ./launch plot '<json>'         → run plot

The dispatcher is hand-written (not using a framework) so the contract
stays visible — a new command is one entry in ``commands/__init__.py``
+ one module with an ``Args`` model, a ``DESCRIPTION``, and a ``run``.
"""

from __future__ import annotations

import json
import sys

from pydantic import ValidationError

from csv_column_summary.commands import COMMANDS


def _list_payload() -> str:
    return json.dumps(
        [{"name": n, "description": m.DESCRIPTION} for n, m in COMMANDS.items()]
    )


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
    # Stage 1: bare → list commands as a JSON array.
    if not a:
        print(_list_payload())
        return 0
    cmd_name = a[0]
    mod = COMMANDS.get(cmd_name)
    if mod is None:
        avail = ", ".join(COMMANDS)
        print(f"unknown command: {cmd_name}. available: {avail}", file=sys.stderr)
        return 2
    # Stage 2: command only → metadata + JSON schema.
    if len(a) == 1:
        print(_schema_payload(cmd_name))
        return 0
    # Stage 3: command + JSON args → pydantic validate + run.
    try:
        args = mod.Args.model_validate_json(a[1])
    except ValidationError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        mod.run(args)
    except ValueError as e:
        # Friendly errors from inside `run` (e.g. file not found) → exit 2
        # so the calling agent can recover.
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
