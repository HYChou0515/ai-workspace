"""The three-stage contract. You will rarely need to edit this file.

The platform talks to the tool through argv and stdout, and nothing else:

    job-tool                     -> [{"name", "description"}, …]   which commands exist
    job-tool submit              -> {"name", "description", "params_json_schema"}
    job-tool submit '<json>'     -> the answer                       do the work

Which commands exist is decided by the backend: one per call it supports.
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any

from job_contract import JobBackend
from jsonschema import Draft202012Validator

from job_tool.commands import commands
from job_tool.describe import describe
from job_tool.discover import NoBackend, load_backend
from job_tool.run import abandoned, run


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

    if len(rest) > 1:
        return _fail(f"{name} takes one JSON object of arguments; got {len(rest)} values", 2)
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
    args = _whole(args)

    # stdout is the answer and nothing else. While the backend runs — and
    # after, since an abandoned call may still be printing — anything it
    # prints goes to stderr; the answer is written to the stdout we started with.
    answer_to = sys.stdout
    sys.stdout = sys.stderr
    try:
        outcome = run(name, args, backend)
    finally:
        if not abandoned():
            sys.stdout = answer_to
    if outcome.answer is not None:
        print(_dump(outcome.answer), file=answer_to)
    if outcome.error is not None:
        print(outcome.error, file=sys.stderr)
    return outcome.exit_code


def _whole(value: Any) -> Any:
    """`value` with every whole-number float made an int. JSON Schema counts
    `5.0` as an integer, so it passes validation; left a float it would give
    the same spec a different idempotency key and break a backend that slices
    or ranges with it."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _whole(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_whole(item) for item in value]
    return value


def _jsonable(value: Any) -> Any:
    """`value` with what JSON cannot say spelled as text: NaN and infinities
    (which `json` would write as bare, invalid tokens) and anything that is
    not a JSON type (a datetime in `detail`, say)."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, str | int | float):
        return value
    return str(value)


def _dump(answer: dict[str, Any]) -> str:
    return json.dumps(_jsonable(answer), allow_nan=False)


def entry(argv: list[str] | None = None, *, backend: type[JobBackend] | None = None) -> int:
    """The installed command. Like `main`, except that once a call has been
    abandoned it ends the process the moment the answer is out: the backend
    may have left a thread pool running, and the interpreter would wait for
    it at exit — up to the platform's whole ceiling."""
    code = main(argv, backend=backend)
    if abandoned():
        sys.stdout.flush()
        sys.__stdout__.flush()
        sys.stderr.flush()
        os._exit(code)
    return code


if __name__ == "__main__":
    raise SystemExit(entry())
