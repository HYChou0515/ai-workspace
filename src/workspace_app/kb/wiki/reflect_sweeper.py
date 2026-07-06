"""WikiReflectSweeper (#479) — the daily wall-clock producer for wiki reflection.

Every tick, enqueue a ``reflect`` job for any PROSE wiki Collection (``use_wiki``
on, no ``git_url``) due for its daily consolidation. Like ``CodeRepoSweeper``
(#355) it is a pure *producer*: it does NOT survey/plan/apply itself — that runs
in the enqueued job on the wiki worker (#312 keeps heavy work off the API). The
app's lifespan task wakes it periodically and calls ``tick()``.

The schedule is a single server-local time-of-day (``reflect_daily``, ``HH:MM``)
that applies to every prose wiki collection — no per-collection knob.
``reflect_daily=None`` ⇒ the daily reflection is off and ``tick()`` is a no-op
(manual ``POST /wiki/reflect`` only). The once-a-day gate reads
``Collection.last_reflected_at`` (stamped on every run — success or failure — by
the reflect handler), so a due collection fires at most once a day even if the
reflection keeps failing. ``enqueue`` is the coordinator's ``enqueue_reflect``,
which coalesces, so a still-running reflection isn't re-queued.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from specstar import QB

from ...resources import Collection
from ..code_repo import _due_for_daily_sync

if TYPE_CHECKING:
    from specstar import SpecStar


def _last_reflected_ms(iso: str) -> int | None:
    """The ``last_reflected_at`` ISO timestamp as epoch ms (what the daily gate
    compares), or ``None`` when the wiki has never been reflected (``""``)."""
    if not iso:
        return None
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


class WikiReflectSweeper:
    """Background-loop helper: enqueue a ``reflect`` job for every prose wiki
    Collection due for its daily consolidation (#479)."""

    def __init__(
        self,
        spec: SpecStar,
        *,
        enqueue: Callable[[str], None],
        reflect_daily: str | None = None,
    ) -> None:
        self._spec = spec
        self._enqueue = enqueue
        self._reflect_daily = reflect_daily

    def tick(self, *, now_ms: int | None = None) -> list[str]:
        """Run one sweep pass. Returns the Collection ids a ``reflect`` was enqueued
        for this tick (collections not yet due, or not prose wikis, are skipped)."""
        stamp = now_ms if now_ms is not None else int(time.time() * 1000)
        enqueued: list[str] = []
        rm = self._spec.get_resource_manager(Collection)
        for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
            coll = r.data
            assert isinstance(coll, Collection)
            if not (coll.use_wiki and not coll.git_url):
                continue  # only a PROSE wiki collection is reflected
            if not _due_for_daily_sync(
                now_ms=stamp,
                last_pulled_ms=_last_reflected_ms(coll.last_reflected_at),
                daily_sync=self._reflect_daily,
            ):
                continue
            cid = r.info.resource_id  # ty: ignore[unresolved-attribute]
            self._enqueue(cid)
            enqueued.append(cid)
        return enqueued
