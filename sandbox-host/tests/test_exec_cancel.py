"""P2: a client that walks away takes the running command with it.

The app pod cancels a turn, which closes its `client.stream(...)` to
`/sandboxes/{rid}/exec`. Starlette then closes this response's async generator —
measured, and promptly (0.03s), whether the command is still producing output or
has gone silent. So `_exec_ndjson`'s `finally` IS the place a Stop reaches the
host; it simply awaited the exec task there instead of cancelling it, and the
command ran on to its own end (or to the 60s `exec_timeout`) with the agent's
files still changing under a user who had pressed Stop.

`LocalProcessSandbox.exec` already handles `CancelledError` by SIGKILLing the
command's whole process group. The machinery was complete; nothing pulled the
trigger.
"""

from __future__ import annotations

import asyncio

from sandbox_host.app import _exec_ndjson
from sandbox_host.mock import MockSandbox
from sandbox_host.protocol import ExecResult, OutputSink, SandboxHandle


class _HangingSandbox(MockSandbox):
    """`exec` streams one chunk and then hangs, like a long command that has gone
    quiet. Records whether it was ever cancelled."""

    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    async def exec(  # type: ignore[override]
        self,
        handle: SandboxHandle,
        cmd: list[str],
        on_output: OutputSink | None = None,
        env=None,  # noqa: ANN001 — mirrors the protocol's optional mapping
    ) -> ExecResult:
        if on_output is not None:
            on_output(b"working\n")
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return ExecResult(exit_code=0, stdout=b"", stderr=b"")  # pragma: no cover


async def test_closing_the_stream_cancels_the_running_command():
    """Closing the response generator — what a disconnect does — must cancel the
    exec, not wait for it.

    Bounded on purpose: the unfixed `await task` makes `aclose()` wait out the
    whole 30s command, so a lost cancel is a timeout (a failure) rather than a
    test that sits there agreeing with the bug.
    """
    sb = _HangingSandbox()
    handle = await sb.create({})
    gen = _exec_ndjson(sb, handle, ["sleep", "30"])

    first = await gen.__anext__()  # the command is live and streaming
    assert b'"o"' in first

    await asyncio.wait_for(gen.aclose(), 2)

    assert sb.cancelled


async def test_a_command_that_finishes_normally_is_not_reported_as_cancelled():
    """The `finally` runs on EVERY exit, including the ordinary one after the
    final frame. Cancelling an already-finished task is a no-op, so the normal
    path must be untouched — otherwise the fix for a stopped turn would start
    corrupting every turn that was never stopped."""
    sb = MockSandbox()
    handle = await sb.create({})
    frames = [frame async for frame in _exec_ndjson(sb, handle, ["echo", "hi"])]

    assert frames  # it streamed
    assert b'"exit"' in frames[-1]  # …and ended with the real result, not an error
