"""The three-stage contract. You will rarely need to edit this file.

The platform talks to the tool through argv and stdout, and nothing else:

    job-tool                     -> [{"name", "description"}, …]   which commands exist
    job-tool submit              -> {"name", "description", "params_json_schema"}
    job-tool submit '<json>'     -> the answer                       do the work

Which commands exist is decided by the backend: one per call it supports.
"""

from __future__ import annotations

import io
import json
import math
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TextIO

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


@contextmanager
def _answer_channel() -> Iterator[TextIO]:
    """A stream that reaches the real stdout, while everything else that would
    have — a backend's `print`, a library's banner at import, the output of a
    command the backend runs (`sbatch`, `kubectl`), a C extension — goes to
    stderr instead. stdout is the channel the platform parses as the answer,
    so one stray line there corrupts it.

    Swapping `sys.stdout` alone catches only Python-level prints: a child
    process writes to file descriptor 1 directly. So when stdout is a real
    file descriptor, fd 1 itself is pointed at stderr for the duration and the
    answer is written to a duplicate of the original. Once a call has been
    abandoned nothing is restored: its thread may still be writing, and
    `entry` ends the process right after."""
    original = sys.stdout
    answer: TextIO = original
    saved: int | None = None
    try:
        is_fd1 = original.fileno() == 1
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        is_fd1 = False  # not a real descriptor (a test's capture): sys level only
    if is_fd1:
        original.flush()
        saved = os.dup(1)
        os.dup2(2, 1)
        answer = open(saved, "w", encoding="utf-8", closefd=False)  # noqa: SIM115
    sys.stdout = sys.stderr
    try:
        yield answer
    finally:
        answer.flush()
        if not abandoned():
            if saved is not None:
                os.dup2(saved, 1)
                os.close(saved)
            sys.stdout = original


def main(argv: list[str] | None = None, *, backend: type[JobBackend] | None = None) -> int:
    with _answer_channel() as answer:
        return _main(sys.argv[1:] if argv is None else argv, backend, answer)


def _main(argv: list[str], backend: type[JobBackend] | None, answer: TextIO) -> int:
    if backend is None:
        try:
            backend = load_backend()  # imports the backend: inside the channel
        except NoBackend as exc:
            return _fail(str(exc), 1)

    offered = commands(backend)
    if not argv:
        listed = [{"name": n, "description": describe(n, backend)} for n in offered]
        print(json.dumps(listed), file=answer)
        return 0

    name, rest = argv[0], argv[1:]
    command = offered.get(name)
    if command is None:
        return _fail(f"unknown command {name!r}; this tool has {list(offered)}", 2)

    if not rest:
        described = {
            "name": name,
            "description": describe(name, backend),
            "params_json_schema": command.params_json_schema,
        }
        print(json.dumps(described), file=answer)
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

    outcome = run(name, args, backend)
    if outcome.answer is not None:
        print(_dump(outcome.answer), file=answer)
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
