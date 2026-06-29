"""Chat → knowledge promotion (#54).

Best-effort extraction of an investigation/chat's dialogue into the insights KB
collection. Shared by the item-close path and the explicit promote-to-kb route, so
it lives outside ``create_app``.
"""

from __future__ import annotations

import asyncio
import logging

from ..kb.ingest import Ingestor
from ..resources import Message

logger = logging.getLogger(__name__)


async def promote_chat_to_kb(
    *,
    ingestor: Ingestor,
    insights_collection_id: str,
    actor: str,
    investigation_id: str,
    investigation_title: str,
    messages: list[Message],
) -> list[str]:
    """Run `ingestor.ingest_chat` in a thread (the LLM call is blocking).
    Swallows exceptions — chat → knowledge is best-effort, never block /close
    or surface as a hard failure to the FE. Returns the SourceDoc ids written
    (or `[]` on error / inconclusive chat). Logs failures."""
    try:
        msgs = [
            {
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name or "",
            }
            for m in messages
        ]
        return await asyncio.to_thread(
            ingestor.ingest_chat,
            collection_id=insights_collection_id,
            user=actor,
            investigation_id=investigation_id,
            investigation_title=investigation_title,
            messages=msgs,
        )
    except Exception:  # noqa: BLE001 — best-effort; don't propagate
        logger.exception("chat → knowledge promote failed for %s", investigation_id)
        return []
