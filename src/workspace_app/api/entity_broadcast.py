"""#455 P2: the entity-write fan-out sink — compose the P9 event-trigger dispatch
with a live-sync ``FileChanged`` broadcast so every committed entity write BOTH
fires triggers AND tells peers to refetch.

Human HTTP writes, AI agent-tool writes, and workflow writes all converge on the
one ``EntityStore._emit`` seam, so wiring this single sink into all three makes an
AI edit indistinguishable from a human one to every viewer. The broadcast carries
the record's own path, so the existing file-tree refetch (on ``file_changed``,
`useAgent`) also picks up agent-created records — which previously emitted no
broadcast at all and left the tree stale.
"""

from __future__ import annotations

from ..entity.events import EntityWriteEvent, EntityWriteSink
from .events import FileChanged
from .turns import ChatTurnEngine


def build_entity_write_sink(
    dispatch: EntityWriteSink, turn_engine: ChatTurnEngine
) -> EntityWriteSink:
    """Wrap the P9 ``dispatch`` sink so it also broadcasts a ``FileChanged`` for the
    written record. Broadcast FIRST: this runs post-commit inside ``_emit``, so the
    record already landed and viewers must learn of it even if the trigger dispatch
    raises (``turn_engine.publish`` is synchronous + a no-op when nobody's watching,
    so it never blocks or fails the write)."""

    async def sink(ev: EntityWriteEvent) -> None:
        turn_engine.publish(ev.item_id, FileChanged(path=ev.path, by=ev.actor, kind="written"))
        await dispatch(ev)

    return sink
