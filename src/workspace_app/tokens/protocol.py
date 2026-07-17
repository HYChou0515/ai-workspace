"""ITokenService — the seam between a user id and that user's API token for the
external (LLM) system."""

from __future__ import annotations

import abc


class ITokenService(abc.ABC):
    """Resolve a user's API token for the external (LLM) system.

    V1 hands back the single system token for everyone (see
    :class:`~workspace_app.tokens.service.SystemTokenService`), so external
    behaviour is identical to a baked ``api_key``; a later impl fetches each
    user's personal token from an external system — swapped in by config with no
    re-plumbing.

    Resolved at the interactive agent turn, where the user is known
    (``AgentToolContext.speaker``). User-less paths (background jobs, retrieval
    sub-LLMs, embedders) keep the system token and never reach here.
    """

    @abc.abstractmethod
    async def get_token(self, user_id: str) -> str:
        """Return *user_id*'s token for the external system."""
        ...
