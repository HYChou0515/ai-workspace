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

from collections.abc import Iterable

_TRACEBACK = b"Traceback (most recent call last):"
_FRAME = b"  File "
_SOURCE = b"    "
#: How much of an unterminated line is held before some of it is released.
_HOLD = 64 * 1024


class ToolOutputFilter:
    """One stream's filter: `feed` each chunk, `flush` once at the end."""

    def __init__(self, *, tool: str, hide_source: bool, secrets: Iterable[str] = ()) -> None:
        self._tool = tool
        self._hide_source = hide_source
        # Longest first, so a value that contains another is masked whole.
        self._secrets = sorted({s.encode() for s in secrets if s}, key=len, reverse=True)
        self._keep = max((len(s) for s in self._secrets), default=1) - 1
        self._pending = b""
        self._in_traceback = False
        self._hid = False

    def feed(self, chunk: bytes) -> bytes:
        self._pending += chunk
        out: list[bytes] = []
        start = 0
        for i, byte in enumerate(self._pending):
            if byte in (0x0A, 0x0D):  # \n, \r
                out.append(self._line(self._pending[start : i + 1]))
                start = i + 1
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
        if self._hid:
            rest += (
                f"[source lines of this traceback are hidden; add {self._tool} to "
                "TOOL_DEBUG to show them]\n"
            ).encode()
            self._hid = False
        return rest

    def _line(self, line: bytes) -> bytes:
        if self._hide_source and self._drops(line):
            self._hid = True
            return b""
        return self._mask(line)

    def _drops(self, line: bytes) -> bool:
        if line.startswith(_TRACEBACK):
            self._in_traceback = True
            return False
        if not self._in_traceback:
            return False
        if line.startswith(_FRAME):
            return False
        if line.startswith(_SOURCE):
            return True
        # The exception line, a blank, or "During handling…": the traceback is
        # over (a chained one starts again with its own header).
        self._in_traceback = False
        return False

    def _mask(self, data: bytes) -> bytes:
        for secret in self._secrets:
            data = data.replace(secret, b"***")
        return data


def tool_debug_names(env: dict[str, str]) -> set[str]:
    """`TOOL_DEBUG=a, b` → `{"a", "b"}` — read from the env the tool is given,
    whatever layer set it (the user decided: not this hop's business)."""
    return {n.strip() for n in env.get("TOOL_DEBUG", "").split(",") if n.strip()}
