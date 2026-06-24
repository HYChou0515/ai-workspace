"""`boot_step` — startup narration so a hang names itself (#208).

The pod hung silently after the config dump: between the dump and a live
server there's a string of blocking steps (specstar backend connect +
``spec.apply`` schema build, embedder/LLM init, message-queue consumers) that
printed nothing, so any one of them stalling looked identical — silence.

``boot_step`` wraps each step: ``→ step …`` on enter, ``✓ step (1.2s)`` on
success, ``✗ step (failed after 1.2s): Err`` on failure — each line flushed so
it survives a block-buffered (piped, non-TTY) stdout. A true hang leaves the
unmatched ``→`` line as the last output, naming the culprit.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TextIO


@contextmanager
def boot_step(
    label: str,
    *,
    stream: TextIO | None = None,
    clock: Callable[[], float] | None = None,
) -> Iterator[None]:
    out = stream if stream is not None else sys.stdout
    tick = clock if clock is not None else time.monotonic
    print(f"  → {label} …", file=out, flush=True)
    start = tick()
    try:
        yield
    except BaseException as exc:
        print(
            f"  ✗ {label} (failed after {tick() - start:.1f}s): {type(exc).__name__}",
            file=out,
            flush=True,
        )
        raise
    print(f"  ✓ {label} ({tick() - start:.1f}s)", file=out, flush=True)
