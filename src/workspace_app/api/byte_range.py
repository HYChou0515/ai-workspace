"""One HTTP ``Range`` header, read (RFC 9110 §14.1.2) — for the file route,
so a ``<video>`` over it can seek and Safari, which refuses media the server
will not serve by byte range, will play a chat video at all.

Pure: the header and the size in, the slice out. Only the single-range
forms are honoured — ``bytes=a-b``, ``bytes=a-`` (to the end) and
``bytes=-n`` (the last ``n``); anything else (a multi-range, another unit,
a malformed value) is ``None`` = "serve the whole file, 200", which is what
a server that never heard of ranges does and what every client accepts.
A range that starts past the end is :data:`UNSATISFIABLE` (416).
"""

from __future__ import annotations

import re
from typing import Final

_SINGLE = re.compile(r"^bytes=(\d*)-(\d*)$")

UNSATISFIABLE: Final = "unsatisfiable"


def byte_range(header: str | None, size: int) -> tuple[int, int] | str | None:
    """``(first, last)`` inclusive for a satisfiable single range, ``None``
    for no / not-a-single range, :data:`UNSATISFIABLE` when it lies beyond
    the file. ``size`` 0 has no satisfiable range."""
    if not header:
        return None
    m = _SINGLE.match(header.strip())
    if m is None:
        return None
    first_s, last_s = m.groups()
    if not first_s and not last_s:
        return None
    if not first_s:  # bytes=-n: the last n bytes
        n = int(last_s)
        if n == 0 or size == 0:
            return UNSATISFIABLE
        return (max(0, size - n), size - 1)
    first = int(first_s)
    if first >= size:
        return UNSATISFIABLE
    last = size - 1 if not last_s else min(int(last_s), size - 1)
    if last < first:
        return None
    return (first, last)
