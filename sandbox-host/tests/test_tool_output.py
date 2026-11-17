"""What a third-party tool prints is filtered before anyone sees it
(docs/plan-tool-identity-secrets.md P3).

Two rules, both applied where the output enters the platform — the host's exec
pump — so the live stream, the tool card and the text the agent reads are one:

- a Python traceback loses its SOURCE lines (the code under each `File …` and
  the `^^^` markers), keeping the frames and the exception, unless the item's
  `TOOL_DEBUG` lists the tool;
- a credential the platform handed the tool is replaced by `***`, always.
"""

from __future__ import annotations

import sys

import pytest

from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.protocol import SandboxSpec
from sandbox_host.tool_cache import EXT_DIR, ToolCache
from sandbox_host.tool_output import ToolOutputFilter

from .test_tool_secrets_host import _bundle

TB = (
    b"Traceback (most recent call last):\n"
    b'  File "/x/.venv/bin/erp", line 8, in <module>\n'
    b"    sys.exit(main())\n"
    b"             ^^^^^^\n"
    b'  File "/x/erp/cli.py", line 12, in main\n'
    b"    conn = connect(user='svc', password=PASSWORD)\n"
    b"ValueError: machine refused the login\n"
)


def _run(f: ToolOutputFilter, data: bytes, step: int | None = None) -> bytes:
    if step is None:
        return f.feed(data) + f.flush()
    out = b"".join(f.feed(data[i : i + step]) for i in range(0, len(data), step))
    return out + f.flush()


def test_a_traceback_keeps_its_frames_and_exception_but_not_its_source():
    out = _run(ToolOutputFilter(tool="erp", hide_source=True), TB)
    assert b'File "/x/erp/cli.py", line 12, in main' in out
    assert b"ValueError: machine refused the login" in out
    assert b"password=PASSWORD" not in out
    assert b"sys.exit(main())" not in out
    assert b"^^^^^^" not in out


def test_hidden_source_is_said_without_naming_how_to_unhide_it():
    """The agent reads this line. Naming the variable that unhides source is an
    invitation to ask the user for it (review round 1); the docs say how."""
    out = _run(ToolOutputFilter(tool="erp", hide_source=True), TB)
    assert b"source lines" in out and b"hidden" in out
    assert b"TOOL_DEBUG" not in out


# Captured from CPython 3.12 — the formats are Python's, not ours to invent.
_GROUP = (
    b"  + Exception Group Traceback (most recent call last):\n"
    b'  |   File "/w/eg.py", line 8, in <module>\n'
    b"  |     g()\n"
    b'  |   File "/w/eg.py", line 4, in g\n'
    b'  |     raise ExceptionGroup("grp", [ValueError("x")])  # SECRET-G\n'
    b"  |     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
    b"  | ExceptionGroup: grp (1 sub-exception)\n"
    b"  +-+---------------- 1 ----------------\n"
    b"    | Traceback (most recent call last):\n"
    b'    |   File "/w/eg.py", line 2, in f\n'
    b'    |     raise ValueError("x")  # SECRET-F\n'
    b"    | ValueError: x\n"
    b"    +------------------------------------\n"
)
_WARNING = b'/w/w.py:2: UserWarning: careful\n  warnings.warn("careful")  # SECRET-W\n'
_SYNTAX = (
    b'  File "/w/s.py", line 1\n'
    b"    x = (1 +  # SECRET-S\n"
    b"        ^\n"
    b"SyntaxError: '(' was never closed\n"
)


def test_an_exception_group_loses_its_source_lines():
    out = _run(ToolOutputFilter(tool="erp", hide_source=True), _GROUP)
    assert b"SECRET" not in out
    assert b'File "/w/eg.py", line 4, in g' in out
    assert b"ExceptionGroup: grp (1 sub-exception)" in out
    assert b"ValueError: x" in out


def test_a_warning_loses_the_source_line_it_quotes():
    out = _run(ToolOutputFilter(tool="erp", hide_source=True), _WARNING)
    assert b"SECRET" not in out
    assert b"UserWarning: careful" in out


def test_a_syntax_error_loses_its_source_line():
    out = _run(ToolOutputFilter(tool="erp", hide_source=True), _SYNTAX)
    assert b"SECRET" not in out
    assert b"SyntaxError: '(' was never closed" in out


def test_a_warning_hides_only_the_one_line_python_quotes():
    """Review round 2 (L1): Python prints one source line under a warning. The
    tool's own indented lines after it are its output, not a quote."""
    data = _WARNING + b"  data row 1\n  data row 2\n"
    out = _run(ToolOutputFilter(tool="erp", hide_source=True), data)
    assert b"SECRET" not in out
    assert b"  data row 1\n  data row 2\n" in out


