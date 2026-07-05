"""Entity-write events (#429 P9) — the payload the single write path emits post-commit.

Lives in the ``entity`` package (not ``workflow``) so ``EntityStore`` can emit it without
importing the workflow layer — the dependency only ever points workflow → entity. The P9
dispatcher (``workflow.event_dispatch``) consumes these to fire event-triggered runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from msgspec import Struct


class EntityOrigin(Struct):
    """Who caused an entity write, for the recursion guard (#429 P9). A human / agent / UI write
    has no origin; a triggered run's writes carry the trigger that spawned the run (so it never
    re-fires itself — guard 1) and the run's depth in the trigger chain (so an indirect cycle
    hits the global cap — guard 2)."""

    trigger: str
    depth: int


class EntityWriteEvent(Struct):
    """One entity write, emitted by ``EntityStore`` post-commit (#429 P9). ``action`` is
    ``created`` / ``updated`` (matched against a trigger's ``on``); ``fields`` is the record's
    parsed fields (matched against ``where``); ``version`` is the entity's optimistic version
    (the watermark's once-per-change key); ``origin`` is set only for a triggered run's writes.
    ``path`` is the record's own workspace file path (``/{records_path}/{number}.md``) — the
    store denormalises it so a #455 broadcast sink can raise a ``FileChanged`` without
    re-resolving the catalog (empty only for a re-synthesised backfill event, which never
    broadcasts)."""

    item_id: str
    type_name: str
    number: int
    action: str
    actor: str
    version: str
    fields: dict[str, Any]
    origin: EntityOrigin | None = None
    path: str = ""


# The sink the single write path calls after a committed create/update. Post-commit, in-request,
# on the writing pod — the P9 dispatcher is the production implementation.
EntityWriteSink = Callable[[EntityWriteEvent], Awaitable[None]]
