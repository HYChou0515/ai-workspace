"""Your own helpers. This file belongs to you — change it freely.

It exists so a command can be one decorated function. The platform never sees
it: your tool is reached through argv and stdout, so anything that produces a
name, a description, an `Args` model and a `run` is a valid command, whether
you write those four things out or let a decorator assemble them.

Keeping the decorator here rather than importing one from the platform is what
lets you run `pytest` with nothing installed but your own dependencies, and
lets the platform evolve without reaching into your repository.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import get_type_hints

from pydantic import BaseModel


class ToolError(Exception):
    """A failure worth reporting, with the exit code that says what to do next.

    The platform reads the exit code and tells the model how to proceed, so
    the code is guidance and the message is the detail. Raise the subclass
    that matches; the plain `ToolError` covers everything else."""

    exit_code = 1


class Retryable(ToolError):
    """Calling again may work — a bad argument the model can fix, a timeout,
    an upstream that was briefly unavailable.

        raise Retryable(f"no such file in the workspace: {args.path}")

    The model may call again. The platform never retries on its own: your tool
    may have side effects, and repeating them unasked is its own bug."""

    exit_code = 2


class NeedsAction(ToolError):
    """A person has to do something first, so calling again changes nothing.

        raise NeedsAction("set FTP_PASSWORD in the workspace's environment variables")

    Name what is missing. The model relays it, and for a variable the user can
    set it themselves in this workspace's environment panel."""

    exit_code = 3


@dataclass(frozen=True)
class Command:
    """What `cli.py` needs from a command, however it was written."""

    DESCRIPTION: str
    Args: type[BaseModel]
    run: Callable[[BaseModel], str]


#: Commands that registered themselves by decoration, in declaration order.
REGISTERED: dict[str, Command] = {}


def command(name: str, description: str) -> Callable[[Callable], Callable]:
    """Register one command from one function.

        @command("head", description="Show the first lines of a file.")
        def head(args: Args) -> str: ...

    The `Args` model is taken from the parameter's annotation, so the schema
    the model is shown and the validation your code relies on remain the same
    object. `description` is what a model reads to decide whether to call you,
    so give it a real sentence.
    """

    def register(fn: Callable) -> Callable:
        hints = get_type_hints(fn)
        hints.pop("return", None)
        if len(hints) != 1:
            raise TypeError(
                f"{fn.__name__} takes exactly one annotated parameter, its Args model; "
                f"found {sorted(hints)}"
            )
        model = next(iter(hints.values()))
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise TypeError(f"{fn.__name__}'s parameter must be annotated with a pydantic model")

        REGISTERED[name] = Command(DESCRIPTION=description, Args=model, run=fn)
        return fn

    return register
