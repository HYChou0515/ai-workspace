"""Deterministic chat display-name helper (#357).

Shared by the KB chat list (#357) and the topic-hub multi-chat list (#132): an
unnamed chat is labelled by its first user message so the list can tell threads
apart. A leaf module (no schema/route imports) so both surfaces reuse it without
an import cycle.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class _RoleContent(Protocol):
    """A chat message, structurally — both ``Message`` and ``KbMessage`` fit."""

    role: str
    content: str


def first_user_snippet(messages: Iterable[_RoleContent], *, limit: int = 60) -> str:
    """First user message of a chat, whitespace-collapsed and truncated — the FE's
    fallback display name for an unnamed chat. "" when there's no user turn yet."""
    for m in messages:
        if m.role == "user" and m.content.strip():
            return " ".join(m.content.split())[:limit]
    return ""
