"""Deterministic chunk sampling for the retrieval eval (#535).

A collection has thousands of docs / tens of thousands of chunks, so the eval
runs over a SAMPLE. The sample must be reproducible (a recorded baseline has to
be re-derivable) and cheap to pick — the ``split`` job lists chunk IDs only (no
vectors / text — cf. #508) and selects here.

Selection is a stable order by ``hash(seed, id)``: same seed + same id set ⇒ the
same sample, independent of the order the IDs arrived in. No RNG (which would
break reproducibility) and no wall-clock.
"""

from __future__ import annotations

import hashlib


def _key(seed: str, item: str) -> str:
    return hashlib.sha256(f"{seed}\x00{item}".encode()).hexdigest()


def select_sample(chunk_ids: list[str], seed: str, n: int) -> list[str]:
    """Up to ``n`` chunk IDs, deterministically ordered by ``hash(seed, id)``.
    Same ``seed`` + same set of IDs ⇒ same sample, whatever order they came in."""
    return sorted(chunk_ids, key=lambda cid: _key(seed, cid))[:n]


def into_batches[T](items: list[T], batch_size: int) -> list[list[T]]:
    """Split ``items`` into consecutive batches of ``batch_size`` (the last is the
    remainder). ``batch_size`` must be positive."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]
