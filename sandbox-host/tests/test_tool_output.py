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
from sandbox_host.tool_output import ToolOutputFilter

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


def test_hidden_source_says_how_to_see_it():
    out = _run(ToolOutputFilter(tool="erp", hide_source=True), TB)
    assert b"TOOL_DEBUG" in out and b"erp" in out


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
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    handle = await sb.create(SandboxSpec())
    await sb.upload(handle, _RAISE.encode(), "boom.py")
    return sb, handle


async def test_a_tool_commands_traceback_reaches_nobody_with_its_source(sandbox):
    sb, h = sandbox
    live: list[bytes] = []
    result = await sb.exec(h, [sys.executable, "boom.py"], on_output=live.append, as_tool="erp")
    assert result.exit_code != 0
    assert b"ValueError" in result.stderr
    assert _SOURCE_LINE not in result.stderr
    assert _SOURCE_LINE not in b"".join(live)


async def test_tool_debug_lists_the_tool_and_its_source_comes_back(sandbox):
    sb, h = sandbox
    result = await sb.exec(
        h, [sys.executable, "boom.py"], env={"TOOL_DEBUG": "other, erp"}, as_tool="erp"
    )
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
        [sys.executable, "-c", "import os; print('pw', os.environ['MACHINE_PASSWORD'])"],
        on_output=live.append,
        env={"TOOL_DEBUG": "erp"},
        as_tool="erp",
    )
    assert result.exit_code == 0, result.stderr
    assert result.stdout == b"pw ***\n"
    assert b"hunter2" not in b"".join(live)
