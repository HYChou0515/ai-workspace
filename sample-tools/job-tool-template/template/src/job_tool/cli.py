"""The three-stage contract. You will rarely need to edit this file.

The platform talks to the tool through argv and stdout, and nothing else:

    job-tool                     -> [{"name", "description"}, …]   which commands exist
    job-tool submit              -> {"name", "description", "params_json_schema"}
    job-tool submit '<json>'     -> the answer                       do the work

Which commands exist is decided by the backend: one per call it supports.
"""

from __future__ import annotations

import json
import sys

from job_contract import JobBackend
from jsonschema import Draft202012Validator

from job_tool.commands import commands
from job_tool.describe import describe
from job_tool.discover import NoBackend, load_backend
from job_tool.run import run


def _fail(message: str, exit_code: int) -> int:
    """The detail on stderr, the next step in the exit code. stdout stays
    clean: it is the channel the platform reads as the answer."""
    print(message, file=sys.stderr)
    return exit_code


def main(argv: list[str] | None = None, *, backend: type[JobBackend] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if backend is None:
        try:
            backend = load_backend()
        except NoBackend as exc:
            return _fail(str(exc), 1)

    offered = commands(backend)
    if not argv:
        print(json.dumps([{"name": n, "description": describe(n, backend)} for n in offered]))
        return 0

    name, rest = argv[0], argv[1:]
    command = offered.get(name)
    if command is None:
        return _fail(f"unknown command {name!r}; this tool has {list(offered)}", 2)

    if not rest:
        print(
            json.dumps(
                {
                    "name": name,
                    "description": describe(name, backend),
                    "params_json_schema": command.params_json_schema,
                }
            )
        )
        return 0

    try:
        args = json.loads(rest[0])
    except ValueError as exc:
        return _fail(f"arguments for {name} are not JSON: {exc}", 2)
    # The schema the model was shown is the one its arguments are held to, so
    # what reaches the backend is what the backend declared it accepts.
    problems = sorted(Draft202012Validator(command.params_json_schema).iter_errors(args), key=str)
    if problems:
        detail = "; ".join(
            f"{'/'.join(map(str, p.absolute_path)) or 'arguments'}: {p.message}" for p in problems
        )
        return _fail(f"bad arguments for {name}: {detail}", 2)

    outcome = run(name, args, backend)
    if outcome.answer is not None:
        print(json.dumps(outcome.answer))
    if outcome.error is not None:
        print(outcome.error, file=sys.stderr)
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