def test_a_frame_at_column_zero_is_not_a_frame():
    """Python always indents `File "…"` under its header; a tool printing a
    log line that starts with it at column 0 is not a traceback."""
    data = b'File "report.py", line 3 is where the totals are\n    | col | val |\n'
    assert _run(ToolOutputFilter(tool="erp", hide_source=True), data) == data


def test_a_blank_line_ends_a_quote():
    data = b'  File "/w/x.py", line 1, in f\n\n    indented answer text\n'
    out = _run(ToolOutputFilter(tool="erp", hide_source=True), data)
    assert b"    indented answer text\n" in out


def test_crlf_output_is_filtered_the_same():
    out = _run(ToolOutputFilter(tool="erp", hide_source=True), TB.replace(b"\n", b"\r\n"))
    assert b"password=PASSWORD" not in out
    assert b"ValueError: machine refused the login" in out


def test_tool_debug_keeps_every_format():
    data = _GROUP + _WARNING + _SYNTAX
    assert _run(ToolOutputFilter(tool="erp", hide_source=False), data) == data


def test_a_multi_line_credential_is_masked_line_by_line():
    """A private key or a service-account JSON is a typical CI variable; the
    filter works per line, so each of its lines is masked on its own."""
    pem = (
        "-----BEGIN KEY-----\n"
        "MIIBVgIBADANBgkqhkiG9w0BAQEF\n"
        "AASCAUAwggE8AgEAAkEAq7BFUpkG\n"
        "-----END KEY-----"
    )
    f = ToolOutputFilter(tool="erp", hide_source=False, secrets=[pem])
    out = _run(f, (pem + "\n").encode())
    assert b"MIIBVgIBADANBgkqhkiG9w0BAQEF" not in out
    assert b"AASCAUAwggE8AgEAAkEAq7BFUpkG" not in out


def test_a_stream_that_does_not_carry_the_hint_stays_parseable():
    """Review round 1: a tool that logs a handled traceback to stdout and then
    prints its JSON answer there must still print valid JSON — the hint goes on
    stderr only."""
    f = ToolOutputFilter(tool="erp", hide_source=True, hint=False)
    out = _run(f, TB + b'{"ok": true}\n')
    assert out.endswith(b'{"ok": true}\n')
    assert b"hidden" not in out


def test_tool_debug_keeps_the_source():
    out = _run(ToolOutputFilter(tool="erp", hide_source=False), TB)
    assert out == TB


def test_a_chained_traceback_is_filtered_in_every_part():
    chained = TB + b"\nDuring handling of the above exception, another exception occurred:\n\n" + TB
    out = _run(ToolOutputFilter(tool="erp", hide_source=True), chained)
    assert out.count(b"ValueError: machine refused the login") == 2
    assert b"password=PASSWORD" not in out
    assert b"During handling" in out


def test_indented_output_outside_a_traceback_is_untouched():
    """A tool printing pretty JSON is not a traceback."""
    data = b'{\n    "lot": "A1",\n    "yield": 0.93\n}\n'
    assert _run(ToolOutputFilter(tool="erp", hide_source=True), data) == data


def test_a_credential_is_masked_wherever_it_appears():
    f = ToolOutputFilter(tool="erp", hide_source=False, secrets=["hunter2"])
    out = _run(f, b"login as svc / hunter2 ok\nretry hunter2\n")
    assert out == b"login as svc / *** ok\nretry ***\n"


@pytest.mark.parametrize("step", [1, 2, 3, 5])
def test_a_credential_split_across_chunks_is_still_masked(step):
    """The pump reads 4 KiB at a time and a credential can straddle two reads;
    every split must give what one read gives."""
    data = b"token=s3cr3t-value\n" + TB + b"tail s3cr3t-value"
    whole = _run(ToolOutputFilter(tool="erp", hide_source=True, secrets=["s3cr3t-value"]), data)
    split = _run(
        ToolOutputFilter(tool="erp", hide_source=True, secrets=["s3cr3t-value"]), data, step
    )
    assert split == whole
    assert b"s3cr3t-value" not in split


def test_the_longest_credential_is_masked_first():
    """`abc` inside `abcdef` must not leave `***def` behind."""
    f = ToolOutputFilter(tool="erp", hide_source=False, secrets=["abc", "abcdef"])
    assert _run(f, b"abcdef\n") == b"***\n"


def test_a_long_line_with_no_end_is_not_held_forever():
    """A progress line can run long; it streams in pieces rather than at exit,
    still without ever releasing a partial credential."""
    f = ToolOutputFilter(tool="erp", hide_source=False, secrets=["hunter2"])
    body = b"x" * 200_000 + b"hunter2"
    first = f.feed(body)
    assert first, "some of a long line must stream before the command ends"
    out = first + f.flush()
    assert b"hunter2" not in out
    assert out.endswith(b"***")


