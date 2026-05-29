"""§B.T2 dispatcher — helper for tool authors implementing the 3-stage
binary contract from `docs/plan-skills-and-tools.md` B.2.

The contract:
  $ ./launch              → JSON array, list of {"name", "description"}
  $ ./launch <cmd>        → JSON object, that command's metadata + JSON schema
  $ ./launch <cmd> '{…}'  → pydantic-validate, then run handler; stdout/stderr/exit_code
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from workspace_app.tooling.dispatcher import Dispatcher


class _Echo(BaseModel):
    text: str
    times: int = 1


class _Greet(BaseModel):
    name: str


def _bare_dispatcher() -> Dispatcher:
    """A dispatcher with two commands registered — `echo` (runs) +
    `greet` (only used to test "lists more than one")."""
    d = Dispatcher()

    @d.command("echo", "Print text some number of times.")
    def _echo(args: _Echo) -> None:
        for _ in range(args.times):
            print(args.text)

    @d.command("greet", "Print a greeting.")
    def _greet(args: _Greet) -> None:
        print(f"hello, {args.name}!")

    return d


def test_dispatcher_lists_commands_with_no_args(capsys):
    """Tracer: zero args → JSON array of registered command metadata
    (sorted by name for determinism)."""
    d = _bare_dispatcher()
    d.main(argv=["./launch"])
    out = capsys.readouterr().out
    assert json.loads(out) == [
        {"name": "echo", "description": "Print text some number of times."},
        {"name": "greet", "description": "Print a greeting."},
    ]


def test_dispatcher_prints_schema_for_known_command(capsys):
    """One arg → JSON object: {name, description, params_json_schema}.
    The schema comes straight from the pydantic Args model."""
    d = _bare_dispatcher()
    d.main(argv=["./launch", "echo"])
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "echo"
    assert out["description"] == "Print text some number of times."
    schema = out["params_json_schema"]
    assert schema["type"] == "object"
    assert "text" in schema["properties"]
    assert "times" in schema["properties"]
    # Required: text (no default); times has a default → not required.
    assert schema["required"] == ["text"]


def test_dispatcher_executes_command_with_validated_args(capsys):
    """Two args → pydantic_validate(argv[2]) → handler(model). Stdout
    is whatever the handler prints; nothing extra."""
    d = _bare_dispatcher()
    d.main(argv=["./launch", "echo", '{"text":"hi","times":2}'])
    assert capsys.readouterr().out == "hi\nhi\n"


def test_dispatcher_validation_error_prints_to_stderr_exit_2(capsys):
    """Bad JSON (wrong type / missing required) → pydantic friendly
    str to stderr + exit_code=2 (sys.exit raises SystemExit)."""
    d = _bare_dispatcher()
    with pytest.raises(SystemExit) as ei:
        d.main(argv=["./launch", "echo", '{"text":42}'])  # text must be str
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "text" in err  # pydantic mentions the bad field
    assert "validation error" in err.lower() or "Input should be" in err


def test_dispatcher_unknown_command_exits_2(capsys):
    """Unknown command name → error to stderr + exit_code=2."""
    d = _bare_dispatcher()
    with pytest.raises(SystemExit) as ei:
        d.main(argv=["./launch", "nope"])
    assert ei.value.code == 2
    assert "nope" in capsys.readouterr().err


def test_dispatcher_command_decorator_requires_pydantic_args():
    """The handler's first param must be annotated with a BaseModel
    subclass; otherwise the decorator raises at registration time —
    fail loud at import, not at runtime."""
    d = Dispatcher()
    with pytest.raises(TypeError):

        @d.command("bad", "no pydantic")
        def _bad(args: dict) -> None: ...  # dict, not BaseModel


def test_dispatcher_command_handler_with_no_args_raises_typeerror():
    """A handler must take exactly one Args param — zero args (or two+)
    is a registration error, not a runtime one. Covers the param-count
    branch of the decorator."""
    d = Dispatcher()
    with pytest.raises(TypeError, match="exactly one Args arg"):

        @d.command("nullary", "no args at all")
        def _bad() -> None: ...


def test_dispatcher_command_handler_with_unresolvable_forward_ref_raises():
    """An Args annotation that can't be resolved (a forward ref pointing
    at a symbol that doesn't exist in any visible namespace) raises
    TypeError at registration — covers the get_type_hints NameError branch."""
    d = Dispatcher()
    # Build a handler whose annotation is a forward ref to something undefined.
    # Using `exec` keeps the bad symbol invisible at module scope so the
    # NameError fires on get_type_hints, not at function definition time.
    src = "def handler(args: 'DefinitelyDoesNotExist') -> None: ...\n"
    ns: dict = {}
    exec(src, ns)
    with pytest.raises(TypeError, match="cannot resolve Args annotation"):
        d.command("bad-ref", "uses an undefined ref")(ns["handler"])


def test_dispatcher_uses_sys_argv_when_argv_not_passed(monkeypatch, capsys):
    """Calling `main()` without argv= falls back to sys.argv — the
    normal CLI entrypoint path."""
    import sys

    d = _bare_dispatcher()
    monkeypatch.setattr(sys, "argv", ["./launch", "echo", '{"text":"yo"}'])
    d.main()
    assert capsys.readouterr().out == "yo\n"
