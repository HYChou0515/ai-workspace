"""Mention recording + notification for RCA workspace items (#54).

Extracted from ``create_app``: ``MentionService`` collapses the
``_record_mention`` / ``_agent_mention`` closures into one injectable service
so the routes that move out of ``create_app`` can call ``service.record`` /
``service.agent_mention`` directly instead of capturing closures.
"""

from __future__ import annotations

from specstar import SpecStar

from ..resources import Conversation, Message
from .locator import ItemLocator
from .notifications import notify
from .timeutil import now_ms


class MentionService:
    """Records `role="mention"` entries on a conversation and notifies the
    mentioned users — the human-to-human "come look" summon (NOT an agent turn),
    plus the agent-authored variant used by the `mention_user` tool."""

    def __init__(self, *, spec: SpecStar, locator: ItemLocator) -> None:
        self._spec = spec
        self._locator = locator
        self._conv_rm = spec.get_resource_manager(Conversation)

    def record(
        self,
        investigation_id: str,
        inv_title: str,
        user_ids: list[str],
        note: str,
        *,
        actor: str | None,
        author: str,
    ) -> None:
        """Append a `role="mention"` entry to the conversation (a human-to-human
        "come look", NOT an agent turn) and notify each mentioned user. `actor`
        is the summoner (a user id, or None when the agent did it)."""
        rid, conv = self._locator.conversation_for(investigation_id)
        conv.messages.append(
            Message(
                role="mention",
                content=note,
                author=author,
                mentions=list(user_ids),
                created_at=now_ms(),
            )
        )
        self._conv_rm.update(rid, conv)
        for uid in user_ids:
            if uid == actor:
                continue  # don't summon yourself
            notify(
                self._spec,
                recipient=uid,
                kind="mention",
                title=f'You were mentioned in "{inv_title}"',
                body=note,
                link=f"/a/{self._locator.slug_of(investigation_id)}/items/{investigation_id}",
                actor=actor,
            )

    def agent_mention(self, investigation_id: str, user_ids: list[str], note: str) -> None:
        """The agent's `mention_user` tool reaches this — same summon, authored
        by the agent."""
        title = self._locator.title_of(investigation_id)
        if title is None:  # pragma: no cover - the agent only mentions on a live item
            return
        self.record(investigation_id, title, user_ids, note, actor=None, author="RCA Agent")