def test_carriage_returns_end_a_line_too():
    """Progress bars redraw with `\\r`; waiting for `\\n` would hold them to the end."""
    f = ToolOutputFilter(tool="erp", hide_source=True)
    assert f.feed(b"10%\r20%\r") == b"10%\r20%\r"


# ── wired into the host's exec ───────────────────────────────────────────────

_RAISE = (
    "def main():\n"
    "    secret_password = 'hunter2'\n"
    "    raise ValueError('refused ' + secret_password)\n"
    "main()\n"
)
#: The line a traceback of `_RAISE` prints under its frame — the one to hide.
_SOURCE_LINE = b"raise ValueError('refused ' + secret_password)"


@pytest.fixture
async def sandbox(tmp_path):
    """A real file, not `python -c`: a `-c` traceback has no source lines at
    all, so a test of hiding them would pass on unfiltered output."""
    tools = tmp_path / "tools"
    sha = "e" * 64
    ToolCache(tools / EXT_DIR, harden=lambda _p: None).ensure(sha, _bundle(None))
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    handle = await sb.create(SandboxSpec(tools={"erp": sha}))
    await sb.upload(handle, _RAISE.encode(), "boom.py")
    return sb, handle


#: A tool call that runs `boom.py` through the mounted launcher — the only
#: shape a `tool` exec may take.
_BOOM = ["../.tools/erp/launch", "run", "import runpy; runpy.run_path('boom.py')"]


async def test_a_tool_commands_traceback_reaches_nobody_with_its_source(sandbox):
    sb, h = sandbox
    live: list[bytes] = []
    result = await sb.exec(h, _BOOM, on_output=live.append, tool="erp")
    assert result.exit_code != 0
    assert b"ValueError" in result.stderr
    assert _SOURCE_LINE not in result.stderr
    assert _SOURCE_LINE not in b"".join(live)


async def test_tool_debug_lists_the_tool_and_its_source_comes_back(sandbox):
    sb, h = sandbox
    result = await sb.exec(h, _BOOM, env={"TOOL_DEBUG": "other, erp"}, tool="erp")
    assert _SOURCE_LINE in result.stderr


async def test_the_agents_own_command_is_not_filtered(sandbox):
    sb, h = sandbox
    result = await sb.exec(h, [sys.executable, "boom.py"])
    assert _SOURCE_LINE in result.stderr


async def test_a_credential_the_platform_handed_the_tool_is_masked(sandbox, monkeypatch):
    sb, h = sandbox
    monkeypatch.setattr(sb, "_tool_secrets", lambda handle, tool: {"MACHINE_PASSWORD": "hunter2"})
    live: list[bytes] = []
    result = await sb.exec(
        h,
        ["../.tools/erp/launch", "run", "import os; print('pw', os.environ['MACHINE_PASSWORD'])"],
        on_output=live.append,
        env={"TOOL_DEBUG": "erp"},
        tool="erp",
    )
    assert result.exit_code == 0, result.stderr
    assert result.stdout == b"pw ***\n"
    assert b"hunter2" not in b"".join(live)


def test_a_credential_straddling_a_long_line_release_is_still_masked():
    """The release keeps back the tail a credential could still be completed in;
    without it the head of one split across that point would go out bare."""
    f = ToolOutputFilter(tool="erp", hide_source=False, secrets=["hunter2"])
    first = f.feed(b"x" * 70_000 + b"hunt")
    out = first + f.feed(b"er2 done\n") + f.flush()
    assert b"hunt" not in first
    assert b"hunter2" not in out
    assert out.endswith(b"*** done\n")


async def test_a_last_line_without_a_newline_and_the_hint_reach_the_result(sandbox):
    """What the filters still hold when the command ends is flushed into the
    result — the final line, and (on stderr) the line saying source was hidden."""
    sb, h = sandbox
    tail = "import sys; sys.stdout.write('last'); import runpy; runpy.run_path('boom.py')"
    result = await sb.exec(h, ["../.tools/erp/launch", "run", tail], tool="erp")
    assert result.stdout == b"last"
    assert result.stderr.endswith(b"source lines in this output are hidden]\n")


async def test_a_tools_stdout_never_carries_the_hint(sandbox):
    """Review round 1: a tool that logs a handled traceback to stdout and then
    prints its JSON answer there must still print valid JSON."""
    sb, h = sandbox
    code = (
        "import traceback, runpy\n"
        "try:\n    runpy.run_path('boom.py')\n"
        "except ValueError:\n    traceback.print_exc(file=__import__('sys').stdout)\n"
        "print('{\"ok\": true}')"
    )
    result = await sb.exec(h, ["../.tools/erp/launch", "run", code], tool="erp")
    assert result.stdout.endswith(b'{"ok": true}\n')
    assert b"hidden" not in result.stdout
