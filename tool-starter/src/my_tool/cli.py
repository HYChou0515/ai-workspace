"""The three-stage contract. Copy this file; you will rarely need to edit it.

The platform talks to your tool through argv and stdout, and nothing else:

    my-tool                       -> [{"name", "description"}, …]   which commands exist
    my-tool count '<json>'        -> whatever your command returns    do the work
    my-tool count                 -> {"name", "description", "params_json_schema"}

The first two lines are how the model learns your tool exists and how to call
it; the third is the call itself.

This dispatcher is deliberately hand-written and depends on nothing but the
standard library and pydantic. Adding a command is one line in
``commands/__init__.py`` — the loop below never changes.
"""

from __future__ import annotations

import json
import sys

from pydantic import ValidationError

from my_tool.commands import COMMANDS


def _fail(message: str) -> int:
    """Errors go to stderr and a non-zero exit. Never to stdout: stdout is the
    channel the platform parses, and a stray line there is read as your
    command's answer."""
    print(message, file=sys.stderr)
    return 1


def main() -> int:
    argv = sys.argv[1:]

    if not argv:
        print(
            json.dumps(
                [{"name": name, "description": cmd.DESCRIPTION} for name, cmd in COMMANDS.items()]
            )
        )
        return 0

    name, rest = argv[0], argv[1:]
    cmd = COMMANDS.get(name)
    if cmd is None:
        return _fail(f"unknown command {name!r}; expected one of {sorted(COMMANDS)}")

    if not rest:
        print(
            json.dumps(
                {
                    "name": name,
                    "description": cmd.DESCRIPTION,
                    # Derived from the pydantic model, never hand-written: the
                    # schema the model is shown and the validation your code
                    # relies on are then the same thing by construction.
                    "params_json_schema": cmd.Args.model_json_schema(),
                }
            )
        )
        return 0

    try:
        args = cmd.Args.model_validate_json(rest[0])
    except ValidationError as exc:
        return _fail(f"bad arguments for {name}: {exc}")

    print(cmd.run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
