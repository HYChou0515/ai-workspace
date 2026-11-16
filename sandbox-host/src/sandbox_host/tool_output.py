"""What a third-party tool prints, filtered before anyone sees it
(docs/plan-tool-identity-secrets.md P3).

Applied in the host's exec pump, per stream, to every chunk — so the live
stream, the final result, the tool card and the text the agent reads are the
same bytes. Two rules:

- **Source lines.** A Python traceback shows the code of every frame. The agent
  cannot read a third-party tool's files (P2), and a traceback would hand them
  back a few lines at a time — the credential a tool was written with, often.
  The frames (`File "…", line N, in f`) and the exception stay: they say where
  and what. Unless the item's `TOOL_DEBUG` names the tool.
- **Credentials.** A value the platform decrypted for the tool (P4) is replaced
  by `***` wherever it appears, `TOOL_DEBUG` or not.

Line-based: a partial line is held until it ends, which is what lets a value
that straddles two reads be masked. A line that never ends is released in
pieces once it is long, keeping back just enough to never split a credential.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Where Python quotes source: under a frame (`File "…", line N` — in a
#: traceback, an exception group's `|` margin, or a SyntaxError, which has no
#: `Traceback` header at all), and under a warning (`path:N: SomeWarning: msg`).
#: A line indented deeper than the line that introduced it is the quote.
_FRAME = re.compile(rb'^([ |]*)File ".*", line \d+')
_WARNING = re.compile(rb"^\S.*:\d+: \w*Warning: ")
#: The shortest line of a multi-line credential masked on its own: shorter ones
#: (a lone `}`) would mask half of every JSON a tool prints, and say nothing.
_MIN_PART = 8
_LINE_END = re.compile(rb"[\r\n]")
#: How much of an unterminated line is held before some of it is released.
_HOLD = 64 * 1024


class ToolOutputFilter:
    """One stream's filter: `feed` each chunk, `flush` once at the end."""

    def __init__(
        self, *, tool: str, hide_source: bool, secrets: Iterable[str] = (), hint: bool = True
    ) -> None:
        self._tool = tool
        self._hide_source = hide_source
        # Whether this stream may carry the "source lines hidden" line. Only
        # stderr: a tool's stdout is often its ANSWER (JSON), and a line added
        # to it would make it unparseable.
        self._hint = hint
        # The filter works per line, so a multi-line value (a private key, a
        # service-account JSON) is also masked line by line. Longest first, so
        # a value that contains another is masked whole.
        parts = {s for s in secrets if s}
        for value in list(parts):
            parts.update(
                line.strip() for line in value.splitlines() if len(line.strip()) >= _MIN_PART
            )
        self._secrets = sorted({s.encode() for s in parts}, key=len, reverse=True)
        self._keep = max((len(s) for s in self._secrets), default=1) - 1
        self._pending = b""
        # The column of the line that introduced a quote (a frame, a warning),
        # or None when the current line is not under one.
        self._quoted_under: int | None = None
        self._hid = False

    def feed(self, chunk: bytes) -> bytes:
        self._pending += chunk
        out: list[bytes] = []
        start = 0
        # Searched, not walked byte by byte in Python: a tool's output is all
        # of it on this path, and a per-byte loop held it to ~3 MB/s.
        for end in _LINE_END.finditer(self._pending):
            out.append(self._line(self._pending[start : end.end()]))
            start = end.end()
        self._pending = self._pending[start:]
        if len(self._pending) > _HOLD:
            # Mask FIRST, then hold back the tail: after masking, the only
            # unmasked credential bytes left can be an incomplete occurrence at
            # the very end, shorter than the longest one. Cutting before masking
            # would release the head of a value that straddles the cut.
            masked = self._mask(self._pending)
            cut = len(masked) - self._keep
            out.append(masked[:cut])
            self._pending = masked[cut:]
        return b"".join(out)

    def flush(self) -> bytes:
        rest = self._line(self._pending) if self._pending else b""
        self._pending = b""
        if self._hid and self._hint:
            # Not naming the switch that unhides them: the agent reads this
            # line, and the docs are where a person learns it.
            rest += f"[{self._tool}: source lines in this output are hidden]\n".encode()
            self._hid = False
        return rest

    def _line(self, line: bytes) -> bytes:
        if self._hide_source and self._drops(line):
            self._hid = True
            return b""
        return self._mask(line)

    def _drops(self, line: bytes) -> bool:
        body = line.rstrip(b"\r\n")
        if not body:
            # A bare line end — CRLF arrives as "…\r" then "\n" — or a blank:
            # neither starts nor ends a quote.
            return False
        if frame := _FRAME.match(body):
            self._quoted_under = len(frame.group(1))
            return False
        if _WARNING.match(body):
            self._quoted_under = 0
            return False
        if self._quoted_under is not None and _column(body) > self._quoted_under:
            return True
        # The exception line, a group's separator, "During handling…": the
        # quote is over (the next frame starts another).
        self._quoted_under = None
        return False

    def _mask(self, data: bytes) -> bytes:
        for secret in self._secrets:
            data = data.replace(secret, b"***")
        return data


def tool_debug_names(env: dict[str, str]) -> set[str]:
    """`TOOL_DEBUG=a, b` → `{"a", "b"}` — read from the env the tool is given,
    whatever layer set it (the user decided: not this hop's business)."""
    return {n.strip() for n in env.get("TOOL_DEBUG", "").split(",") if n.strip()}


def _column(line: bytes) -> int:
    """Where the text starts, past spaces and an exception group's `|` margin."""
    return len(line) - len(line.lstrip(b" |"))
