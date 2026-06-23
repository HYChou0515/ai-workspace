"""Mid-stream repetition guard — detect neural text degeneration (#113).

A model that loops on the same phrase / sentence / multi-sentence block never
converges: it burns tokens and stalls the turn. This is *neural text
degeneration* (Holtzman et al. 2020), seen even on large hosted models, and no
backend penalty reliably stops it (Ollama's Go runner silently drops
`repetition_penalty`). So we watch the streamed tail ourselves: when it
degenerates into a block repeated `repeats` times, the caller can stop
generation and truncate the persisted text to before the loop began.

Pure + incremental: `feed()` each delta, get a `RepetitionResult` the first
time the tail loops; `reset()` between responses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepetitionResult:
    """A detected loop. ``loop_length`` is the number of trailing characters
    that form the repeating run, so the caller drops the last ``loop_length``
    chars of its accumulated content to keep only the clean prefix. (Reported
    as a tail length, not an absolute offset, so it stays correct even though
    the detector only retains a sliding window of recent text.)"""

    loop_length: int


class RepetitionDetector:
    """Flags a tail that is a block of length ``p`` repeated ``repeats`` times.

    ``p`` ranges over ``1..max_period`` (small ``p`` catches ``the the the``,
    large ``p`` a repeated multi-sentence block); only the last ``window``
    chars are kept. Detection is suppressed inside fenced code blocks (```)
    where repetition is legitimate.
    """

    def __init__(
        self,
        *,
        repeats: int = 10,
        max_period: int = 800,
        window: int = 10000,
        min_loop_chars: int = 1200,
    ) -> None:
        self._repeats = repeats
        self._max_period = max_period
        self._window = window
        # The line between *degeneration* and *legitimate bounded repetition*
        # (a wide table separator `| --- | --- | …`, a list, a numeric column)
        # is not the *kind* of text but the *amount*: a real loop is unbounded
        # and runs on past any structure, whereas even a 200-column table row
        # ends at the newline. So we only believe a periodic tail once it spans
        # ``min_loop_chars`` AND repeats ``repeats`` times — both floors are
        # generous on purpose: this in-stream guard is a last-resort backstop,
        # so a few wasted tokens beat truncating a legitimate answer (#146).
        self._min_loop_chars = min_loop_chars
        self._buf = ""
        self._in_fence = False
        self._backtick_run = 0

    def reset(self) -> None:
        """Forget all accumulated state — call at every response boundary so a
        loop is only ever detected *within* a single model response."""
        self._buf = ""
        self._in_fence = False
        self._backtick_run = 0

    def feed(self, delta: str) -> RepetitionResult | None:
        for ch in delta:
            if self._track_fence(ch):
                # Crossed a ``` boundary — code must never mix with prose for
                # loop detection, so drop everything seen so far.
                self._buf = ""
                continue
            if self._in_fence:
                continue
            self._buf += ch
        if len(self._buf) > self._window:
            self._buf = self._buf[-self._window :]
        if self._in_fence:
            return None
        return self._scan()

    def _track_fence(self, ch: str) -> bool:
        """Feed one char to the fence tracker; return True iff it just toggled
        in/out of a ``` fence."""
        if ch == "`":
            self._backtick_run += 1
            if self._backtick_run == 3:  # third backtick of the run = a fence marker
                self._in_fence = not self._in_fence
                return True
            return False
        self._backtick_run = 0
        return False

    def _scan(self) -> RepetitionResult | None:
        buf = self._buf
        n = len(buf)
        max_p = min(self._max_period, n // self._repeats)
        for p in range(1, max_p + 1):
            block = buf[n - p :]
            # Count how many times `block` actually repeats back from the tail.
            r = 1
            while (start := n - p * (r + 1)) >= 0 and buf[start : start + p] == block:
                r += 1
            if r >= self._repeats and r * p >= self._min_loop_chars:
                return RepetitionResult(loop_length=r * p)
        return None
