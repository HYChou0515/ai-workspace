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

from job_tool.commands import commands
from job_tool.describe import describe
from job_tool.discover import NoBackend, load_backend


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

    raise NotImplementedError("running a command arrives in P3")


if __name__ == "__main__":
    raise SystemExit(main())
