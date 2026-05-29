"""Tool author helper: a Dispatcher implementing the 3-stage binary
contract described in `docs/plan-skills-and-tools.md` §B.2.

Each registered command pairs a pydantic ``Args`` model (drives both the
LLM-facing JSON schema **and** runtime validation — single source of
truth) with a handler that takes the validated model and runs the work.

Sample usage (in a tool's `cli.py`)::

    from pydantic import BaseModel
    from workspace_app.tooling.dispatcher import Dispatcher

    d = Dispatcher()

    class FetchArgs(BaseModel):
        name: str
        rows: int = 25_000

    @d.command("data-fetch", "Materialise a named dataset into the workspace.")
    def data_fetch(args: FetchArgs) -> None:
        ...

    def main() -> None:
        d.main()
"""

from __future__ import annotations

import inspect
import json
import sys
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError


class _Command:
    """One registered command: pydantic ``Args`` model + a handler that
    takes the validated model and runs the work (prints whatever it
    needs to stdout / stderr)."""

    def __init__(
        self,
        name: str,
        description: str,
        args_model: type[BaseModel],
        handler: Callable[[Any], None],
    ) -> None:
        self.name = name
        self.description = description
        self.args_model = args_model
        self.handler = handler


class Dispatcher:
    """Three-stage CLI contract dispatcher. Authors register commands
    with `@d.command(name, description)` (the handler's first param
    annotation is the pydantic Args model); then call `d.main()` from
    their console_script entry point.

    No magic outside registration: zero deps beyond pydantic, and the
    dispatcher contains zero domain logic — it just routes argv into
    one of three deterministic paths (list / schema / execute)."""

    def __init__(self) -> None:
        self._commands: dict[str, _Command] = {}

    def command(
        self, name: str, description: str
    ) -> Callable[[Callable[[Any], None]], Callable[[Any], None]]:
        """Decorator: register the wrapped function as `name`. Pulls the
        pydantic Args model from the handler's first parameter annotation
        (typed `BaseModel` subclass; anything else `TypeError`s at
        registration so misuse fails loud, not at runtime)."""

        def wrap(handler: Callable[[Any], None]) -> Callable[[Any], None]:
            sig = inspect.signature(handler)
            params = list(sig.parameters.values())
            if len(params) != 1:
                raise TypeError(
                    f"command {name!r}: handler must take exactly one Args arg, got {len(params)}"
                )
            # Resolve forward refs (PEP 563 / `from __future__ import annotations`
            # leaves the annotation as a string; without `get_type_hints` we'd
            # see `'FetchArgs'` instead of the class).
            try:
                hints = get_type_hints(handler)
            except NameError as e:
                raise TypeError(f"command {name!r}: cannot resolve Args annotation: {e}") from e
            ann = hints.get(params[0].name)
            if not (isinstance(ann, type) and issubclass(ann, BaseModel)):
                raise TypeError(
                    f"command {name!r}: handler's Args param must be a "
                    f"pydantic BaseModel subclass, got {ann!r}"
                )
            self._commands[name] = _Command(name, description, ann, handler)
            return handler

        return wrap

    def main(self, argv: list[str] | None = None) -> None:
        """Run the dispatch loop. ``argv`` defaults to ``sys.argv``.

        Exits 2 on bad subcommand / bad args (with a friendly stderr
        message); on successful execute it returns once the handler
        does — the handler controls its own exit code (default 0 from
        Python falling off the end)."""
        a = argv if argv is not None else sys.argv
        if len(a) == 1:
            self._list_commands()
            return
        cmd = self._commands.get(a[1])
        if cmd is None:
            print(f"unknown command: {a[1]}", file=sys.stderr)
            sys.exit(2)
        if len(a) == 2:
            self._print_schema(cmd)
            return
        self._execute(cmd, a[2])

    # ─── stage handlers ──────────────────────────────────────────────

    def _list_commands(self) -> None:
        meta = [
            {"name": c.name, "description": c.description}
            for c in sorted(self._commands.values(), key=lambda c: c.name)
        ]
        print(json.dumps(meta))

    def _print_schema(self, cmd: _Command) -> None:
        print(
            json.dumps(
                {
                    "name": cmd.name,
                    "description": cmd.description,
                    "params_json_schema": cmd.args_model.model_json_schema(),
                }
            )
        )

    def _execute(self, cmd: _Command, args_json: str) -> None:
        try:
            args = cmd.args_model.model_validate_json(args_json)
        except ValidationError as e:
            print(str(e), file=sys.stderr)
            sys.exit(2)
        cmd.handler(args)
